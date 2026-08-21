"""Content-free usage telemetry and host-owned run ledger."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_PHASES = {"diagnose", "plan", "assemble", "review", "host"}
_STATES = {"running", "needs_revision", "approved", "failed", "needs_human_review"}
_OUTCOMES = {"tool_calls", "artifact_written", "retry", "approved", "failed", "needs_human_review"}
_USAGE_SOURCES = {"provider", "estimated", "unavailable"}
_TOOL_NAMES = {"read_file", "read_artifact", "write_artifact"}
_FINISH_REASONS = {"stop", "length", "tool_calls", "function_call", "content_filter"}


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    source: str = "unavailable"


@dataclass(frozen=True)
class TelemetryRecord:
    run_id: str
    call_id: str
    phase: str
    iteration: int
    model: str
    finish_reason: str | None
    latency_ms: int
    usage: CompletionUsage
    tool_names: tuple[str, ...]
    validation_status: str
    outcome: str
    error_code: str | None = None


class JsonlUsageWriter:
    """Append sanitized call metrics without prompts, transcripts, or responses."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse(self.path.parent)
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")

    def append(self, record: TelemetryRecord) -> None:
        payload = _sanitize_record(record)
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(raw) > 16_384:
            raise ValueError("telemetry record exceeds size limit")
        descriptor = _acquire_lock(self._lock_path)
        try:
            if self.path.exists() or self.path.is_symlink():
                _reject_reparse(self.path)
            output = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                written = 0
                while written < len(raw):
                    count = os.write(output, raw[written:])
                    if count <= 0:
                        raise OSError("telemetry append did not make progress")
                    written += count
                os.fsync(output)
            finally:
                os.close(output)
        finally:
            os.close(descriptor)
            self._lock_path.unlink(missing_ok=True)


class RunLedger:
    """Persist a compact host-only state machine and artifact hash chain."""

    def __init__(self, path: Path, *, run_id: str, model: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse(self.path.parent)
        self.run_id = _safe_id(run_id, "run_id")
        self.model = _safe_label(model, "model", 200)
        if not self.path.exists():
            self._write(
                {
                    "version": 1,
                    "run_id": self.run_id,
                    "model": self.model,
                    "state": "running",
                    "phase": "host",
                    "iterations": {},
                    "artifacts": [],
                    "calls": {"count": 0, "prompt_tokens": 0, "completion_tokens": 0},
                    "last_error_code": None,
                    "updated_at": _utc_now(),
                }
            )

    def record_call(self, record: TelemetryRecord) -> None:
        sanitized = _sanitize_record(record)
        state = self._read()
        calls = state["calls"]
        calls["count"] += 1
        calls["prompt_tokens"] += sanitized["usage"]["prompt_tokens"] or 0
        calls["completion_tokens"] += sanitized["usage"]["completion_tokens"] or 0
        state["phase"] = sanitized["phase"]
        state["iterations"][sanitized["phase"]] = max(
            state["iterations"].get(sanitized["phase"], 0), sanitized["iteration"]
        )
        state["last_error_code"] = sanitized["error_code"]
        state["updated_at"] = _utc_now()
        self._write(state)

    def record_artifacts(self, phase: str, records: Sequence[Mapping[str, Any]]) -> None:
        if phase not in _PHASES:
            raise ValueError("invalid ledger phase")
        state = self._read()
        for record in records:
            name = _safe_label(record.get("name"), "artifact name", 80)
            revision = _safe_non_negative(record.get("revision"), "revision")
            digest = record.get("sha256")
            if not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
                raise ValueError("invalid artifact hash")
            state["artifacts"].append(
                {"name": name, "revision": revision, "sha256": digest, "phase": phase}
            )
        state["phase"] = phase
        state["updated_at"] = _utc_now()
        self._write(state)

    def transition(self, state_name: str, *, phase: str, error_code: str | None = None) -> None:
        if state_name not in _STATES or phase not in _PHASES:
            raise ValueError("invalid run transition")
        state = self._read()
        state["state"] = state_name
        state["phase"] = phase
        state["last_error_code"] = (
            _safe_label(error_code, "error_code", 80) if error_code is not None else None
        )
        state["updated_at"] = _utc_now()
        self._write(state)

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise RuntimeError("run ledger is unavailable")
        _reject_reparse(self.path)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("run ledger is invalid") from exc
        if not isinstance(value, dict) or value.get("run_id") != self.run_id:
            raise RuntimeError("run ledger association is invalid")
        return value

    def _write(self, payload: Mapping[str, Any]) -> None:
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        _atomic_replace(self.path, raw)


def coerce_usage(value: object) -> CompletionUsage:
    """Normalize provider usage from a dataclass, object, or mapping."""
    if isinstance(value, CompletionUsage):
        prompt = value.prompt_tokens
        completion = value.completion_tokens
        total = value.total_tokens
        source = value.source
    elif isinstance(value, Mapping):
        prompt = value.get("prompt_tokens", value.get("input_tokens"))
        completion = value.get("completion_tokens", value.get("output_tokens"))
        total = value.get("total_tokens")
        source = value.get("source", "provider")
    else:
        prompt = getattr(value, "prompt_tokens", getattr(value, "input_tokens", None))
        completion = getattr(
            value, "completion_tokens", getattr(value, "output_tokens", None)
        )
        total = getattr(value, "total_tokens", None)
        source = getattr(value, "source", "provider") if value is not None else "unavailable"
    return CompletionUsage(
        prompt_tokens=_optional_non_negative(prompt),
        completion_tokens=_optional_non_negative(completion),
        total_tokens=_optional_non_negative(total),
        source=source if source in _USAGE_SOURCES else "unavailable",
    )


def _sanitize_record(record: TelemetryRecord) -> dict[str, Any]:
    usage = coerce_usage(record.usage)
    if record.phase not in _PHASES:
        raise ValueError("invalid telemetry phase")
    if record.outcome not in _OUTCOMES:
        raise ValueError("invalid telemetry outcome")
    validation = _safe_label(record.validation_status, "validation_status", 80)
    return {
        "timestamp": _utc_now(),
        "run_id": _safe_id(record.run_id, "run_id"),
        "call_id": _safe_id(record.call_id, "call_id"),
        "phase": record.phase,
        "iteration": _safe_non_negative(record.iteration, "iteration"),
        "model": _safe_label(record.model, "model", 200),
        "finish_reason": _sanitize_finish_reason(record.finish_reason),
        "latency_ms": _safe_non_negative(record.latency_ms, "latency_ms"),
        "usage": asdict(usage),
        "tool_names": [name if name in _TOOL_NAMES else "unknown" for name in record.tool_names],
        "validation_status": validation,
        "outcome": record.outcome,
        "error_code": (
            _safe_label(record.error_code, "error_code", 80)
            if record.error_code is not None
            else None
        ),
    }


def _safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _safe_label(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"invalid {field}")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"invalid {field}")
    return value


def _safe_non_negative(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"invalid {field}")
    return value


def _optional_non_negative(value: object) -> int | None:
    if value is None:
        return None
    return _safe_non_negative(value, "token count")


def _sanitize_finish_reason(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value in _FINISH_REASONS else "other"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_reparse(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("telemetry path is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or attributes & flag:
        raise RuntimeError("telemetry path cannot be a filesystem link")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("telemetry writer is busy") from exc


def _atomic_replace(path: Path, raw: bytes) -> None:
    _reject_reparse(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
