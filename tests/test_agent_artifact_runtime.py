from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from helpers.agent_artifacts import (
    ArtifactError,
    ArtifactStore,
    EvidenceCatalog,
    parse_strict_json_object,
)
from helpers.agent_telemetry import (
    CompletionUsage,
    JsonlUsageWriter,
    RunLedger,
    TelemetryRecord,
)
from helpers.agent_tools import AgentToolExecutor, ToolCallView
from helpers.artifact_agent import (
    ArtifactDrivenEditor,
    StructuredCompletionProvider,
    TransientCompletionError,
)
from helpers.editorial_edl_bridge import EditorialEdlBridge
from helpers.knowledge_loader import (
    compose_agent_knowledge,
    load_artifact_schema_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "agent_knowledge" / "examples"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _store(tmp_path: Path, *, run_id: str = "run-test-001") -> ArtifactStore:
    template = _example("edl_template.json")
    return ArtifactStore(
        tmp_path / "edit" / "agent" / "artifacts",
        run_id=run_id,
        schemas=load_artifact_schema_catalog(),
        evidence=EvidenceCatalog.from_edl_template(template),
        edl_template=template,
    )


def _write_predecessors(store: ArtifactStore) -> None:
    store.write("diagnosis.json", _example("diagnosis.json"), expected_revision=0)
    store.write("cut_plan.json", _example("cut_plan.json"), expected_revision=0)
    store.write("edl.draft.json", _example("edl.json"), expected_revision=0)


def _approved_review() -> dict[str, Any]:
    return _example("review.json")


def _refined_review() -> dict[str, Any]:
    review = _approved_review()
    review["status"] = "refined"
    review["findings"] = [
        {
            "code": "REMOVE_REDUNDANCY",
            "severity": "warning",
            "repair_scope": "edl",
            "range_id": "RANGE_002",
            "evidence": "O segundo range repete a ação final.",
            "repair": "Encurtar o segundo range.",
        }
    ]
    revised = _example("edl.json")
    revised["ranges"][1]["end"] = 28.2
    revised["ranges"][1]["quote"] = "Copie e guarde em um lugar seguro."
    revised["total_duration_s"] = 9.8
    review["revised_edl"] = revised
    return review


def test_offline_catalog_validates_cross_schema_review() -> None:
    catalog = load_artifact_schema_catalog()
    catalog.validate("schemas/diagnosis.schema.json", _example("diagnosis.json"))
    catalog.validate("schemas/edl.schema.json", _example("edl.json"))
    catalog.validate("schemas/review.schema.json", _approved_review())


def test_store_applies_host_revisions_cas_and_publishes_immutable_edl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    diagnosis = _example("diagnosis.json")
    diagnosis["revision"] = 999
    first = store.write("diagnosis.json", diagnosis, expected_revision=0).record(
        "diagnosis.json"
    )
    assert first.revision == 1
    assert first.content["revision"] == 1

    with pytest.raises(ArtifactError) as conflict:
        store.write("diagnosis.json", diagnosis, expected_revision=0)
    assert conflict.value.code == "STATE_CONFLICT"

    store.write("cut_plan.json", _example("cut_plan.json"), expected_revision=0)
    store.write("edl.draft.json", _example("edl.json"), expected_revision=0)
    store.write_review(
        "review.001.json",
        _approved_review(),
        expected_revision=0,
        expected_draft_revision=1,
        iteration=1,
    )
    final_edl = store.publish_approved_edl()
    assert final_edl.name == "edl.json"
    assert (store.root / "edl.json").is_file()
    assert store.publish_approved_edl().sha256 == final_edl.sha256

    draft = _example("edl.json")
    draft["ranges"][0]["end"] = 22.4
    draft["ranges"][0]["quote"] = "Você só vai ver a chave uma vez."
    draft["total_duration_s"] = 9.8
    with pytest.raises(ArtifactError) as immutable:
        store.write("edl.draft.json", draft, expected_revision=1)
    assert immutable.value.code == "STATE_CONFLICT"


def test_refined_review_commits_review_and_new_draft_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_predecessors(store)

    result = store.write_review(
        "review.001.json",
        _refined_review(),
        expected_revision=0,
        expected_draft_revision=1,
        iteration=1,
    )

    assert result.record("review.001.json").content["edl_revision"] == 1
    assert result.record("review.001.json").content["iteration"] == 1
    assert result.record("edl.draft.json").revision == 2
    assert store.read("edl.draft.json").content["total_duration_s"] == 9.8


def test_non_edl_repair_is_persisted_as_human_review(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_predecessors(store)
    review = _refined_review()
    review["findings"][0]["repair_scope"] = "plan"

    result = store.write_review(
        "review.001.json",
        review,
        expected_revision=0,
        expected_draft_revision=1,
        iteration=1,
    )

    persisted = result.record("review.001.json").content
    assert persisted["status"] == "needs_human_review"
    assert persisted["revised_edl"] is None
    assert store.latest_revision("edl.draft.json") == 1


def test_refined_transaction_rolls_back_when_second_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _write_predecessors(store)
    real_replace = os.replace
    revision_replaces = 0

    def fail_second_revision(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        nonlocal revision_replaces
        if ".revisions" in str(target):
            revision_replaces += 1
            if revision_replaces == 2:
                raise OSError("simulated interruption")
        real_replace(source, target)

    monkeypatch.setattr("helpers.agent_artifacts.os.replace", fail_second_revision)
    with pytest.raises(OSError):
        store.write_review(
            "review.001.json",
            _refined_review(),
            expected_revision=0,
            expected_draft_revision=1,
            iteration=1,
        )

    assert store.latest_revision("edl.draft.json") == 1
    assert store.latest_revision("review.001.json") == 0


def test_store_rejects_physical_sources_and_invalid_ranges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_predecessors(store)
    invalid = _example("edl.json")
    invalid["sources"]["C0104"] = "C:/private/video.mp4"
    with pytest.raises(ArtifactError) as physical:
        store.write("edl.draft.json", invalid, expected_revision=1)
    assert physical.value.code == "SCHEMA_MISMATCH"

    invalid = _example("edl.json")
    invalid["ranges"][0]["end"] = invalid["ranges"][0]["start"]
    with pytest.raises(ArtifactError) as interval:
        store.write("edl.draft.json", invalid, expected_revision=1)
    assert interval.value.code == "SEMANTIC_MISMATCH"


def test_transcript_evidence_enforces_word_boundaries_and_literal_quote() -> None:
    template = deepcopy(_example("edl_template.json"))
    template["sources"] = {"C0104": "source:C0104"}
    evidence = EvidenceCatalog.from_transcripts(
        template,
        {
            "C0104": {
                "words": [
                    {"text": "Olá", "start": 1.0, "end": 1.3},
                    {"text": "mundo", "start": 1.4, "end": 1.9},
                ]
            }
        },
    )

    evidence.validate_interval("C0104", 1.0, 1.9)
    evidence.validate_literal_quote("C0104", 1.0, 1.9, "Olá, mundo!")
    with pytest.raises(ArtifactError) as boundary:
        evidence.validate_interval("C0104", 1.1, 1.9)
    assert boundary.value.code == "SEMANTIC_MISMATCH"
    with pytest.raises(ArtifactError) as quote:
        evidence.validate_literal_quote("C0104", 1.0, 1.9, "Outro texto")
    assert quote.value.code == "SEMANTIC_MISMATCH"


def _evidence_with_words(words: list[dict[str, object]]) -> EvidenceCatalog:
    template = deepcopy(_example("edl_template.json"))
    template["sources"] = {"C0104": "source:C0104"}
    return EvidenceCatalog.from_transcripts(template, {"C0104": {"words": words}})


def test_transcript_evidence_accepts_47ms_canonical_overlap() -> None:
    evidence = _evidence_with_words(
        [
            {"text": "primeira", "start": 0.0, "end": 1.0},
            {"text": "segunda", "start": 0.953, "end": 1.4},
        ]
    )

    assert evidence.source_end_seconds["C0104"] == 1.4


def test_transcript_evidence_accepts_exactly_250ms_overlap() -> None:
    evidence = _evidence_with_words(
        [
            {"text": "primeira", "start": 0.0, "end": 1.0},
            {"text": "segunda", "start": 0.75, "end": 1.4},
        ]
    )

    assert len(evidence.words["C0104"]) == 2


def test_transcript_evidence_rejects_overlap_above_250ms() -> None:
    with pytest.raises(ArtifactError) as overlap:
        _evidence_with_words(
            [
                {"text": "primeira", "start": 0.0, "end": 1.0},
                {"text": "segunda", "start": 0.749, "end": 1.4},
            ]
        )

    assert overlap.value.code == "INVALID_INPUT"
    assert "overlap" in str(overlap.value)


def test_transcript_evidence_rejects_reverse_start_order() -> None:
    with pytest.raises(ArtifactError) as reverse:
        _evidence_with_words(
            [
                {"text": "primeira", "start": 1.0, "end": 1.1},
                {"text": "segunda", "start": 0.95, "end": 1.05},
            ]
        )

    assert reverse.value.code == "INVALID_INPUT"
    assert "reverse time order" in str(reverse.value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"value":NaN}',
        '[1,2,3]',
    ],
)
def test_strict_json_rejects_duplicates_non_finite_and_non_objects(raw: str) -> None:
    with pytest.raises(ArtifactError) as error:
        parse_strict_json_object(raw)
    assert error.value.code == "INVALID_JSON"


def _input_files(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    input_root = tmp_path / "inputs"
    (input_root / "transcripts").mkdir(parents=True)
    (input_root / "brief.md").write_text("Brief privado", encoding="utf-8")
    (input_root / "takes_packed.md").write_text(
        "## C0104\n[18.10-29.20] A chave aparece uma vez.", encoding="utf-8"
    )
    (input_root / "edl_template.json").write_text(
        json.dumps(_example("edl_template.json")), encoding="utf-8"
    )
    (input_root / "transcripts" / "C0104.json").write_text(
        json.dumps({"words": []}), encoding="utf-8"
    )
    return input_root, {
        "brief.md": "brief.md",
        "takes_packed.md": "takes_packed.md",
        "edl_template.json": "edl_template.json",
        "transcripts/C0104.json": "transcripts/C0104.json",
    }


def test_tools_deny_traversal_ads_and_phase_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    input_root, input_files = _input_files(tmp_path)
    bundle = compose_agent_knowledge(phase="diagnose")
    tools = AgentToolExecutor(
        phase="diagnose",
        knowledge=bundle,
        input_root=input_root,
        input_files=input_files,
        store=store,
    )

    for logical in ("../brief.md", "C:/brief.md", "brief.md:secret", "\\\\host\\share"):
        response = tools.execute(
            ToolCallView("call-safe", "read_file", {"path": logical})
        )
        assert response["ok"] is False
        assert response["error"]["code"] in {"INVALID_ARGUMENT", "OUTSIDE_ALLOWED_ROOT"}
        assert str(input_root) not in json.dumps(response)

    response = tools.execute(
        ToolCallView("call-artifact", "read_artifact", {"name": "cut_plan.json"})
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "OUTSIDE_ALLOWED_ROOT"


@pytest.mark.parametrize(
    ("phase", "review_iteration", "artifact_names", "write_name"),
    [
        ("diagnose", None, None, "diagnosis.json"),
        ("plan", None, {"diagnosis.json"}, "cut_plan.json"),
        ("assemble", None, {"diagnosis.json", "cut_plan.json"}, "edl.draft.json"),
        (
            "review",
            1,
            {"diagnosis.json", "cut_plan.json", "edl.draft.json"},
            "review.001.json",
        ),
    ],
)
def test_tool_schemas_expose_only_exact_phase_enums(
    tmp_path: Path,
    phase: str,
    review_iteration: int | None,
    artifact_names: set[str] | None,
    write_name: str,
) -> None:
    store = _store(tmp_path)
    input_root, input_files = _input_files(tmp_path)
    bundle = compose_agent_knowledge(phase=phase)
    executor = AgentToolExecutor(
        phase=phase,
        knowledge=bundle,
        input_root=input_root,
        input_files=input_files,
        store=store,
        review_iteration=review_iteration,
    )
    definitions = {
        item["function"]["name"]: item["function"] for item in executor.tools
    }

    read_file_enum = set(
        definitions["read_file"]["parameters"]["properties"]["path"]["enum"]
    )
    expected_files = set(executor.allowed_resources["knowledge"]) | set(
        executor.allowed_resources["input"]
    )
    assert read_file_enum == expected_files
    assert not {".env", "../brief.md", "C:/private/file", "agent_run.json"} & read_file_enum
    assert "literally from the path enum" in definitions["read_file"]["description"]

    if artifact_names is None:
        assert "read_artifact" not in definitions
    else:
        assert set(
            definitions["read_artifact"]["parameters"]["properties"]["name"]["enum"]
        ) == artifact_names
        assert "literally from the name enum" in definitions["read_artifact"]["description"]

    assert definitions["write_artifact"]["parameters"]["properties"]["name"][
        "enum"
    ] == [write_name]
    assert "literally from the name enum" in definitions["write_artifact"]["description"]


def test_tools_require_phase_reads_and_write_with_host_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    input_root, input_files = _input_files(tmp_path)
    tools = AgentToolExecutor(
        phase="diagnose",
        knowledge=compose_agent_knowledge(phase="diagnose"),
        input_root=input_root,
        input_files=input_files,
        store=store,
    )
    denied = tools.execute(
        ToolCallView(
            "call-write-early",
            "write_artifact",
            {"name": "diagnosis.json", "expected_revision": 0, "content": _example("diagnosis.json")},
        )
    )
    assert denied["error"]["code"] == "STATE_CONFLICT"

    for index, logical in enumerate(("brief.md", "transcripts/C0104.json"), 1):
        assert tools.execute(ToolCallView(f"read-file-{index}", "read_file", {"path": logical}))[
            "ok"
        ]
    written = tools.execute(
        ToolCallView(
            "call-write",
            "write_artifact",
            {"name": "diagnosis.json", "expected_revision": 0, "content": _example("diagnosis.json")},
        )
    )
    assert written["ok"] is True
    assert store.read("diagnosis.json").content["revision"] == 1


def test_read_file_rejects_symlink_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    input_root, input_files = _input_files(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = input_root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")
    input_files["linked.md"] = "linked.md"
    bundle = compose_agent_knowledge(phase="diagnose")
    bundle.manifest["phases"]["diagnose"]["reads"].append("linked.md")
    tools = AgentToolExecutor(
        phase="diagnose",
        knowledge=bundle,
        input_root=input_root,
        input_files=input_files,
        store=store,
    )
    response = tools.execute(ToolCallView("read-link", "read_file", {"path": "linked.md"}))
    assert response["ok"] is False
    assert response["error"]["code"] == "OUTSIDE_ALLOWED_ROOT"


def test_telemetry_and_ledger_do_not_persist_sensitive_content(tmp_path: Path) -> None:
    secret = "nvapi-secret-token"
    telemetry_path = tmp_path / "agent" / "llm_usage.jsonl"
    ledger_path = tmp_path / "agent" / "agent_run.json"
    writer = JsonlUsageWriter(telemetry_path)
    ledger = RunLedger(ledger_path, run_id="run-telemetry", model="z-ai/glm-5.2")
    record = TelemetryRecord(
        run_id="run-telemetry",
        call_id="call-001",
        phase="diagnose",
        iteration=1,
        model="z-ai/glm-5.2",
        finish_reason="tool_calls",
        latency_ms=42,
        usage=CompletionUsage(100, 20, 120, "provider"),
        tool_names=("read_file",),
        validation_status="validated",
        outcome="tool_calls",
    )
    writer.append(record)
    ledger.record_call(record)
    combined = telemetry_path.read_text(encoding="utf-8") + ledger_path.read_text(
        encoding="utf-8"
    )
    assert secret not in combined
    assert "Brief privado" not in combined
    assert "prompt_tokens" in combined


@dataclass
class _FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class _FakeCompletion:
    id: str
    model: str
    content: str | None
    tool_calls: list[_FakeToolCall]
    finish_reason: str
    usage: dict[str, int]


class _SuccessfulProvider:
    def __init__(self) -> None:
        self.systems: dict[str, str] = {}
        self.envelopes: dict[str, dict[str, Any]] = {}

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> _FakeCompletion:
        envelope = json.loads(messages[1]["content"])
        phase = envelope["phase"]
        self.systems[phase] = messages[0]["content"]
        self.envelopes[phase] = envelope
        prior_tools = [message for message in messages if message["role"] == "tool"]
        if not prior_tools:
            calls = self._reads(phase)
        else:
            calls = [self._write(phase, envelope)]
        return _FakeCompletion(
            id=f"completion-{phase}-{len(prior_tools)}",
            model="fake-local-14b",
            content=None,
            tool_calls=calls,
            finish_reason="tool_calls",
            usage={"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
        )

    def _reads(self, phase: str) -> list[_FakeToolCall]:
        file_reads = {
            "diagnose": ["brief.md", "transcripts/C0104.json"],
            "plan": ["brief.md", "transcripts/C0104.json"],
            "assemble": ["transcripts/C0104.json", "edl_template.json"],
            "review": ["brief.md", "transcripts/C0104.json"],
        }[phase]
        artifact_reads = {
            "diagnose": [],
            "plan": ["diagnosis.json"],
            "assemble": ["diagnosis.json", "cut_plan.json"],
            "review": ["diagnosis.json", "cut_plan.json", "edl.draft.json"],
        }[phase]
        calls = [
            _FakeToolCall(f"read-{phase}-{index}", "read_file", {"path": name})
            for index, name in enumerate(file_reads, 1)
        ]
        calls.extend(
            _FakeToolCall(f"artifact-{phase}-{index}", "read_artifact", {"name": name})
            for index, name in enumerate(artifact_reads, 1)
        )
        return calls

    def _write(self, phase: str, envelope: dict[str, Any]) -> _FakeToolCall:
        if phase == "diagnose":
            name, payload = "diagnosis.json", _example("diagnosis.json")
        elif phase == "plan":
            name, payload = "cut_plan.json", _example("cut_plan.json")
        elif phase == "assemble":
            name, payload = "edl.draft.json", _example("edl.json")
        else:
            name, payload = f"review.{envelope['review_iteration']:03d}.json", _approved_review()
        return _FakeToolCall(
            f"write-{phase}",
            "write_artifact",
            {"name": name, "expected_revision": 0, "content": payload},
        )


class _DiagnosisNeedsHumanProvider(_SuccessfulProvider):
    def _write(self, phase: str, envelope: dict[str, Any]) -> _FakeToolCall:
        call = super()._write(phase, envelope)
        if phase == "diagnose":
            call.arguments["content"]["status"] = "needs_human_review"
        return call


def test_artifact_agent_rebuilds_each_phase_and_requires_tool_writes(tmp_path: Path) -> None:
    store = _store(tmp_path, run_id="run-agent-001")
    input_root, input_files = _input_files(tmp_path)
    provider = _SuccessfulProvider()
    agent_root = tmp_path / "edit" / "agent"
    editor = ArtifactDrivenEditor(
        run_id="run-agent-001",
        model="fake-local-14b",
        provider=provider,
        store=store,
        input_root=input_root,
        input_files=input_files,
        telemetry=JsonlUsageWriter(agent_root / "llm_usage.jsonl"),
        ledger=RunLedger(agent_root / "agent_run.json", run_id="run-agent-001", model="fake-local-14b"),
    )

    result = editor.run(content_type_hint="reels", brief="Faça um Reels direto.")

    assert result.state == "approved"
    assert result.final_edl is not None
    assert "archetypes/social_talking_head.md" not in provider.systems["diagnose"]
    assert "archetypes/social_talking_head.md" in provider.systems["plan"]
    assert provider.envelopes["diagnose"]["content_type_hint"] == "reels"
    assert provider.envelopes["plan"]["brief"] == "Faça um Reels direto."
    assert provider.envelopes["review"]["brief"] == "Faça um Reels direto."
    assert store.latest_revision("edl.json") == 1


class _NoToolProvider:
    def complete(self, **_: Any) -> _FakeCompletion:
        return _FakeCompletion(
            id="completion-no-tool",
            model="fake-local-8b",
            content=json.dumps(_example("diagnosis.json")),
            tool_calls=[],
            finish_reason="stop",
            usage={},
        )


class _TransientThenSuccessProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.success = _SuccessfulProvider()

    def complete(self, **kwargs: Any) -> _FakeCompletion:
        self.calls += 1
        if self.calls == 1:
            raise TransientCompletionError("temporary provider failure")
        return self.success.complete(**kwargs)


class _AlwaysTransientProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_: Any) -> _FakeCompletion:
        self.calls += 1
        raise TransientCompletionError("temporary provider failure")


class _PermanentFailureProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_: Any) -> _FakeCompletion:
        self.calls += 1
        raise RuntimeError("permanent provider failure")


class _InvalidCallIdProvider:
    def complete(self, **_: Any) -> _FakeCompletion:
        return _FakeCompletion(
            id="completion-invalid-call-id",
            model="fake-local-8b",
            content=None,
            tool_calls=[
                _FakeToolCall("bad/tool/id", "read_file", {"path": "transcripts/C0104.json"})
            ],
            finish_reason="tool_calls",
            usage={},
        )


class _BatchFailureProvider:
    def __init__(self, *, failure_position: str) -> None:
        self.failure_position = failure_position
        self.calls = 0
        self.captured_messages: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _FakeCompletion:
        self.calls += 1
        if self.calls > 1:
            self.captured_messages = deepcopy(kwargs["messages"])
            raise RuntimeError("stop after capturing the correlated tool responses")
        invalid = _FakeToolCall(
            "read-denied", "read_file", {"path": "not-in-the-phase-enum.md"}
        )
        valid_reads = [
            _FakeToolCall("read-brief", "read_file", {"path": "brief.md"}),
            _FakeToolCall("read-takes", "read_file", {"path": "transcripts/C0104.json"}),
        ]
        write = _FakeToolCall(
            "write-diagnosis",
            "write_artifact",
            {
                "name": "diagnosis.json",
                "expected_revision": 0,
                "content": _example("diagnosis.json"),
            },
        )
        calls = (
            [invalid, *valid_reads, write]
            if self.failure_position == "first"
            else [*valid_reads, invalid, write]
        )
        return _FakeCompletion(
            id=f"completion-batch-{self.failure_position}",
            model="fake-local-8b",
            content=None,
            tool_calls=calls,
            finish_reason="tool_calls",
            usage={},
        )


def test_artifact_agent_never_accepts_textual_json_without_write_tool(tmp_path: Path) -> None:
    store = _store(tmp_path, run_id="run-agent-no-tool")
    input_root, input_files = _input_files(tmp_path)
    agent_root = tmp_path / "edit" / "agent"
    editor = ArtifactDrivenEditor(
        run_id="run-agent-no-tool",
        model="fake-local-8b",
        provider=_NoToolProvider(),
        store=store,
        input_root=input_root,
        input_files=input_files,
        telemetry=JsonlUsageWriter(agent_root / "llm_usage.jsonl"),
        ledger=RunLedger(agent_root / "agent_run.json", run_id="run-agent-no-tool", model="fake-local-8b"),
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "needs_human_review"
    assert result.error_code == "NO_PROGRESS"
    assert store.latest_revision("diagnosis.json") == 0
    assert json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))["state"] == "needs_human_review"


def test_artifact_agent_rejects_unsafe_call_id_before_tool_messages(tmp_path: Path) -> None:
    store = _store(tmp_path, run_id="run-agent-invalid-call-id")
    input_root, input_files = _input_files(tmp_path)
    agent_root = tmp_path / "edit" / "agent"
    editor = ArtifactDrivenEditor(
        run_id="run-agent-invalid-call-id",
        model="fake-local-8b",
        provider=_InvalidCallIdProvider(),
        store=store,
        input_root=input_root,
        input_files=input_files,
        telemetry=JsonlUsageWriter(agent_root / "llm_usage.jsonl"),
        ledger=RunLedger(
            agent_root / "agent_run.json",
            run_id="run-agent-invalid-call-id",
            model="fake-local-8b",
        ),
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "failed"
    assert result.error_code == "INVALID_ARGUMENT"
    assert store.latest_revision("diagnosis.json") == 0
    assert not (agent_root / "llm_usage.jsonl").exists()
    assert json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))["state"] == "failed"


@pytest.mark.parametrize("failure_position", ["first", "middle"])
def test_tool_batch_correlates_every_declared_call_and_aborts_after_failure(
    tmp_path: Path, failure_position: str
) -> None:
    provider = _BatchFailureProvider(failure_position=failure_position)
    editor, store, _ = _runtime_with_provider(
        tmp_path, provider, [], f"run-batch-{failure_position}"
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "failed"
    assistant = next(
        message for message in provider.captured_messages if message["role"] == "assistant"
    )
    responses = [
        message for message in provider.captured_messages if message["role"] == "tool"
    ]
    declared_ids = [call["id"] for call in assistant["tool_calls"]]
    assert len(responses) == len(declared_ids) == 4
    assert [message["tool_call_id"] for message in responses] == declared_ids
    payloads = [json.loads(message["content"]) for message in responses]
    assert [payload["call_id"] for payload in payloads] == declared_ids
    failure_index = 0 if failure_position == "first" else 2
    assert payloads[failure_index]["error"]["code"] == "OUTSIDE_ALLOWED_ROOT"
    assert all(
        payload["error"]["code"] == "BATCH_ABORTED"
        for payload in payloads[failure_index + 1 :]
    )
    assert store.latest_revision("diagnosis.json") == 0


def test_structured_provider_normalizes_llm_transient_error() -> None:
    from helpers.llm_client import LLMTransientError

    def transient_completion(**_: Any) -> Any:
        raise LLMTransientError("temporary")

    provider = StructuredCompletionProvider(
        transient_completion,
        config={"api_key": "sentinel", "base_url": "https://example.test/v1", "model": "test"},
    )

    with pytest.raises(TransientCompletionError):
        provider.complete(messages=[], tools=[], temperature=0.1, max_tokens=100)


def _runtime_with_provider(
    tmp_path: Path,
    provider: Any,
    sleep_calls: list[float],
    run_id: str,
) -> tuple[ArtifactDrivenEditor, ArtifactStore, Path]:
    store = _store(tmp_path, run_id=run_id)
    input_root, input_files = _input_files(tmp_path)
    agent_root = tmp_path / "edit" / "agent"
    editor = ArtifactDrivenEditor(
        run_id=run_id,
        model="fake-local-14b",
        provider=provider,
        store=store,
        input_root=input_root,
        input_files=input_files,
        telemetry=JsonlUsageWriter(agent_root / "llm_usage.jsonl"),
        ledger=RunLedger(agent_root / "agent_run.json", run_id=run_id, model="fake-local-14b"),
        sleep=sleep_calls.append,
    )
    return editor, store, agent_root


def test_editorial_status_still_transitions_to_needs_human_review(tmp_path: Path) -> None:
    editor, _, agent_root = _runtime_with_provider(
        tmp_path, _DiagnosisNeedsHumanProvider(), [], "run-editorial-human"
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "needs_human_review"
    assert result.error_code == "DIAGNOSIS_BLOCKED"
    ledger = json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))
    assert ledger["state"] == "needs_human_review"


@pytest.mark.parametrize(
    ("error", "expected_state", "expected_code"),
    [
        (ArtifactError("NEEDS_HUMAN_REVIEW", "editorial escalation"), "needs_human_review", "NEEDS_HUMAN_REVIEW"),
        (ArtifactError("STATE_CONFLICT", "technical state error"), "failed", "STATE_CONFLICT"),
        (RuntimeError("unexpected"), "failed", "COMPLETION_FAILED"),
    ],
)
def test_runner_classifies_explicit_human_and_technical_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_state: str,
    expected_code: str,
) -> None:
    editor, _, agent_root = _runtime_with_provider(
        tmp_path, _SuccessfulProvider(), [], f"run-classify-{expected_code.lower()}"
    )

    def fail_phase(**_: Any) -> Any:
        raise error

    monkeypatch.setattr(editor, "_run_phase", fail_phase)
    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == expected_state
    assert result.error_code == expected_code
    ledger = json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))
    assert ledger["state"] == expected_state


