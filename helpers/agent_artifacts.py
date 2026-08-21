"""Versioned, fail-closed storage for editorial agent artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from helpers.knowledge_loader import ArtifactSchemaCatalog, ArtifactSchemaError
from helpers.transcription_contract import DEFAULT_MAX_WORD_OVERLAP_SECONDS


ARTIFACT_NAME_RE = re.compile(
    r"^(?:diagnosis\.json|cut_plan\.json|edl\.draft\.json|edl\.json|review\.[0-9]{3}\.json)$"
)
REVIEW_NAME_RE = re.compile(r"^review\.([0-9]{3})\.json$")
ARTIFACT_SCHEMAS = {
    "diagnosis.json": "schemas/diagnosis.schema.json",
    "cut_plan.json": "schemas/cut-plan.schema.json",
    "edl.draft.json": "schemas/edl.schema.json",
    "edl.json": "schemas/edl.schema.json",
}
INDEX_VERSION = 1


class ArtifactError(RuntimeError):
    """Stable artifact failure that is safe to expose without physical paths."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata for one immutable artifact revision."""

    name: str
    revision: int
    schema: str
    sha256: str
    byte_count: int
    content: dict[str, Any]


@dataclass(frozen=True)
class WriteResult:
    """One or more revisions committed by a single logical transaction."""

    records: tuple[ArtifactRecord, ...]

    def record(self, name: str) -> ArtifactRecord:
        for item in self.records:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class EvidenceWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class EvidenceCatalog:
    """Logical source IDs and optional canonical timing boundaries."""

    source_refs: Mapping[str, str]
    word_boundaries: Mapping[str, frozenset[float]]
    source_end_seconds: Mapping[str, float]
    boundary_tolerance_seconds: float = 0.002
    words: Mapping[str, tuple[EvidenceWord, ...]] = field(default_factory=dict)

    @classmethod
    def from_source_ids(cls, source_ids: Sequence[str]) -> "EvidenceCatalog":
        refs: dict[str, str] = {}
        for source_id in source_ids:
            validated = _validate_source_id(source_id)
            refs[validated] = f"source:{validated}"
        if not refs:
            raise ArtifactError("INVALID_INPUT", "At least one logical source is required")
        return cls(refs, {}, {})

    @classmethod
    def from_edl_template(
        cls,
        template: Mapping[str, Any],
        *,
        word_boundaries: Mapping[str, Sequence[float]] | None = None,
        source_end_seconds: Mapping[str, float] | None = None,
    ) -> "EvidenceCatalog":
        raw_sources = template.get("sources")
        if not isinstance(raw_sources, dict) or not raw_sources:
            raise ArtifactError("INVALID_INPUT", "EDL template has no logical sources")
        refs: dict[str, str] = {}
        for raw_id, raw_ref in raw_sources.items():
            source_id = _validate_source_id(raw_id)
            expected = f"source:{source_id}"
            if raw_ref != expected:
                raise ArtifactError(
                    "INVALID_INPUT", "EDL template contains a non-logical source reference"
                )
            refs[source_id] = expected
        boundaries = {
            source_id: frozenset(float(value) for value in values)
            for source_id, values in (word_boundaries or {}).items()
        }
        ends = {
            source_id: float(value)
            for source_id, value in (source_end_seconds or {}).items()
        }
        return cls(refs, boundaries, ends)

    @classmethod
    def from_transcripts(
        cls,
        template: Mapping[str, Any],
        transcripts: Mapping[str, Mapping[str, Any]],
    ) -> "EvidenceCatalog":
        base = cls.from_edl_template(template)
        boundaries: dict[str, frozenset[float]] = {}
        ends: dict[str, float] = {}
        words_by_source: dict[str, tuple[EvidenceWord, ...]] = {}
        for source_id in base.source_refs:
            transcript = transcripts.get(source_id)
            if not isinstance(transcript, Mapping):
                raise ArtifactError("INVALID_INPUT", "Canonical transcript is missing")
            raw_words = transcript.get("words")
            if not isinstance(raw_words, list) or not raw_words:
                raise ArtifactError("INVALID_INPUT", "Canonical transcript has no words")
            parsed: list[EvidenceWord] = []
            for raw_word in raw_words:
                if not isinstance(raw_word, Mapping):
                    raise ArtifactError("INVALID_INPUT", "Canonical transcript word is invalid")
                text = raw_word.get("text")
                start = raw_word.get("start")
                end = raw_word.get("end")
                if not isinstance(text, str) or not text.strip():
                    raise ArtifactError("INVALID_INPUT", "Canonical transcript word is invalid")
                if not _is_finite_number(start) or not _is_finite_number(end):
                    raise ArtifactError("INVALID_INPUT", "Canonical transcript timing is invalid")
                start_value = float(start)
                end_value = float(end)
                if start_value < 0 or end_value <= start_value:
                    raise ArtifactError("INVALID_INPUT", "Canonical transcript timing is invalid")
                parsed.append(EvidenceWord(text.strip(), start_value, end_value))
            previous_start = -math.inf
            previous_end: float | None = None
            for word in parsed:
                if word.start < previous_start:
                    raise ArtifactError(
                        "INVALID_INPUT", "Canonical transcript words are in reverse time order"
                    )
                if (
                    previous_end is not None
                    and previous_end - word.start
                    > DEFAULT_MAX_WORD_OVERLAP_SECONDS + 1e-9
                ):
                    raise ArtifactError(
                        "INVALID_INPUT", "Canonical transcript word overlap exceeds the limit"
                    )
                previous_start = word.start
                previous_end = word.end
            words_by_source[source_id] = tuple(parsed)
            boundaries[source_id] = frozenset(
                value for word in parsed for value in (word.start, word.end)
            )
            ends[source_id] = max(word.end for word in parsed)
        return cls(
            source_refs=base.source_refs,
            word_boundaries=boundaries,
            source_end_seconds=ends,
            words=words_by_source,
        )

    def validate_interval(self, source: object, start: object, end: object) -> None:
        if not isinstance(source, str) or source not in self.source_refs:
            raise ArtifactError("SEMANTIC_MISMATCH", "Artifact references an unknown source")
        if not _is_finite_number(start) or not _is_finite_number(end):
            raise ArtifactError("SEMANTIC_MISMATCH", "Artifact range is not finite")
        start_value = float(start)
        end_value = float(end)
        if start_value < 0 or end_value <= start_value:
            raise ArtifactError("SEMANTIC_MISMATCH", "Artifact range is not positive")
        known_end = self.source_end_seconds.get(source)
        if known_end is not None and end_value > known_end + self.boundary_tolerance_seconds:
            raise ArtifactError("SEMANTIC_MISMATCH", "Artifact range exceeds source evidence")
        boundaries = self.word_boundaries.get(source)
        if boundaries:
            if not _matches_boundary(start_value, boundaries, self.boundary_tolerance_seconds):
                raise ArtifactError("SEMANTIC_MISMATCH", "Range start cuts inside a word")
            if not _matches_boundary(end_value, boundaries, self.boundary_tolerance_seconds):
                raise ArtifactError("SEMANTIC_MISMATCH", "Range end cuts inside a word")

    def validate_literal_quote(
        self, source: str, start: float, end: float, quote: object
    ) -> None:
        words = self.words.get(source)
        if not words:
            return
        if not isinstance(quote, str) or not quote.strip():
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL quote is empty")
        selected = [
            word.text
            for word in words
            if word.start >= start - self.boundary_tolerance_seconds
            and word.end <= end + self.boundary_tolerance_seconds
        ]
        if _lexical_tokens(" ".join(selected)) != _lexical_tokens(quote):
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL quote is not literal evidence")


