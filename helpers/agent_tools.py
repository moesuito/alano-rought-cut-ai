"""Constrained tool executor for the embedded editorial agent."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from helpers.agent_artifacts import (
    ARTIFACT_NAME_RE,
    REVIEW_NAME_RE,
    ArtifactError,
    ArtifactStore,
    parse_strict_json_object,
)
from helpers.knowledge_loader import KnowledgeBundle


STABLE_TOOL_ERRORS = {
    "BATCH_ABORTED",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "OUTSIDE_ALLOWED_ROOT",
    "SCHEMA_MISMATCH",
    "SEMANTIC_MISMATCH",
    "STATE_CONFLICT",
    "SIZE_LIMIT",
    "INVALID_JSON",
    "INTEGRITY_FAILURE",
    "READ_FAILED",
    "BUDGET_EXCEEDED",
    "NO_PROGRESS",
    "NEEDS_HUMAN_REVIEW",
}
ARTIFACT_READ_NAMES = {"diagnosis.json", "cut_plan.json", "edl.draft.json"}
CALL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class ToolBudget:
    max_calls: int = 24
    max_read_bytes: int = 8_000_000
    max_write_bytes: int = 2_000_000
    max_no_progress: int = 3


@dataclass(frozen=True)
class ToolCallView:
    call_id: str
    name: str
    arguments: Mapping[str, Any] | str


class AgentToolExecutor:
    """Execute exactly the tools and logical resources allowlisted for one phase."""

    def __init__(
        self,
        *,
        phase: str,
        knowledge: KnowledgeBundle,
        input_root: Path,
        input_files: Mapping[str, str],
        store: ArtifactStore,
        review_iteration: int | None = None,
        budget: ToolBudget | None = None,
        max_file_bytes: int = 4_000_000,
    ) -> None:
        phases = knowledge.manifest.get("phases")
        if not isinstance(phases, dict) or phase not in phases:
            raise ArtifactError("INVALID_ARGUMENT", "Editorial phase is not declared")
        self.phase = phase
        self.knowledge = knowledge
        self.input_root = Path(input_root)
        self.input_files = dict(input_files)
        self.store = store
        self.review_iteration = review_iteration
        self.budget = budget or ToolBudget()
        self.max_file_bytes = max_file_bytes
        self.calls = 0
        self.read_bytes = 0
        self.write_bytes = 0
        self.no_progress_count = 0
        self.successful_write = False
        self.written_artifacts: tuple[dict[str, Any], ...] = ()
        self.tool_names: list[str] = []
        self._fingerprints: set[str] = set()
        self._read_files: set[str] = set()
        self._read_artifact_revisions: dict[str, int] = {}
        self._phase_config = phases[phase]
        self._knowledge_allowlist = frozenset(knowledge.sources)
        self._input_allowlist = self._build_input_allowlist()
        self._artifact_read_allowlist = frozenset(
            name for name in self._phase_config["reads"] if name in ARTIFACT_READ_NAMES
        )
        self._write_allowlist = self._build_write_allowlist()
        _validate_root(self.input_root)
        for logical, relative in self.input_files.items():
            _validate_logical_path(logical)
            _validate_logical_path(relative)
            if logical != relative:
                raise ArtifactError("INVALID_ARGUMENT", "Input mapping must preserve logical names")

    @property
    def tools(self) -> list[dict[str, Any]]:
        readable_files = sorted(self._knowledge_allowlist | self._input_allowlist)
        definitions: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": (
                        "Read one allowlisted knowledge or immutable input file. "
                        "Choose path literally from the path enum; never invent a path."
                    ),
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path"],
                        "properties": {"path": {"type": "string", "enum": readable_files}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_artifact",
                    "description": (
                        "Write the single host-selected artifact for this phase. "
                        "Choose name literally from the name enum."
                    ),
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "expected_revision", "content"],
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": sorted(self._write_allowlist),
                            },
                            "expected_revision": {"type": "integer", "minimum": 0},
                            "content": {"type": "object"},
                        },
                    },
                },
            },
        ]
        if self._artifact_read_allowlist:
            definitions.insert(
                1,
                {
                    "type": "function",
                    "function": {
                        "name": "read_artifact",
                        "description": (
                            "Read one validated predecessor artifact. "
                            "Choose name literally from the name enum."
                        ),
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name"],
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "enum": sorted(self._artifact_read_allowlist),
                                },
                                "revision": {"type": "integer", "minimum": 1},
                            },
                        },
                    },
                },
            )
        return definitions

    @property
    def allowed_resources(self) -> dict[str, list[str]]:
        return {
            "knowledge": sorted(self._knowledge_allowlist),
            "input": sorted(self._input_allowlist),
            "artifact": sorted(self._artifact_read_allowlist),
            "write": sorted(self._write_allowlist),
        }

    def execute(self, call: ToolCallView) -> dict[str, Any]:
        """Execute a tool call and always return a stable structured response."""
        if not is_safe_call_id(call.call_id):
            return {
                "call_id": "invalid_call",
                "ok": False,
                "result": None,
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": "Tool arguments are invalid",
                },
            }
        call_id = call.call_id
        try:
            self._consume_call_budget()
            arguments = self._parse_arguments(call.arguments)
            fingerprint = _fingerprint(call.name, arguments)
            if fingerprint in self._fingerprints:
                self.no_progress_count += 1
            else:
                self._fingerprints.add(fingerprint)
            if self.no_progress_count >= self.budget.max_no_progress:
                raise ArtifactError("NO_PROGRESS", "Tool loop repeated without progress")
            if call.name == "read_file":
                result = self._read_file(arguments)
            elif call.name == "read_artifact":
                result = self._read_artifact(arguments)
            elif call.name == "write_artifact":
                result = self._write_artifact(arguments)
            else:
                raise ArtifactError("INVALID_ARGUMENT", "Tool name is not allowed")
            self.tool_names.append(call.name)
            return {"call_id": call_id, "ok": True, "result": result, "error": None}
        except ArtifactError as exc:
            code = exc.code if exc.code in STABLE_TOOL_ERRORS else "INVALID_ARGUMENT"
            return {
                "call_id": call_id,
                "ok": False,
                "result": None,
                "error": {"code": code, "message": _safe_error_message(code, exc.message)},
            }
        except Exception:
            return {
                "call_id": call_id,
                "ok": False,
                "result": None,
                "error": {"code": "INVALID_ARGUMENT", "message": "Tool call failed"},
            }

    def _build_input_allowlist(self) -> frozenset[str]:
        declared = self._phase_config["reads"]
        allowed: set[str] = set()
        for logical in self.input_files:
            if logical in declared:
                allowed.add(logical)
            elif logical.startswith("transcripts/") and "transcripts/<source>.json" in declared:
                if re.fullmatch(r"transcripts/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json", logical):
                    allowed.add(logical)
        return frozenset(allowed)

    def _build_write_allowlist(self) -> frozenset[str]:
        allowed: set[str] = set()
        for declared in self._phase_config["writes"]:
            if declared == "review.NNN.json":
                if self.review_iteration is None or not 1 <= self.review_iteration <= 999:
                    raise ArtifactError("INVALID_ARGUMENT", "Review iteration is required")
                allowed.add(f"review.{self.review_iteration:03d}.json")
            else:
                allowed.add(declared)
        if self.phase == "review":
            allowed.discard("edl.draft.json")
        return frozenset(allowed)

    def _consume_call_budget(self) -> None:
        self.calls += 1
        if self.calls > self.budget.max_calls:
            raise ArtifactError("BUDGET_EXCEEDED", "Tool call budget was exceeded")

    def _parse_arguments(self, raw: Mapping[str, Any] | str) -> dict[str, Any]:
        if isinstance(raw, str):
            return parse_strict_json_object(raw)
        if not isinstance(raw, Mapping):
            raise ArtifactError("INVALID_ARGUMENT", "Tool arguments must be an object")
        rendered = json.dumps(raw, allow_nan=False, ensure_ascii=False)
        return parse_strict_json_object(rendered)

    def _read_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"path"}:
            raise ArtifactError("INVALID_ARGUMENT", "read_file arguments are invalid")
        logical = _validate_logical_path(arguments.get("path"))
        if logical in self._knowledge_allowlist:
            root = self.knowledge.root
            relative = logical
        elif logical in self._input_allowlist:
            root = self.input_root
            relative = self.input_files[logical]
        else:
            raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "File is not allowlisted")
        path = _resolve_confined_file(root, relative)
        raw = _read_stable_file(path, self.max_file_bytes)
        try:
            content = raw.decode("utf-8")
        except UnicodeError as exc:
            raise ArtifactError("INVALID_ARGUMENT", "File is not valid UTF-8") from exc
        self.read_bytes += len(raw)
        if self.read_bytes > self.budget.max_read_bytes:
            raise ArtifactError("BUDGET_EXCEEDED", "Read byte budget was exceeded")
        if logical not in self._read_files:
            self.no_progress_count = 0
            self._read_files.add(logical)
        return {
            "path": logical,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "content": content,
        }

    def _read_artifact(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not set(arguments).issubset({"name", "revision"}) or "name" not in arguments:
            raise ArtifactError("INVALID_ARGUMENT", "read_artifact arguments are invalid")
        name = arguments["name"]
        if not isinstance(name, str) or name not in self._artifact_read_allowlist:
            raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Artifact is not allowlisted")
        revision = arguments.get("revision")
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
        ):
            raise ArtifactError("INVALID_ARGUMENT", "Artifact revision is invalid")
        record = self.store.read(name, revision)
        if record.revision != self.store.latest_revision(name):
            raise ArtifactError("STATE_CONFLICT", "Artifact revision is stale")
        if name == "cut_plan.json" and record.content["diagnosis_revision"] != self.store.latest_revision(
            "diagnosis.json"
        ):
            raise ArtifactError("STATE_CONFLICT", "Plan depends on a stale diagnosis")
        if name == "edl.draft.json":
            metadata = record.content["metadata"]
            if (
                metadata["diagnosis_revision"] != self.store.latest_revision("diagnosis.json")
                or metadata["plan_revision"] != self.store.latest_revision("cut_plan.json")
            ):
                raise ArtifactError("STATE_CONFLICT", "EDL draft has stale predecessors")
        self.read_bytes += record.byte_count
        if self.read_bytes > self.budget.max_read_bytes:
            raise ArtifactError("BUDGET_EXCEEDED", "Read byte budget was exceeded")
        previous = self._read_artifact_revisions.get(name)
        self._read_artifact_revisions[name] = record.revision
        if previous != record.revision:
            self.no_progress_count = 0
        return {
            "name": record.name,
            "revision": record.revision,
            "schema": record.schema,
            "sha256": record.sha256,
            "content": record.content,
        }

    def _write_artifact(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"name", "expected_revision", "content"}:
            raise ArtifactError("INVALID_ARGUMENT", "write_artifact arguments are invalid")
        name = arguments["name"]
        if not isinstance(name, str) or name not in self._write_allowlist:
            raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Artifact is not writable in this phase")
        if ARTIFACT_NAME_RE.fullmatch(name) is None:
            raise ArtifactError("INVALID_ARGUMENT", "Artifact name is invalid")
        expected = arguments["expected_revision"]
        content = arguments["content"]
        if not isinstance(content, dict):
            raise ArtifactError("INVALID_ARGUMENT", "Artifact content must be an object")
        estimated_write = _strict_json_size(content) + 4096
        if REVIEW_NAME_RE.fullmatch(name) and content.get("status") == "refined":
            revised = content.get("revised_edl")
            if isinstance(revised, dict):
                estimated_write += _strict_json_size(revised) + 4096
        if self.write_bytes + estimated_write > self.budget.max_write_bytes:
            raise ArtifactError("BUDGET_EXCEEDED", "Write byte budget was exceeded")
        self._require_predecessor_reads()
        if REVIEW_NAME_RE.fullmatch(name):
            draft_revision = self._read_artifact_revisions.get("edl.draft.json")
            if draft_revision is None:
                raise ArtifactError("STATE_CONFLICT", "Latest EDL draft was not read")
            result = self.store.write_review(
                name,
                content,
                expected_revision=expected,
                expected_draft_revision=draft_revision,
                iteration=self.review_iteration or 0,
            )
        else:
            result = self.store.write(name, content, expected_revision=expected)
        written = sum(record.byte_count for record in result.records)
        self.write_bytes += written
        self.successful_write = True
        self.written_artifacts = tuple(
            {
                "name": record.name,
                "revision": record.revision,
                "sha256": record.sha256,
            }
            for record in result.records
        )
        self.no_progress_count = 0
        return {
            "artifacts": [
                {
                    "name": record.name,
                    "revision": record.revision,
                    "schema": record.schema,
                    "sha256": record.sha256,
                    "state": "validated",
                }
                for record in result.records
            ]
        }

    def _require_predecessor_reads(self) -> None:
        required_files = {
            f for f in self._input_allowlist if f.startswith("transcripts/")
        }
        if self.phase == "assemble" and "edl_template.json" in self._input_allowlist:
            required_files.add("edl_template.json")
        missing_files = required_files - self._read_files
        required_artifacts = set(self._artifact_read_allowlist)
        missing_artifacts = required_artifacts - self._read_artifact_revisions.keys()
        if missing_files or missing_artifacts:
            raise ArtifactError("STATE_CONFLICT", "Required phase inputs were not read")


def is_safe_call_id(value: object) -> bool:
    """Return whether a provider tool-call ID is safe for message correlation."""
    return isinstance(value, str) and CALL_ID_RE.fullmatch(value) is not None


def batch_aborted_response(call_id: str) -> dict[str, Any]:
    """Return a correlated response for a call skipped after an earlier batch failure."""
    if not is_safe_call_id(call_id):
        raise ArtifactError("INVALID_ARGUMENT", "Tool call ID is invalid")
    return {
        "call_id": call_id,
        "ok": False,
        "result": None,
        "error": {
            "code": "BATCH_ABORTED",
            "message": _safe_error_message("BATCH_ABORTED"),
        },
    }


def _validate_logical_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 300:
        raise ArtifactError("INVALID_ARGUMENT", "Logical path is invalid")
    if "\\" in value or ":" in value or "\x00" in value:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Logical path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Logical path is invalid")
    return path.as_posix()


def _validate_root(root: Path) -> None:
    if not root.is_dir():
        raise ArtifactError("NOT_FOUND", "Input root is unavailable")
    _reject_reparse(root)


def _resolve_confined_file(root: Path, relative: str) -> Path:
    logical = _validate_logical_path(relative)
    _validate_root(root)
    candidate = root.joinpath(*PurePosixPath(logical).parts)
    current = root
    for part in PurePosixPath(logical).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse(current)
    if not candidate.is_file():
        raise ArtifactError("NOT_FOUND", "File does not exist")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "File escaped its logical root") from exc
    return candidate


def _reject_reparse(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ArtifactError("NOT_FOUND", "Runtime path is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Filesystem links are not allowed")


def _read_stable_file(path: Path, maximum: int) -> bytes:
    _reject_reparse(path)
    try:
        before = os.stat(path, follow_symlinks=False)
        if before.st_size > maximum:
            raise ArtifactError("SIZE_LIMIT", "File exceeds the configured size limit")
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
        after = os.stat(path, follow_symlinks=False)
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError("READ_FAILED", "File could not be read") from exc
    if len(raw) > maximum:
        raise ArtifactError("SIZE_LIMIT", "File exceeds the configured size limit")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ArtifactError("STATE_CONFLICT", "File changed while being read")
    _reject_reparse(path)
    return raw


def _fingerprint(name: str, arguments: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ArtifactError("INVALID_JSON", "Tool arguments are not strict JSON") from exc
    return hashlib.sha256(f"{name}\n{raw}".encode("utf-8")).hexdigest()


def _strict_json_size(value: Mapping[str, Any]) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError("INVALID_JSON", "Artifact content is not strict JSON") from exc


def _safe_error_message(code: str, detail: str = "") -> str:
    messages = {
        "BATCH_ABORTED": "Tool call was skipped after an earlier batch failure",
        "INVALID_ARGUMENT": "Tool arguments are invalid",
        "NOT_FOUND": "Requested resource was not found",
        "OUTSIDE_ALLOWED_ROOT": "Requested resource is not allowed",
        "SCHEMA_MISMATCH": "Artifact does not match its schema",
        "SEMANTIC_MISMATCH": "Artifact violates editorial invariants",
        "STATE_CONFLICT": "Artifact state changed or is incomplete",
        "SIZE_LIMIT": "Configured size limit was exceeded",
        "INVALID_JSON": "Content is not strict JSON",
        "INTEGRITY_FAILURE": "Stored resource failed integrity validation",
        "READ_FAILED": "Resource could not be read",
        "BUDGET_EXCEEDED": "Phase budget was exceeded",
        "NO_PROGRESS": "Tool loop made no progress",
        "NEEDS_HUMAN_REVIEW": "The decision requires human review",
    }
    base = messages.get(code, "Tool call failed")
    if detail and detail != base:
        return f"{base}: {detail}"
    return base