def test_transient_provider_retries_once_then_completes(tmp_path: Path) -> None:
    provider = _TransientThenSuccessProvider()
    sleep_calls: list[float] = []
    editor, store, agent_root = _runtime_with_provider(
        tmp_path, provider, sleep_calls, "run-transient-success"
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "approved"
    assert store.latest_revision("edl.json") == 1
    assert sleep_calls == [1.0]
    telemetry = [
        json.loads(line)
        for line in (agent_root / "llm_usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert telemetry[0]["outcome"] == "retry"
    assert telemetry[0]["validation_status"] == "provider_transient"
    assert telemetry[0]["error_code"] == "PROVIDER_TRANSIENT"


def test_transient_provider_exhausts_two_retries_then_fails(tmp_path: Path) -> None:
    provider = _AlwaysTransientProvider()
    sleep_calls: list[float] = []
    editor, store, agent_root = _runtime_with_provider(
        tmp_path, provider, sleep_calls, "run-transient-exhausted"
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "failed"
    assert result.error_code == "PROVIDER_RETRY_EXHAUSTED"
    assert provider.calls == 3
    assert sleep_calls == [1.0, 2.0]
    assert store.latest_revision("diagnosis.json") == 0
    telemetry = [
        json.loads(line)
        for line in (agent_root / "llm_usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(telemetry) == 3
    assert all(item["outcome"] == "retry" for item in telemetry)
    assert all(item["error_code"] == "PROVIDER_TRANSIENT" for item in telemetry)
    assert json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))["state"] == "failed"


def test_permanent_provider_failure_is_not_retried(tmp_path: Path) -> None:
    provider = _PermanentFailureProvider()
    sleep_calls: list[float] = []
    editor, store, agent_root = _runtime_with_provider(
        tmp_path, provider, sleep_calls, "run-permanent-failure"
    )

    result = editor.run(content_type_hint="reels", brief="Brief")

    assert result.state == "failed"
    assert result.error_code == "COMPLETION_FAILED"
    assert provider.calls == 1
    assert sleep_calls == []
    assert store.latest_revision("diagnosis.json") == 0
    telemetry = [
        json.loads(line)
        for line in (agent_root / "llm_usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(telemetry) == 1
    assert telemetry[0]["outcome"] == "failed"
    assert telemetry[0]["validation_status"] == "provider_error"
    assert json.loads((agent_root / "agent_run.json").read_text(encoding="utf-8"))["state"] == "failed"


def test_bridge_keeps_editorial_sources_opaque_and_projects_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_predecessors(store)
    store.write_review(
        "review.001.json",
        _approved_review(),
        expected_revision=0,
        expected_draft_revision=1,
        iteration=1,
    )
    media_path = tmp_path / "private" / "C0104.MP4"
    bridge = EditorialEdlBridge(
        store=store,
        edit_root=tmp_path / "edit",
        source_paths={"C0103": tmp_path / "private" / "C0103.MP4", "C0104": media_path},
    )

    editorial, technical_path = bridge.publish_and_project()

    assert editorial.content["sources"] == {
        "C0103": "source:C0103",
        "C0104": "source:C0104",
    }
    technical = json.loads(technical_path.read_text(encoding="utf-8"))
    assert technical["sources"] == {
        "C0103": str(tmp_path / "private" / "C0103.MP4"),
        "C0104": str(media_path),
    }
    assert technical["ranges"][0]["beat"] == "HOOK_RISK"