class ArtifactSemanticValidator:
    """Cross-artifact editorial invariants not expressible in JSON Schema."""

    def __init__(
        self,
        evidence: EvidenceCatalog,
        edl_template: Mapping[str, Any] | None = None,
    ) -> None:
        self._evidence = evidence
        self._template = deepcopy(dict(edl_template)) if edl_template is not None else None

    def validate(
        self,
        name: str,
        payload: Mapping[str, Any],
        predecessors: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if name == "diagnosis.json":
            self._validate_diagnosis(payload)
        elif name == "cut_plan.json":
            self._validate_plan(payload, predecessors)
        elif name in {"edl.draft.json", "edl.json"}:
            self._validate_edl(payload, predecessors)
        elif REVIEW_NAME_RE.fullmatch(name):
            self._validate_review(payload, predecessors)
        else:
            raise ArtifactError("INVALID_ARGUMENT", "Artifact name is not supported")

    def _validate_evidence(self, evidence: Mapping[str, Any]) -> None:
        self._evidence.validate_interval(
            evidence.get("source"), evidence.get("start"), evidence.get("end")
        )

    def _validate_diagnosis(self, payload: Mapping[str, Any]) -> None:
        target = payload["runtime_target"]
        minimum = target.get("min_seconds")
        maximum = target.get("max_seconds")
        if minimum is not None and maximum is not None and float(minimum) > float(maximum):
            raise ArtifactError("SEMANTIC_MISMATCH", "Runtime target has inverted bounds")
        if any(item.get("blocking") is True for item in payload["uncertainties"]):
            if payload["status"] != "needs_human_review":
                raise ArtifactError(
                    "SEMANTIC_MISMATCH", "Blocking uncertainty requires human review"
                )
        for family in payload["retake_families"]:
            candidates = family["candidates"]
            winner = family["winner"]
            if winner is not None and winner >= len(candidates):
                raise ArtifactError("SEMANTIC_MISMATCH", "Retake winner is outside candidates")
            for evidence in candidates:
                self._validate_evidence(evidence)
        for field in ("production_speech", "must_keep", "must_avoid"):
            for marked in payload[field]:
                self._validate_evidence(marked["evidence"])
        beat_ids: set[str] = set()
        for beat in payload["candidate_beats"]:
            if beat["id"] in beat_ids:
                raise ArtifactError("SEMANTIC_MISMATCH", "Diagnosis has duplicate beat IDs")
            beat_ids.add(beat["id"])
            for evidence in beat["evidence"]:
                self._validate_evidence(evidence)

    def _validate_plan(
        self,
        payload: Mapping[str, Any],
        predecessors: Mapping[str, Mapping[str, Any]],
    ) -> None:
        diagnosis = predecessors.get("diagnosis.json")
        if diagnosis is None:
            raise ArtifactError("STATE_CONFLICT", "A valid diagnosis is required")
        if payload["diagnosis_revision"] != diagnosis["revision"]:
            raise ArtifactError("STATE_CONFLICT", "Plan references a stale diagnosis")
        if payload["archetype"] != diagnosis["selected_archetype"]:
            raise ArtifactError("SEMANTIC_MISMATCH", "Plan archetype differs from diagnosis")
        beat_ids: set[str] = set()
        positions: set[int] = set()
        for beat in payload["beats"]:
            if beat["id"] in beat_ids or beat["position"] in positions:
                raise ArtifactError("SEMANTIC_MISMATCH", "Plan beats are not unique")
            beat_ids.add(beat["id"])
            positions.add(beat["position"])
            for evidence in beat["candidates"]:
                self._validate_evidence(evidence)
        diagnosis_required = {
            beat["id"] for beat in diagnosis["candidate_beats"] if beat["required"]
        }
        if not diagnosis_required.issubset(beat_ids):
            raise ArtifactError("SEMANTIC_MISMATCH", "Plan omits a required diagnosis beat")
        if positions != set(range(1, len(positions) + 1)):
            raise ArtifactError("SEMANTIC_MISMATCH", "Plan positions must be contiguous")
        for exclusion in payload["global_exclusions"]:
            self._validate_evidence(exclusion)

    def _validate_edl(
        self,
        payload: Mapping[str, Any],
        predecessors: Mapping[str, Mapping[str, Any]],
    ) -> None:
        diagnosis = predecessors.get("diagnosis.json")
        plan = predecessors.get("cut_plan.json")
        if diagnosis is None or plan is None or self._template is None:
            raise ArtifactError("STATE_CONFLICT", "EDL predecessors are incomplete")
        template_metadata = self._template["metadata"]
        metadata = payload["metadata"]
        for field in (
            "timeline_name",
            "video_type",
            "content_number",
            "content_slug",
            "sequence_fps",
        ):
            if metadata[field] != template_metadata[field]:
                raise ArtifactError("SEMANTIC_MISMATCH", "EDL changed immutable template data")
        if payload["sources"] != self._template["sources"]:
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL changed logical source references")
        if metadata["diagnosis_revision"] != diagnosis["revision"]:
            raise ArtifactError("STATE_CONFLICT", "EDL references a stale diagnosis")
        if metadata["plan_revision"] != plan["revision"]:
            raise ArtifactError("STATE_CONFLICT", "EDL references a stale plan")
        plan_beats = {beat["id"]: beat for beat in plan["beats"]}
        range_ids: set[str] = set()
        covered_beats: set[str] = set()
        duration = 0.0
        for item in payload["ranges"]:
            if item["id"] in range_ids:
                raise ArtifactError("SEMANTIC_MISMATCH", "EDL has duplicate range IDs")
            if item["beat_id"] not in plan_beats:
                raise ArtifactError("SEMANTIC_MISMATCH", "EDL range references an unknown beat")
            range_ids.add(item["id"])
            covered_beats.add(item["beat_id"])
            self._evidence.validate_interval(item["source"], item["start"], item["end"])
            self._evidence.validate_literal_quote(
                item["source"], float(item["start"]), float(item["end"]), item["quote"]
            )
            duration += float(item["end"]) - float(item["start"])
        required = {beat_id for beat_id, beat in plan_beats.items() if beat["required"]}
        if not required.issubset(covered_beats):
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL omits a required beat")
        declared_required = {item["id"] for item in metadata["required_beats"]}
        if declared_required != required:
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL required beats differ from plan")
        tolerance = max(0.01, len(payload["ranges"]) * 0.001)
        if abs(duration - float(payload["total_duration_s"])) > tolerance:
            raise ArtifactError("SEMANTIC_MISMATCH", "EDL duration does not match its ranges")

    def _validate_review(
        self,
        payload: Mapping[str, Any],
        predecessors: Mapping[str, Mapping[str, Any]],
    ) -> None:
        draft = predecessors.get("edl.draft.json")
        if draft is None:
            raise ArtifactError("STATE_CONFLICT", "A valid EDL draft is required")
        status = payload["status"]
        findings = payload["findings"]
        if status == "refined":
            if any(item["repair_scope"] != "edl" for item in findings):
                raise ArtifactError(
                    "NEEDS_HUMAN_REVIEW", "A non-EDL defect cannot be repaired in the MVP"
                )
            revised = payload["revised_edl"]
            if revised == draft:
                raise ArtifactError("NO_PROGRESS", "Refined review did not change the EDL")
            self._validate_edl(revised, predecessors)
        elif payload["revised_edl"] is not None:
            raise ArtifactError("SEMANTIC_MISMATCH", "Review carries an unexpected revised EDL")
        range_ids = {item["id"] for item in draft["ranges"]}
        if any(
            finding["range_id"] is not None and finding["range_id"] not in range_ids
            for finding in findings
        ):
            raise ArtifactError("SEMANTIC_MISMATCH", "Review references an unknown range")


class ArtifactStore:
    """CAS-backed immutable revision store for one isolated agent run."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        schemas: ArtifactSchemaCatalog,
        evidence: EvidenceCatalog,
        edl_template: Mapping[str, Any] | None,
        max_artifact_bytes: int = 2_000_000,
        max_json_depth: int = 64,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
            raise ArtifactError("INVALID_ARGUMENT", "Run ID is invalid")
        self.root = Path(root)
        self.run_id = run_id
        self.schemas = schemas
        self.max_artifact_bytes = max_artifact_bytes
        self.max_json_depth = max_json_depth
        self._semantic = ArtifactSemanticValidator(evidence, edl_template)
        self._revisions_root = self.root / ".revisions"
        self._index_path = self.root / ".artifact-index.json"
        self._lock_path = self.root / ".artifact.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        self._revisions_root.mkdir(parents=True, exist_ok=True)
        _assert_no_reparse(self.root)
        _assert_no_reparse(self._revisions_root)
        if self._index_path.is_symlink():
            raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Artifact index cannot be a link")
        if not self._index_path.exists():
            _atomic_replace(
                self._index_path,
                _canonical_json_bytes(
                    {"version": INDEX_VERSION, "run_id": run_id, "artifacts": {}},
                    max_depth=self.max_json_depth,
                ),
            )
        self._load_index()

    def latest_revision(self, name: str) -> int:
        _validate_artifact_name(name)
        index = self._load_index()
        entry = index["artifacts"].get(name)
        return int(entry["latest"]) if entry is not None else 0

    def read(self, name: str, revision: int | None = None) -> ArtifactRecord:
        _validate_artifact_name(name)
        index = self._load_index()
        entry = index["artifacts"].get(name)
        if entry is None:
            raise ArtifactError("NOT_FOUND", "Artifact does not exist")
        selected = int(entry["latest"]) if revision is None else revision
        if not isinstance(selected, int) or isinstance(selected, bool) or selected < 1:
            raise ArtifactError("INVALID_ARGUMENT", "Artifact revision is invalid")
        metadata = entry["revisions"].get(str(selected))
        if metadata is None:
            raise ArtifactError("NOT_FOUND", "Artifact revision does not exist")
        relative_file = _validate_internal_relative(metadata["file"])
        artifact_path = _confined_path(self.root, relative_file, must_exist=True)
        raw = _read_stable_bytes(artifact_path, self.max_artifact_bytes)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != metadata["sha256"] or len(raw) != metadata["bytes"]:
            raise ArtifactError("INTEGRITY_FAILURE", "Artifact revision hash is invalid")
        payload = _parse_strict_json_object(raw)
        try:
            self.schemas.validate(metadata["schema"], payload)
        except ArtifactSchemaError as exc:
            raise ArtifactError("SCHEMA_MISMATCH", "Stored artifact schema is invalid") from exc
        return ArtifactRecord(
            name=name,
            revision=selected,
            schema=metadata["schema"],
            sha256=digest,
            byte_count=len(raw),
            content=payload,
        )

    def write(
        self,
        name: str,
        content: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> WriteResult:
        """Write a normal phase artifact; reviews use ``write_review``."""
        _validate_artifact_name(name)
        if REVIEW_NAME_RE.fullmatch(name) or name == "edl.json":
            raise ArtifactError("INVALID_ARGUMENT", "Artifact requires a host-only operation")
        schema = ARTIFACT_SCHEMAS.get(name)
        if schema is None:
            raise ArtifactError("INVALID_ARGUMENT", "Artifact is not writable")
        with self._exclusive_lock():
            index = self._load_index()
            if _latest_from_index(index, "edl.json"):
                raise ArtifactError("STATE_CONFLICT", "Published editorial state is immutable")
            current = _latest_from_index(index, name)
            _check_expected_revision(expected_revision, current)
            next_revision = current + 1
            payload = self._apply_authority(name, content, next_revision, index)
            predecessors = self._latest_predecessors(index)
            prepared = self._prepare(name, next_revision, schema, payload, predecessors)
            self._commit(index, (prepared,))
        return WriteResult((self.read(name, next_revision),))

    def write_review(
        self,
        name: str,
        content: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_draft_revision: int,
        iteration: int,
    ) -> WriteResult:
        """Commit a review and its refined draft as one index transaction."""
        match = REVIEW_NAME_RE.fullmatch(name)
        if match is None or int(match.group(1)) != iteration or iteration < 1:
            raise ArtifactError("INVALID_ARGUMENT", "Review name does not match host iteration")
        with self._exclusive_lock():
            index = self._load_index()
            if _latest_from_index(index, "edl.json"):
                raise ArtifactError("STATE_CONFLICT", "Published editorial state is immutable")
            current_review = _latest_from_index(index, name)
            _check_expected_revision(expected_revision, current_review)
            current_draft = _latest_from_index(index, "edl.draft.json")
            _check_expected_revision(expected_draft_revision, current_draft)
            if current_draft < 1:
                raise ArtifactError("STATE_CONFLICT", "Review requires a valid EDL draft")
            review_payload = deepcopy(dict(content))
            review_payload["iteration"] = iteration
            review_payload["edl_revision"] = current_draft
            predecessors = self._latest_predecessors(index)
            findings = review_payload.get("findings")
            if (
                review_payload.get("status") == "refined"
                and isinstance(findings, list)
                and any(
                    isinstance(item, dict) and item.get("repair_scope") != "edl"
                    for item in findings
                )
            ):
                review_payload["status"] = "needs_human_review"
                review_payload["revised_edl"] = None
            status = review_payload.get("status")
            prepared: list[_PreparedRevision] = []
            if status == "refined":
                revised = review_payload.get("revised_edl")
                if not isinstance(revised, dict):
                    raise ArtifactError("SCHEMA_MISMATCH", "Refined review requires a complete EDL")
                next_draft = current_draft + 1
                revised_payload = self._apply_authority(
                    "edl.draft.json", revised, next_draft, index
                )
                review_payload["revised_edl"] = revised_payload
                prepared.append(
                    self._prepare(
                        "edl.draft.json",
                        next_draft,
                        ARTIFACT_SCHEMAS["edl.draft.json"],
                        revised_payload,
                        predecessors,
                    )
                )
            prepared.append(
                self._prepare(
                    name,
                    current_review + 1,
                    "schemas/review.schema.json",
                    review_payload,
                    predecessors,
                )
            )
            self._commit(index, tuple(prepared))
        records = tuple(self.read(item.name, item.revision) for item in prepared)
        return WriteResult(records)

    def publish_approved_edl(self) -> ArtifactRecord:
        """Publish the latest approved draft once; the model cannot call this method."""
        with self._exclusive_lock():
            index = self._load_index()
            if _latest_from_index(index, "edl.json"):
                existing = self.read("edl.json")
                draft = self.read("edl.draft.json")
                if existing.sha256 != draft.sha256:
                    raise ArtifactError("STATE_CONFLICT", "Published EDL is immutable")
                return existing
            review = self._latest_review(index)
            if review is None or review.content["status"] != "approved":
                raise ArtifactError("STATE_CONFLICT", "An approved review is required")
            draft = self.read("edl.draft.json")
            if review.content["edl_revision"] != draft.revision:
                raise ArtifactError("STATE_CONFLICT", "Approval references a stale EDL draft")
            predecessors = self._latest_predecessors(index)
            prepared = self._prepare(
                "edl.json",
                1,
                ARTIFACT_SCHEMAS["edl.json"],
                draft.content,
                predecessors,
                direct_file="edl.json",
            )
            self._commit(index, (prepared,))
        return self.read("edl.json", 1)

    def _apply_authority(
        self,
        name: str,
        content: Mapping[str, Any],
        revision: int,
        index: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = deepcopy(dict(content))
        if name == "diagnosis.json":
            payload["revision"] = revision
        elif name == "cut_plan.json":
            payload["revision"] = revision
            payload["diagnosis_revision"] = _latest_from_index(index, "diagnosis.json")
        elif name == "edl.draft.json":
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                raise ArtifactError("SCHEMA_MISMATCH", "EDL metadata is missing")
            metadata["diagnosis_revision"] = _latest_from_index(index, "diagnosis.json")
            metadata["plan_revision"] = _latest_from_index(index, "cut_plan.json")
        return payload

    def _prepare(
        self,
        name: str,
        revision: int,
        schema: str,
        payload: Mapping[str, Any],
        predecessors: Mapping[str, Mapping[str, Any]],
        *,
        direct_file: str | None = None,
    ) -> "_PreparedRevision":
        raw = _canonical_json_bytes(payload, max_depth=self.max_json_depth)
        if len(raw) > self.max_artifact_bytes:
            raise ArtifactError("SIZE_LIMIT", "Artifact exceeds the configured size limit")
        strict_payload = _parse_strict_json_object(raw)
        try:
            self.schemas.validate(schema, strict_payload)
        except ArtifactSchemaError as exc:
            raise ArtifactError("SCHEMA_MISMATCH", "Artifact does not match its schema") from exc
        self._semantic.validate(name, strict_payload, predecessors)
        if direct_file is None:
            key = name[:-5].replace(".", "_")
            relative = f".revisions/{key}/{revision:06d}.json"
        else:
            relative = direct_file
        return _PreparedRevision(
            name=name,
            revision=revision,
            schema=schema,
            relative_file=relative,
            raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _commit(
        self,
        index: dict[str, Any],
        revisions: tuple["_PreparedRevision", ...],
    ) -> None:
        targets: list[Path] = []
        temp_files: list[Path] = []
        try:
            for prepared in revisions:
                relative = _validate_internal_relative(prepared.relative_file)
                target = _confined_path(self.root, relative, must_exist=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                _assert_no_reparse(target.parent)
                if target.exists() or target.is_symlink():
                    raise ArtifactError("STATE_CONFLICT", "Artifact revision already exists")
                temp_files.append(_stage_bytes(target.parent, prepared.raw))
                targets.append(target)
            for temp_path, target in zip(temp_files, targets):
                os.replace(temp_path, target)
            now = datetime.now(timezone.utc).isoformat()
            for prepared in revisions:
                entry = index["artifacts"].setdefault(
                    prepared.name, {"latest": 0, "revisions": {}}
                )
                entry["latest"] = prepared.revision
                entry["revisions"][str(prepared.revision)] = {
                    "schema": prepared.schema,
                    "sha256": prepared.sha256,
                    "bytes": len(prepared.raw),
                    "file": prepared.relative_file,
                    "created_at": now,
                }
            _atomic_replace(
                self._index_path,
                _canonical_json_bytes(index, max_depth=self.max_json_depth),
            )
        except Exception:
            for temp_path in temp_files:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            for target in targets:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _latest_predecessors(
        self, index: Mapping[str, Any]
    ) -> dict[str, Mapping[str, Any]]:
        predecessors: dict[str, Mapping[str, Any]] = {}
        for name in ("diagnosis.json", "cut_plan.json", "edl.draft.json"):
            if _latest_from_index(index, name):
                predecessors[name] = self.read(name).content
        return predecessors

    def _latest_review(self, index: Mapping[str, Any]) -> ArtifactRecord | None:
        candidates: list[tuple[int, str]] = []
        for name in index["artifacts"]:
            match = REVIEW_NAME_RE.fullmatch(name)
            if match:
                candidates.append((int(match.group(1)), name))
        if not candidates:
            return None
        return self.read(max(candidates)[1])

    def _load_index(self) -> dict[str, Any]:
        _assert_no_reparse(self.root)
        raw = _read_stable_bytes(self._index_path, self.max_artifact_bytes)
        index = _parse_strict_json_object(raw)
        if (
            set(index) != {"version", "run_id", "artifacts"}
            or
            index.get("version") != INDEX_VERSION
            or index.get("run_id") != self.run_id
            or not isinstance(index.get("artifacts"), dict)
        ):
            raise ArtifactError("INTEGRITY_FAILURE", "Artifact index is invalid")
        for name, entry in index["artifacts"].items():
            _validate_artifact_name(name)
            if not isinstance(entry, dict) or set(entry) != {"latest", "revisions"}:
                raise ArtifactError("INTEGRITY_FAILURE", "Artifact index is invalid")
            latest = entry["latest"]
            revisions = entry["revisions"]
            if (
                not isinstance(latest, int)
                or isinstance(latest, bool)
                or latest < 1
                or not isinstance(revisions, dict)
                or set(revisions) != {str(value) for value in range(1, latest + 1)}
            ):
                raise ArtifactError("INTEGRITY_FAILURE", "Artifact index is invalid")
            expected_schema = (
                "schemas/review.schema.json"
                if REVIEW_NAME_RE.fullmatch(name)
                else ARTIFACT_SCHEMAS.get(name)
            )
            for raw_revision, metadata in revisions.items():
                revision = int(raw_revision)
                if not isinstance(metadata, dict) or set(metadata) != {
                    "schema",
                    "sha256",
                    "bytes",
                    "file",
                    "created_at",
                }:
                    raise ArtifactError("INTEGRITY_FAILURE", "Artifact index is invalid")
                key = name[:-5].replace(".", "_")
                expected_file = (
                    "edl.json"
                    if name == "edl.json"
                    else f".revisions/{key}/{revision:06d}.json"
                )
                if (
                    metadata["schema"] != expected_schema
                    or not isinstance(metadata["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is None
                    or not isinstance(metadata["bytes"], int)
                    or isinstance(metadata["bytes"], bool)
                    or metadata["bytes"] < 1
                    or metadata["bytes"] > self.max_artifact_bytes
                    or metadata["file"] != expected_file
                    or not isinstance(metadata["created_at"], str)
                ):
                    raise ArtifactError("INTEGRITY_FAILURE", "Artifact index is invalid")
        return index

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        _assert_no_reparse(self.root)
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise ArtifactError("STATE_CONFLICT", "Artifact store is busy") from exc
        try:
            os.write(descriptor, self.run_id.encode("ascii"))
            os.close(descriptor)
            descriptor = -1
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass(frozen=True)
class _PreparedRevision:
    name: str
    revision: int
    schema: str
    relative_file: str
    raw: bytes
    sha256: str


def _validate_artifact_name(name: object) -> str:
    if not isinstance(name, str) or ARTIFACT_NAME_RE.fullmatch(name) is None:
        raise ArtifactError("INVALID_ARGUMENT", "Artifact name is not allowed")
    if ":" in name or "/" in name or "\\" in name:
        raise ArtifactError("INVALID_ARGUMENT", "Artifact name is not allowed")
    return name


def _validate_source_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ArtifactError("INVALID_INPUT", "Source ID is invalid")
    if ":" in value or value in {".", ".."}:
        raise ArtifactError("INVALID_INPUT", "Source ID is invalid")
    return value


def _validate_internal_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ArtifactError("INTEGRITY_FAILURE", "Internal artifact path is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArtifactError("INTEGRITY_FAILURE", "Internal artifact path is invalid")
    return relative.as_posix()


def _confined_path(root: Path, relative: str, *, must_exist: bool) -> Path:
    _validate_internal_relative(relative)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    _assert_no_reparse(root)
    if must_exist and not candidate.is_file():
        raise ArtifactError("NOT_FOUND", "Artifact revision does not exist")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_reparse(current)
    try:
        candidate.resolve(strict=must_exist).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Artifact path escaped its root") from exc
    return candidate


def _assert_no_reparse(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ArtifactError("NOT_FOUND", "Required runtime path is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Filesystem links are not allowed")


def _read_stable_bytes(path: Path, maximum: int) -> bytes:
    _assert_no_reparse(path)
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
    _assert_no_reparse(path)
    return raw


def _parse_strict_json_object(raw: bytes | str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArtifactError("INVALID_JSON", "Content is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactError("INVALID_JSON", "Artifact JSON must be an object")
    return value


def parse_strict_json_object(raw: bytes | str) -> dict[str, Any]:
    """Public strict JSON parser used at the model/tool boundary."""
    return _parse_strict_json_object(raw)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _canonical_json_bytes(value: object, *, max_depth: int) -> bytes:
    _validate_json_value(value, max_depth=max_depth)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError("INVALID_JSON", "Content cannot be encoded as strict JSON") from exc
    return (rendered + "\n").encode("utf-8")


def _validate_json_value(value: object, *, max_depth: int, depth: int = 0) -> None:
    if depth > max_depth:
        raise ArtifactError("SIZE_LIMIT", "JSON nesting exceeds the configured limit")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactError("INVALID_JSON", "JSON contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, max_depth=max_depth, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ArtifactError("INVALID_JSON", "JSON object keys must be strings")
            _validate_json_value(item, max_depth=max_depth, depth=depth + 1)
        return
    raise ArtifactError("INVALID_JSON", "JSON contains an unsupported value")


def _stage_bytes(parent: Path, raw: bytes) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=parent)
    path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _atomic_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse(path.parent)
    temporary = _stage_bytes(path.parent, raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _latest_from_index(index: Mapping[str, Any], name: str) -> int:
    entry = index["artifacts"].get(name)
    return int(entry["latest"]) if entry is not None else 0


def _check_expected_revision(expected: object, current: int) -> None:
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise ArtifactError("INVALID_ARGUMENT", "Expected revision is invalid")
    if expected != current:
        raise ArtifactError("STATE_CONFLICT", "Expected revision is stale")


def _matches_boundary(value: float, boundaries: frozenset[float], tolerance: float) -> bool:
    return any(abs(value - boundary) <= tolerance for boundary in boundaries)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _lexical_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in re.findall(r"[^\W_]+", value, flags=re.UNICODE))
