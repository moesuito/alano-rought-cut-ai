"""Artifact-driven four-phase editorial agent runtime."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence

from helpers.agent_artifacts import ArtifactError, ArtifactRecord, ArtifactStore
from helpers.agent_telemetry import (
    JsonlUsageWriter,
    RunLedger,
    TelemetryRecord,
    coerce_usage,
)
from helpers.agent_tools import (
    AgentToolExecutor,
    ToolBudget,
    ToolCallView,
    batch_aborted_response,
    is_safe_call_id,
)
from helpers.knowledge_loader import compose_agent_knowledge

if TYPE_CHECKING:
    from helpers.llm_client import ChatCompletionResult, ToolCall


class CompletionProvider(Protocol):
    """Injectable adapter around an OpenAI-compatible completion client."""

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> "ChatCompletionResult": ...


class TransientCompletionError(RuntimeError):
    """Retryable provider failure normalized by the completion adapter."""


class ModelResponseError(RuntimeError):
    """Model produced malformed or invalid response structure recoverable by retrying."""


class StructuredCompletionProvider:
    """Adapt ``send_chat_completion_structured`` without coupling the runner to config."""

    def __init__(
        self,
        completion: Callable[..., "ChatCompletionResult"],
        *,
        config: Mapping[str, str],
        timeout_seconds: float = 1200,
    ) -> None:
        self._completion = completion
        self._config = dict(config)
        self._timeout_seconds = timeout_seconds

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None = None,
    ) -> "ChatCompletionResult":
        from helpers.llm_client import LLMTransientError, LLMResponseError

        try:
            return self._completion(
                messages=messages,
                config=self._config,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=self._timeout_seconds,
                tools=tools,
                tool_choice="auto",
            )
        except LLMTransientError as exc:
            raise TransientCompletionError("provider request failed transiently") from exc
        except LLMResponseError as exc:
            raise ModelResponseError(str(exc)) from exc


@dataclass(frozen=True)
class AgentBudget:
    max_completion_calls_per_phase: int = 12
    max_review_iterations: int = 4
    max_empty_completions: int = 2
    max_tokens_per_completion: int | None = None
    max_provider_retries_per_phase: int = 2
    tool_budget: ToolBudget = ToolBudget()


@dataclass(frozen=True)
class ArtifactAgentResult:
    state: str
    last_phase: str
    review_iterations: int
    final_edl: ArtifactRecord | None
    error_code: str | None


@dataclass(frozen=True)
class _PhaseResult:
    artifact: ArtifactRecord
    calls: int


class ArtifactDrivenEditor:
    """Run each phase from durable artifacts instead of an inherited chat history."""

    def __init__(
        self,
        *,
        run_id: str,
        model: str,
        provider: CompletionProvider,
        store: ArtifactStore,
        input_root: Path,
        input_files: Mapping[str, str],
        telemetry: JsonlUsageWriter,
        ledger: RunLedger,
        knowledge_dir: str | os.PathLike[str] | None = None,
        budget: AgentBudget | None = None,
        temperature: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if store.run_id != run_id:
            raise ArtifactError("STATE_CONFLICT", "Agent run does not own the artifact store")
        self.run_id = run_id
        self.model = model
        self.provider = provider
        self.store = store
        self.input_root = input_root
        self.input_files = dict(input_files)
        self.telemetry = telemetry
        self.ledger = ledger
        self.knowledge_dir = knowledge_dir
        self.budget = budget or AgentBudget()
        self.temperature = temperature
        self._sleep = sleep

    def run(self, *, content_type_hint: str, brief: str) -> ArtifactAgentResult:
        """Execute diagnose, plan, assemble, and review with fail-closed transitions."""
        try:
            diagnosis_phase = self._run_phase(
                phase="diagnose",
                archetype=None,
                phase_iteration=1,
                content_type_hint=content_type_hint,
                brief=brief,
            )
            diagnosis = diagnosis_phase.artifact.content
            if diagnosis["status"] != "ready":
                return self._needs_human("diagnose", "DIAGNOSIS_BLOCKED", 0)

            selected_archetype = diagnosis["selected_archetype"]
            plan_phase = self._run_phase(
                phase="plan",
                archetype=selected_archetype,
                phase_iteration=1,
                content_type_hint=content_type_hint,
                brief=brief,
            )
            if plan_phase.artifact.content["status"] != "ready":
                return self._needs_human("plan", "PLAN_BLOCKED", 0)

            self._run_phase(
                phase="assemble",
                archetype=selected_archetype,
                phase_iteration=1,
                content_type_hint=content_type_hint,
                brief="",
            )

            for iteration in range(1, self.budget.max_review_iterations + 1):
                review_phase = self._run_phase(
                    phase="review",
                    archetype=selected_archetype,
                    phase_iteration=iteration,
                    content_type_hint=content_type_hint,
                    brief=brief,
                    review_iteration=iteration,
                )
                status = review_phase.artifact.content["status"]
                if status == "approved":
                    final_edl = self.store.publish_approved_edl()
                    self.ledger.record_artifacts(
                        "host",
                        [
                            {
                                "name": final_edl.name,
                                "revision": final_edl.revision,
                                "sha256": final_edl.sha256,
                            }
                        ],
                    )
                    self.ledger.transition("approved", phase="host")
                    return ArtifactAgentResult(
                        state="approved",
                        last_phase="review",
                        review_iterations=iteration,
                        final_edl=final_edl,
                        error_code=None,
                    )
                if status == "needs_human_review":
                    return self._needs_human("review", "REVIEW_BLOCKED", iteration)
                if status != "refined":
                    return self._failed("review", "INVALID_REVIEW_STATE", iteration)

            return self._needs_human(
                "review", "REVIEW_LOOP_LIMIT", self.budget.max_review_iterations
            )
        except ArtifactError as exc:
            human_codes = {"NEEDS_HUMAN_REVIEW", "NO_PROGRESS", "BUDGET_EXCEEDED"}
            terminal = self._needs_human if exc.code in human_codes else self._failed
            return terminal(self._safe_current_phase(), exc.code, self._current_review_iteration())
        except Exception:
            return self._failed(
                self._safe_current_phase(), "COMPLETION_FAILED", self._current_review_iteration()
            )

    def _run_phase(
        self,
        *,
        phase: str,
        archetype: str | None,
        phase_iteration: int,
        content_type_hint: str,
        brief: str,
        review_iteration: int | None = None,
    ) -> _PhaseResult:
        self._active_phase = phase
        self._active_review_iteration = review_iteration or 0
        bundle = compose_agent_knowledge(
            phase=phase,
            archetype=archetype,
            knowledge_dir=self.knowledge_dir,
        )
        executor = AgentToolExecutor(
            phase=phase,
            knowledge=bundle,
            input_root=self.input_root,
            input_files=self.input_files,
            store=self.store,
            review_iteration=review_iteration,
            budget=self.budget.tool_budget,
        )
        messages = self._phase_messages(
            phase=phase,
            bundle_content=bundle.content,
            resources=executor.allowed_resources,
            content_type_hint=content_type_hint,
            brief=brief if phase in {"diagnose", "plan", "review"} else "",
            review_iteration=review_iteration,
        )
        empty_completions = 0
        provider_retries = 0
        provider_retry_limit = min(
            max(self.budget.max_provider_retries_per_phase, 0), 2
        )
        for call_number in range(1, self.budget.max_completion_calls_per_phase + 1):
            provider_attempt = 0
            while True:
                provider_attempt += 1
                started = time.perf_counter()
                try:
                    completion = self.provider.complete(
                        messages=messages,
                        tools=executor.tools,
                        temperature=self.temperature,
                        max_tokens=self.budget.max_tokens_per_completion,
                    )
                    break
                except TransientCompletionError as exc:
                    self._record_completion(
                        phase=phase,
                        iteration=phase_iteration,
                        call_number=call_number,
                        provider_attempt=provider_attempt,
                        completion=None,
                        latency_ms=_elapsed_ms(started),
                        tool_names=(),
                        outcome="retry",
                        validation_status="provider_transient",
                        error_code="PROVIDER_TRANSIENT",
                    )
                    if provider_retries >= provider_retry_limit:
                        raise ArtifactError(
                            "PROVIDER_RETRY_EXHAUSTED",
                            "Transient provider retry budget was exhausted",
                        ) from exc
                    delay = float(provider_retries + 1)
                    provider_retries += 1
                    self._sleep(delay)
                except ModelResponseError as exc:
                    self._record_completion(
                        phase=phase,
                        iteration=phase_iteration,
                        call_number=call_number,
                        provider_attempt=provider_attempt,
                        completion=None,
                        latency_ms=_elapsed_ms(started),
                        tool_names=(),
                        outcome="retry",
                        validation_status="schema_mismatch",
                        error_code="SCHEMA_MISMATCH",
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"A chamada de ferramenta falhou com erro de formatação ({exc}). "
                                "Grave o artefato via write_artifact com JSON válido e todos os campos obrigatórios."
                            ),
                        }
                    )
                    completion = None
                    break
                except Exception as exc:
                    self._record_completion(
                        phase=phase,
                        iteration=phase_iteration,
                        call_number=call_number,
                        provider_attempt=provider_attempt,
                        completion=None,
                        latency_ms=_elapsed_ms(started),
                        tool_names=(),
                        outcome="failed",
                        validation_status="provider_error",
                        error_code="COMPLETION_FAILED",
                    )
                    raise ArtifactError("COMPLETION_FAILED", "Completion provider failed") from exc

            if completion is None:
                continue

            tool_calls = _completion_tool_calls(completion)
            if not tool_calls:
                empty_completions += 1
                self._record_completion(
                    phase=phase,
                    iteration=phase_iteration,
                    call_number=call_number,
                    provider_attempt=provider_attempt,
                    completion=completion,
                    latency_ms=_elapsed_ms(started),
                    tool_names=(),
                    outcome="retry",
                    validation_status="missing_tool_call",
                    error_code="NO_PROGRESS",
                )
                if empty_completions >= self.budget.max_empty_completions:
                    raise ArtifactError("NO_PROGRESS", "Model did not use write_artifact")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "A fase só termina por tool call. Leia as entradas obrigatórias e "
                            "grave o artefato com write_artifact; texto livre não é aceito."
                        ),
                    }
                )
                continue

            empty_completions = 0
            _validate_tool_batch(tool_calls)
            messages.append(_assistant_tool_message(completion, tool_calls))
            names: list[str] = []
            error_code: str | None = None
            for tool_call in tool_calls:
                names.append(tool_call.name)
                response = (
                    executor.execute(tool_call)
                    if error_code is None
                    else batch_aborted_response(tool_call.call_id)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": response["call_id"],
                        "content": json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    }
                )
                if not response["ok"] and error_code is None:
                    error_code = response["error"]["code"]
            outcome = "artifact_written" if executor.successful_write else "tool_calls"
            self._record_completion(
                phase=phase,
                iteration=phase_iteration,
                call_number=call_number,
                provider_attempt=provider_attempt,
                completion=completion,
                latency_ms=_elapsed_ms(started),
                tool_names=tuple(names),
                outcome=outcome,
                validation_status="validated" if executor.successful_write else "pending",
                error_code=error_code,
            )
            if error_code in {"BUDGET_EXCEEDED", "NO_PROGRESS", "NEEDS_HUMAN_REVIEW"}:
                raise ArtifactError(error_code, "Phase cannot continue")
            if error_code is not None:
                _prune_failed_writes(messages)
                continue
            if executor.successful_write:
                artifact_name = next(iter(executor.allowed_resources["write"]))
                artifact = self.store.read(artifact_name)
                self.ledger.record_artifacts(phase, executor.written_artifacts)
                return _PhaseResult(artifact=artifact, calls=call_number)

        raise ArtifactError("BUDGET_EXCEEDED", "Completion call budget was exceeded")

    def _phase_messages(
        self,
        *,
        phase: str,
        bundle_content: str,
        resources: Mapping[str, Sequence[str]],
        content_type_hint: str,
        brief: str,
        review_iteration: int | None,
    ) -> list[dict[str, Any]]:
        envelope: dict[str, Any] = {
            "phase": phase,
            "content_type_hint": content_type_hint if phase == "diagnose" else None,
            "brief": brief if phase in {"diagnose", "plan", "review"} else None,
            "review_iteration": review_iteration,
            "resources": resources,
            "rules": [
                "Use somente as tools declaradas.",
                "Leia todas as entradas obrigatórias antes de escrever.",
                "A fase só termina após write_artifact validado.",
                "Não use paths físicos nem texto livre como saída da fase.",
            ],
        }
        return [
            {"role": "system", "content": bundle_content},
            {
                "role": "user",
                "content": json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            },
        ]

    def _record_completion(
        self,
        *,
        phase: str,
        iteration: int,
        call_number: int,
        provider_attempt: int,
        completion: object | None,
        latency_ms: int,
        tool_names: tuple[str, ...],
        outcome: str,
        validation_status: str,
        error_code: str | None,
    ) -> None:
        record = TelemetryRecord(
            run_id=self.run_id,
            call_id=f"{phase}-{iteration}-{call_number}-{provider_attempt}",
            phase=phase,
            iteration=iteration,
            model=self.model,
            finish_reason=_completion_field(completion, "finish_reason"),
            latency_ms=latency_ms,
            usage=coerce_usage(_completion_value(completion, "usage")),
            tool_names=tool_names,
            validation_status=validation_status,
            outcome=outcome,
            error_code=error_code,
        )
        self.telemetry.append(record)
        self.ledger.record_call(record)

    def _needs_human(
        self, phase: str, error_code: str, review_iterations: int
    ) -> ArtifactAgentResult:
        safe_phase = phase if phase in {"diagnose", "plan", "assemble", "review"} else "host"
        try:
            self.ledger.transition(
                "needs_human_review", phase=safe_phase, error_code=error_code
            )
        except Exception:
            pass
        return ArtifactAgentResult(
            state="needs_human_review",
            last_phase=safe_phase,
            review_iterations=review_iterations,
            final_edl=None,
            error_code=error_code,
        )

    def _failed(
        self, phase: str, error_code: str, review_iterations: int
    ) -> ArtifactAgentResult:
        """Persist and return a terminal technical failure without human-review semantics."""
        safe_phase = phase if phase in {"diagnose", "plan", "assemble", "review"} else "host"
        try:
            self.ledger.transition("failed", phase=safe_phase, error_code=error_code)
        except Exception:
            pass
        return ArtifactAgentResult(
            state="failed",
            last_phase=safe_phase,
            review_iterations=review_iterations,
            final_edl=None,
            error_code=error_code,
        )

    def _safe_current_phase(self) -> str:
        return getattr(self, "_active_phase", "host")

    def _current_review_iteration(self) -> int:
        return int(getattr(self, "_active_review_iteration", 0))


def _completion_tool_calls(completion: object) -> tuple[ToolCallView, ...]:
    raw_calls = _completion_value(completion, "tool_calls")
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
        raise ArtifactError("INVALID_ARGUMENT", "Completion tool_calls are invalid")
    calls: list[ToolCallView] = []
    for raw in raw_calls:
        call_id = _object_value(raw, "call_id") or _object_value(raw, "id")
        name = _object_value(raw, "name")
        arguments = _object_value(raw, "arguments")
        if not isinstance(call_id, str) or not is_safe_call_id(call_id) or not isinstance(name, str):
            raise ArtifactError("INVALID_ARGUMENT", "Completion tool call is invalid")
        if not isinstance(arguments, (str, Mapping)):
            raise ArtifactError("INVALID_ARGUMENT", "Completion tool arguments are invalid")
        calls.append(ToolCallView(call_id=call_id, name=name, arguments=arguments))
    return tuple(calls)


def _validate_tool_batch(calls: Sequence[ToolCallView]) -> None:
    allowed = {"read_file", "read_artifact", "write_artifact"}
    if any(call.name not in allowed for call in calls):
        raise ArtifactError("INVALID_ARGUMENT", "Completion requested an unknown tool")
    write_positions = [index for index, call in enumerate(calls) if call.name == "write_artifact"]
    if len(write_positions) > 1 or (write_positions and write_positions[0] != len(calls) - 1):
        raise ArtifactError("INVALID_ARGUMENT", "write_artifact must be the final tool call")


def _assistant_tool_message(
    completion: object, calls: Sequence[ToolCallView]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": _completion_field(completion, "content"),
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": (
                        call.arguments
                        if isinstance(call.arguments, str)
                        else json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
                    ),
                },
            }
            for call in calls
        ],
    }


def _completion_value(completion: object | None, field: str) -> object | None:
    if completion is None:
        return None
    return _object_value(completion, field)


def _completion_field(completion: object | None, field: str) -> str | None:
    value = _completion_value(completion, field)
    return value if isinstance(value, str) else None


def _object_value(value: object, field: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _prune_failed_writes(messages: list[dict[str, Any]]) -> None:
    """Keep all read operations, but keep only the most recent failed write attempt."""
    write_assistant_indices: list[int] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, Sequence):
                for tc in tool_calls:
                    func = tc.get("function", {}) if isinstance(tc, Mapping) else getattr(tc, "function", {})
                    name = func.get("name") if isinstance(func, Mapping) else getattr(func, "name", None)
                    if name == "write_artifact":
                        write_assistant_indices.append(idx)
                        break

    if len(write_assistant_indices) > 1:
        indices_to_remove: set[int] = set()
        for old_idx in write_assistant_indices[:-1]:
            indices_to_remove.add(old_idx)
            # Remove associated tool response messages immediately following
            next_idx = old_idx + 1
            while next_idx < len(messages) and messages[next_idx].get("role") == "tool":
                indices_to_remove.add(next_idx)
                next_idx += 1

        pruned = [msg for idx, msg in enumerate(messages) if idx not in indices_to_remove]
        messages.clear()
        messages.extend(pruned)

