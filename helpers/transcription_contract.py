"""Canonical, dependency-free transcript contract for the local WhisperX stack.

This module intentionally does not import WhisperX, faster-whisper, Pyannote,
Torch, or Pandas.  Runtime adapters hand their plain-Python results to
``convert_whisperx_result`` and the rest of Alano Cut consumes the resulting
schema.  Keeping this boundary dependency-free also makes cache validation and
failure handling available before the GPU runtime is loaded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


TRANSCRIPT_SCHEMA_VERSION = 1
TRANSCRIPTION_PROVIDER = "whisperx_faster_whisper"
DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
DEFAULT_PORTUGUESE_ALIGN_MODEL = (
    "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"
)
DEFAULT_PORTUGUESE_INITIAL_PROMPT: str | None = None
DEFAULT_PORTUGUESE_HOTWORDS: str | None = None
DEFAULT_PRIMARY_MODEL_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
DEFAULT_SEMANTIC_VERIFIER_MODEL = "small"
DEFAULT_SEMANTIC_FUSION_REVISION = "windowed-consensus-v1"
DEFAULT_ACOUSTIC_SNAP_REVISION = "interword-residual-v1"
DEFAULT_SEMANTIC_VERIFIER_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
DEFAULT_ALIGN_MODEL_REVISION = "634ac655299bcdc46c83bc01da9bab52d2987e4f"
DEFAULT_DIARIZATION_MODEL_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
DEFAULT_RECORDING_CUES = "corta,cortar,volta,refaz,regrava,gravando"
EXPECTED_RNNOISE_MODEL_HASH = (
    "F1357C4E5BE9DEE8467BEAD486DFCED2D75B640C26AD0B594FA7F102322371D9"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SPEAKER_RE = re.compile(r"^speaker(?:[_ -]?)(\d+)$", re.IGNORECASE)
_LEXICAL_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def _normalized_cue_tokens(value: object) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _LEXICAL_WORD_RE.finditer(str(value)))


class TranscriptContractError(ValueError):
    """Raised when input or cached data violates the canonical contract."""


@dataclass(frozen=True, slots=True)
class WhisperXConfig:
    """All output-affecting parameters used to key a local transcript cache.

    Package versions and model revisions are optional because an installer may
    not know them until the runtime is created.  Production callers should fill
    them whenever possible so an upgrade cannot silently reuse an older cache.
    Authentication tokens are deliberately absent and must never be serialized.
    """

    model: str = "large-v3"
    model_revision: str | None = DEFAULT_PRIMARY_MODEL_REVISION
    semantic_verifier_model: str = DEFAULT_SEMANTIC_VERIFIER_MODEL
    semantic_verifier_revision: str | None = DEFAULT_SEMANTIC_VERIFIER_REVISION
    semantic_fusion_mode: str = "guarded_union"
    semantic_fusion_revision: str = DEFAULT_SEMANTIC_FUSION_REVISION
    acoustic_snap_revision: str = DEFAULT_ACOUSTIC_SNAP_REVISION
    recording_cues: str = DEFAULT_RECORDING_CUES
    language: str | None = "pt"
    device: str = "cuda"
    compute_type: str = "float16"
    batch_size: int = 2
    beam_size: int = 5
    initial_prompt: str | None = DEFAULT_PORTUGUESE_INITIAL_PROMPT
    hotwords: str | None = DEFAULT_PORTUGUESE_HOTWORDS
    vad_method: str = "pyannote"
    align_model: str | None = DEFAULT_PORTUGUESE_ALIGN_MODEL
    align_model_revision: str | None = DEFAULT_ALIGN_MODEL_REVISION
    diarization_model: str = DEFAULT_DIARIZATION_MODEL
    diarization_model_revision: str | None = DEFAULT_DIARIZATION_MODEL_REVISION
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    whisperx_version: str | None = "3.8.6"
    faster_whisper_version: str | None = "1.2.1"
    pyannote_audio_version: str | None = "4.0.7"

    def __post_init__(self) -> None:
        for name in (
            "model",
            "semantic_verifier_model",
            "semantic_fusion_mode",
            "semantic_fusion_revision",
            "acoustic_snap_revision",
            "recording_cues",
            "device",
            "compute_type",
            "vad_method",
            "diarization_model",
        ):
            if not str(getattr(self, name)).strip():
                raise TranscriptContractError(f"config.{name} must not be empty")
        if self.device != "cuda":
            raise TranscriptContractError(
                "config.device must be 'cuda'; CPU fallback is disabled"
            )
        if self.vad_method != "pyannote":
            raise TranscriptContractError(
                "config.vad_method must be 'pyannote'"
            )
        if self.diarization_model != DEFAULT_DIARIZATION_MODEL:
            raise TranscriptContractError(
                f"config.diarization_model must be {DEFAULT_DIARIZATION_MODEL}"
            )
        if self.batch_size < 1:
            raise TranscriptContractError("config.batch_size must be positive")
        if not _normalized_cue_tokens(self.recording_cues):
            raise TranscriptContractError("config.recording_cues must contain words")
        if self.beam_size < 1:
            raise TranscriptContractError("config.beam_size must be positive")
        for name in ("num_speakers", "min_speakers", "max_speakers"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise TranscriptContractError(f"config.{name} must be positive")
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise TranscriptContractError(
                "config.min_speakers must not exceed config.max_speakers"
            )
        if self.num_speakers is not None and (
            self.min_speakers is not None or self.max_speakers is not None
        ):
            raise TranscriptContractError(
                "config.num_speakers cannot be combined with min/max_speakers"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration with a stable key set."""

        return asdict(self)

    @property
    def sha256(self) -> str:
        return config_hash(self)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise TranscriptContractError(f"value is not canonical JSON: {exc}") from exc
    return encoded.encode("utf-8")


def config_hash(config: WhisperXConfig | Mapping[str, Any]) -> str:
    """Return the deterministic SHA-256 of a runtime configuration."""

    value = config.to_dict() if isinstance(config, WhisperXConfig) else dict(config)
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Stream a file into SHA-256 without loading the media into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_speaker_id(value: Any) -> str | None:
    """Normalize Pyannote/WhisperX speaker labels to ``speaker_N``."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise TranscriptContractError("boolean is not a valid speaker identifier")
    if isinstance(value, int):
        if value < 0:
            raise TranscriptContractError("speaker identifier must be non-negative")
        return f"speaker_{value}"
    text = str(value).strip()
    match = _SPEAKER_RE.fullmatch(text)
    if not match:
        raise TranscriptContractError(f"unsupported speaker identifier: {value!r}")
    return f"speaker_{int(match.group(1))}"


def _finite_number(value: Any, field: str, *, non_negative: bool = True) -> float:
    if isinstance(value, bool):
        raise TranscriptContractError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise TranscriptContractError(f"{field} must be a finite number")
    if non_negative and number < 0.0:
        raise TranscriptContractError(f"{field} must be non-negative")
    return number


def _validate_interval(start: Any, end: Any, field: str) -> tuple[float, float]:
    start_value = _finite_number(start, f"{field}.start")
    end_value = _finite_number(end, f"{field}.end")
    if end_value <= start_value:
        raise TranscriptContractError(f"{field} must have a strictly positive duration")
    return start_value, end_value


def _records(value: Any, field: str) -> list[Mapping[str, Any]]:
    """Accept records or a Pandas-like object without importing Pandas."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        raise TranscriptContractError(f"{field} must be a sequence of records")
    if hasattr(value, "to_dict"):
        try:
            value = value.to_dict(orient="records")
        except TypeError:
            value = value.to_dict("records")
    if isinstance(value, (str, bytes)):
        raise TranscriptContractError(f"{field} must be a sequence of records")
    try:
        records = list(value)
    except TypeError as exc:
        raise TranscriptContractError(f"{field} must be a sequence of records") from exc
    if any(not isinstance(record, Mapping) for record in records):
        raise TranscriptContractError(f"{field} contains a non-record value")
    return records


def _convert_diarization(turns: Any) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    previous_start = -math.inf
    for index, turn in enumerate(_records(turns, "diarization")):
        start, end = _validate_interval(
            turn.get("start"), turn.get("end"), f"diarization[{index}]"
        )
        if start < previous_start:
            raise TranscriptContractError("diarization turns are in reverse time order")
        speaker = normalize_speaker_id(
            turn.get("speaker", turn.get("speaker_id", turn.get("label")))
        )
        if speaker is None:
            raise TranscriptContractError(f"diarization[{index}] has no speaker")
        converted.append({"start": start, "end": end, "speaker_id": speaker})
        previous_start = start
    return converted


def _speaker_for_interval(
    start: float,
    end: float,
    diarization: Iterable[Mapping[str, Any]],
    *,
    max_gap_seconds: float = 0.25,
) -> str | None:
    midpoint = (start + end) / 2.0
    candidates: list[tuple[float, float, int, str]] = []
    for index, turn in enumerate(diarization):
        turn_start = float(turn["start"])
        turn_end = float(turn["end"])
        overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
        turn_midpoint = (turn_start + turn_end) / 2.0
        if overlap > 0.0:
            candidates.append(
                (overlap, -abs(midpoint - turn_midpoint), -index, str(turn["speaker_id"]))
            )
    if not candidates:
        gap_candidates: list[tuple[float, str]] = []
        for turn in diarization:
            turn_start = float(turn["start"])
            turn_end = float(turn["end"])
            gap = max(turn_start - end, start - turn_end, 0.0)
            if gap <= max_gap_seconds:
                gap_candidates.append((gap, str(turn["speaker_id"])))
        if not gap_candidates:
            return None
        minimum_gap = min(item[0] for item in gap_candidates)
        nearest_speakers = {
            speaker
            for gap, speaker in gap_candidates
            if abs(gap - minimum_gap) <= 1e-9
        }
        return next(iter(nearest_speakers)) if len(nearest_speakers) == 1 else None
    return max(candidates)[3]


def _validate_word_order(
    words: list[Mapping[str, Any]],
    *,
    max_overlap_seconds: float,
    field: str,
) -> None:
    previous_start = -math.inf
    previous_end: float | None = None
    for index, word in enumerate(words):
        start, end = _validate_interval(word.get("start"), word.get("end"), f"{field}[{index}]")
        if start < previous_start:
            raise TranscriptContractError(f"{field} is in reverse time order at index {index}")
        if previous_end is not None and previous_end - start > max_overlap_seconds + 1e-9:
            raise TranscriptContractError(
                f"{field} overlap exceeds {max_overlap_seconds:.3f}s at index {index}"
            )
        previous_start = start
        previous_end = end


def _resolve_source_hash(
    source_path: str | os.PathLike[str] | None,
    source_sha256: str | None,
) -> str:
    supplied = source_sha256.lower() if source_sha256 is not None else None
    if supplied is not None and not _SHA256_RE.fullmatch(supplied):
        raise TranscriptContractError("source_sha256 must be 64 lowercase hexadecimal characters")
    calculated = sha256_file(source_path) if source_path is not None else None
    if supplied is not None and calculated is not None and supplied != calculated:
        raise TranscriptContractError("source_sha256 does not match source_path")
    resolved = supplied or calculated
    if resolved is None:
        raise TranscriptContractError("source_path or source_sha256 is required")
    return resolved


def convert_whisperx_result(
    result: Mapping[str, Any],
    diarization_turns: Any,
    *,
    config: WhisperXConfig,
    source_path: str | os.PathLike[str] | None = None,
    source_sha256: str | None = None,
    max_overlap_seconds: float = 0.25,
) -> dict[str, Any]:
    """Convert an aligned WhisperX result into Alano Cut transcript schema v1.

    Every input word must carry forced-alignment timestamps.  Missing timing is
    a fatal contract error; words are never silently dropped or approximated.
    A word-level WhisperX speaker label wins, otherwise the label is selected by
    maximum overlap with the supplied diarization turns.
    """

    if not isinstance(result, Mapping):
        raise TranscriptContractError("WhisperX result must be a mapping")
    if not math.isfinite(max_overlap_seconds) or max_overlap_seconds < 0.0:
        raise TranscriptContractError("max_overlap_seconds must be finite and non-negative")

    resolved_source_hash = _resolve_source_hash(source_path, source_sha256)
    diarization = _convert_diarization(diarization_turns)
    if not diarization:
        raise TranscriptContractError("Community-1 returned no diarization turns")
    input_segments = result.get("segments")
    if not isinstance(input_segments, list):
        raise TranscriptContractError("WhisperX result.segments must be a list")

    canonical_words: list[dict[str, Any]] = []
    canonical_segments: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(input_segments):
        if not isinstance(segment, Mapping):
            raise TranscriptContractError(f"segments[{segment_index}] must be a record")
        input_words = segment.get("words")
        if not isinstance(input_words, list):
            raise TranscriptContractError(f"segments[{segment_index}].words must be a list")
        if not input_words and str(segment.get("text") or "").strip():
            raise TranscriptContractError(
                f"segments[{segment_index}] contains text without aligned words"
            )

        segment_words: list[dict[str, Any]] = []
        for word_index, word in enumerate(input_words):
            if not isinstance(word, Mapping):
                raise TranscriptContractError(
                    f"segments[{segment_index}].words[{word_index}] must be a record"
                )
            text = str(word.get("word", word.get("text", ""))).strip()
            if not text:
                raise TranscriptContractError(
                    f"segments[{segment_index}].words[{word_index}] has empty text"
                )
            start, end = _validate_interval(
                word.get("start"),
                word.get("end"),
                f"segments[{segment_index}].words[{word_index}]",
            )
            score_raw = word.get("score")
            score = None
            if score_raw is not None:
                score = _finite_number(
                    score_raw,
                    f"segments[{segment_index}].words[{word_index}].score",
                    non_negative=False,
                )
                if not 0.0 <= score <= 1.0:
                    raise TranscriptContractError("word alignment score must be between 0 and 1")
            explicit_speaker = normalize_speaker_id(
                word.get("speaker", word.get("speaker_id"))
            )
            overlap_speaker = _speaker_for_interval(
                start, end, diarization, max_gap_seconds=0.0
            )
            speaker = explicit_speaker or overlap_speaker or _speaker_for_interval(
                start, end, diarization
            )
            if speaker is None:
                raise TranscriptContractError(
                    f"segments[{segment_index}].words[{word_index}] has no diarized speaker"
                )
            timing_source = str(word.get("timing_source") or "forced_alignment")
            if timing_source not in {"forced_alignment", "forced_alignment_acoustic"}:
                raise TranscriptContractError(
                    f"segments[{segment_index}].words[{word_index}] has invalid timing_source"
                )
            canonical_word = {
                "text": text,
                "start": start,
                "end": end,
                "type": "word",
                "speaker_id": speaker,
                "speaker_assignment": (
                    "word_label"
                    if explicit_speaker is not None
                    else "turn_overlap"
                    if overlap_speaker is not None
                    else "bounded_gap"
                ),
                "score": score,
                "timing_source": timing_source,
            }
            if word.get("forced_alignment_start") is not None:
                canonical_word["forced_alignment_start"] = _finite_number(
                    word["forced_alignment_start"],
                    f"segments[{segment_index}].words[{word_index}].forced_alignment_start",
                )
            if word.get("forced_alignment_end") is not None:
                canonical_word["forced_alignment_end"] = _finite_number(
                    word["forced_alignment_end"],
                    f"segments[{segment_index}].words[{word_index}].forced_alignment_end",
                )
            segment_words.append(canonical_word)
            canonical_words.append(canonical_word.copy())

        _validate_word_order(
            segment_words,
            max_overlap_seconds=max_overlap_seconds,
            field=f"segments[{segment_index}].words",
        )
        if segment_words:
            start = segment_words[0]["start"]
            end = max(float(word["end"]) for word in segment_words)
        else:
            start, end = _validate_interval(
                segment.get("start"), segment.get("end"), f"segments[{segment_index}]"
            )
        speaker_ids = list(
            dict.fromkeys(
                word["speaker_id"]
                for word in segment_words
                if word["speaker_id"] is not None
            )
        )
        segment_text = str(segment.get("text") or "").strip()
        if not segment_text:
            segment_text = " ".join(word["text"] for word in segment_words)
        canonical_segments.append(
            {
                "id": segment_index,
                "start": start,
                "end": end,
                "text": segment_text,
                "speaker_id": speaker_ids[0] if len(speaker_ids) == 1 else None,
                "words": [word.copy() for word in segment_words],
            }
        )

    if not canonical_words:
        raise TranscriptContractError("WhisperX result contains no aligned words")
    _validate_word_order(
        canonical_words,
        max_overlap_seconds=max_overlap_seconds,
        field="words",
    )

    language_code = str(result.get("language") or config.language or "").strip()
    if not language_code:
        raise TranscriptContractError("WhisperX result has no language")
    transcript_text = str(result.get("text") or "").strip()
    if not transcript_text:
        transcript_text = " ".join(
            segment["text"] for segment in canonical_segments if segment["text"]
        )

    transcript: dict[str, Any] = {
        "text": transcript_text,
        "language_code": language_code,
        "words": canonical_words,
        "segments": canonical_segments,
        "diarization": diarization,
        "_alano_cut": {
            "schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "transcription_provider": TRANSCRIPTION_PROVIDER,
            "source_sha256": resolved_source_hash,
            "config": config.to_dict(),
            "config_sha256": config.sha256,
            "word_count": len(canonical_words),
            "timed_word_count": len(canonical_words),
            "timed_word_coverage": 1.0,
        },
    }
    validate_transcript(transcript, max_overlap_seconds=max_overlap_seconds)
    return transcript


def validate_transcript(
    transcript: Mapping[str, Any],
    *,
    max_overlap_seconds: float = 0.25,
) -> None:
    """Validate schema v1, including normative 100% forced-aligned coverage."""

    if not isinstance(transcript, Mapping):
        raise TranscriptContractError("transcript must be a mapping")
    metadata = transcript.get("_alano_cut")
    if not isinstance(metadata, Mapping):
        raise TranscriptContractError("transcript._alano_cut must be a record")
    if metadata.get("schema_version") != TRANSCRIPT_SCHEMA_VERSION:
        raise TranscriptContractError("unsupported transcript schema_version")
    if metadata.get("transcription_provider") != TRANSCRIPTION_PROVIDER:
        raise TranscriptContractError("unexpected transcription provider")
    source_hash = metadata.get("source_sha256")
    if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
        raise TranscriptContractError("invalid transcript source_sha256")
    config_value = metadata.get("config")
    if not isinstance(config_value, Mapping):
        raise TranscriptContractError("transcript config must be a record")
    if metadata.get("config_sha256") != config_hash(config_value):
        raise TranscriptContractError("transcript config hash mismatch")

    words = transcript.get("words")
    if not isinstance(words, list) or not words:
        raise TranscriptContractError("transcript words must be a non-empty list")
    for index, word in enumerate(words):
        if not isinstance(word, Mapping):
            raise TranscriptContractError(f"words[{index}] must be a record")
        if not str(word.get("text") or "").strip():
            raise TranscriptContractError(f"words[{index}] has empty text")
        if word.get("type") != "word":
            raise TranscriptContractError(f"words[{index}].type must be 'word'")
        if word.get("timing_source") not in {
            "forced_alignment",
            "forced_alignment_acoustic",
        }:
            raise TranscriptContractError(f"words[{index}] is not forced-aligned")
        if word.get("timing_source") == "forced_alignment_acoustic":
            forced_start, forced_end = _validate_interval(
                word.get("forced_alignment_start"),
                word.get("forced_alignment_end"),
                f"words[{index}].forced_alignment",
            )
            if forced_end <= forced_start:
                raise TranscriptContractError(f"words[{index}] has invalid forced evidence")
        speaker = word.get("speaker_id")
        if speaker is None:
            raise TranscriptContractError(f"words[{index}] has no diarized speaker")
        if normalize_speaker_id(speaker) != speaker:
            raise TranscriptContractError(f"words[{index}] speaker_id is not canonical")
        if word.get("speaker_assignment") not in {
            "word_label",
            "turn_overlap",
            "bounded_gap",
        }:
            raise TranscriptContractError(f"words[{index}] speaker_assignment is invalid")
        score = word.get("score")
        if score is not None:
            score_value = _finite_number(score, f"words[{index}].score", non_negative=False)
            if not 0.0 <= score_value <= 1.0:
                raise TranscriptContractError(f"words[{index}].score must be between 0 and 1")
    _validate_word_order(words, max_overlap_seconds=max_overlap_seconds, field="words")

    word_count = len(words)
    if metadata.get("word_count") != word_count:
        raise TranscriptContractError("transcript word_count mismatch")
    if metadata.get("timed_word_count") != word_count:
        raise TranscriptContractError("timed word coverage is not 100%")
    if metadata.get("timed_word_coverage") != 1.0:
        raise TranscriptContractError("timed_word_coverage must be exactly 1.0")

    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise TranscriptContractError("transcript segments must be a list")
    flattened: list[Mapping[str, Any]] = []
    previous_segment_start = -math.inf
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise TranscriptContractError(f"segments[{index}] must be a record")
        start, _ = _validate_interval(
            segment.get("start"), segment.get("end"), f"segments[{index}]"
        )
        if start < previous_segment_start:
            raise TranscriptContractError("segments are in reverse time order")
        nested = segment.get("words")
        if not isinstance(nested, list):
            raise TranscriptContractError(f"segments[{index}].words must be a list")
        flattened.extend(nested)
        previous_segment_start = start
    if flattened != words:
        raise TranscriptContractError("segment words do not match flattened transcript words")

    diarization = transcript.get("diarization")
    if not isinstance(diarization, list) or not diarization:
        raise TranscriptContractError("transcript diarization must be a non-empty list")
    _convert_diarization(diarization)


def analyze_alignment_quality(
    transcript: Mapping[str, Any],
    *,
    max_word_duration_seconds: float = 2.0,
    max_seconds_per_character: float = 0.50,
    low_score_threshold: float = 0.05,
) -> dict[str, Any]:
    """Detect CTC blank-dwell stretches and weak alignment evidence.

    WhisperX's CTC backtrack can assign intervening blank frames to the final
    token of a word.  A long word interval is therefore not accepted merely
    because it has a high mean CTC score.
    """
    validate_transcript(transcript)
    outliers: list[dict[str, Any]] = []
    low_scores: list[dict[str, Any]] = []
    for index, word in enumerate(transcript["words"]):
        duration = float(word["end"]) - float(word["start"])
        word_text = str(word["text"])
        lexical_chars = len(re.sub(r"[^\w]", "", word_text, flags=re.UNICODE))
        seconds_per_character = duration / max(1, lexical_chars)
        acoustic_supported = word.get("timing_source") == "forced_alignment_acoustic"
        reasons: list[str] = []
        if duration > max_word_duration_seconds and not acoustic_supported:
            reasons.append("word_duration_exceeds_limit")
        # Numeric notation compresses several spoken words (for example,
        # "4.1" -> "quatro ponto um"), so character density is not a valid
        # blank-dwell signal for tokens containing digits. The absolute word
        # duration limit still applies.
        if (
            duration > 1.0
            and not acoustic_supported
            and not any(character.isdigit() for character in word_text)
            and seconds_per_character > max_seconds_per_character
        ):
            reasons.append("seconds_per_character_exceeds_limit")
        if reasons:
            outliers.append(
                {
                    "word_index": index,
                    "text": word["text"],
                    "start": word["start"],
                    "end": word["end"],
                    "duration_seconds": round(duration, 6),
                    "seconds_per_character": round(seconds_per_character, 6),
                    "reasons": reasons,
                }
            )
        score = word.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score < low_score_threshold:
            low_scores.append(
                {"word_index": index, "text": word["text"], "score": float(score)}
            )
    return {
        "status": "review" if outliers else "pass",
        "blocking_outlier_count": len(outliers),
        "outliers": outliers,
        "low_score_warning_count": len(low_scores),
        "low_score_warnings": low_scores,
        "thresholds": {
            "max_word_duration_seconds": max_word_duration_seconds,
            "max_seconds_per_character": max_seconds_per_character,
            "low_score_threshold": low_score_threshold,
        },
    }


def validate_normative_transcript(
    transcript: Mapping[str, Any], *, max_overlap_seconds: float = 0.25
) -> None:
    """Validate the full local CUDA/model binding required by the workflow."""
    validate_transcript(transcript, max_overlap_seconds=max_overlap_seconds)
    metadata = transcript["_alano_cut"]
    config = metadata["config"]
    models = metadata.get("models")
    model_revisions = metadata.get("model_revisions")
    runtime = metadata.get("runtime")
    alignment = metadata.get("alignment")
    diarization_status = metadata.get("diarization_status")
    semantic_verification = metadata.get("semantic_verification")
    acoustic_timing = metadata.get("acoustic_timing")
    if not isinstance(models, Mapping):
        raise TranscriptContractError("transcript models binding is missing")
    if models.get("asr") != config.get("model"):
        raise TranscriptContractError("ASR model binding does not match config")
    if models.get("semantic_verifier") != config.get("semantic_verifier_model"):
        raise TranscriptContractError("semantic verifier model binding does not match config")
    if models.get("alignment") != config.get("align_model"):
        raise TranscriptContractError("alignment model binding does not match config")
    if models.get("diarization") != DEFAULT_DIARIZATION_MODEL:
        raise TranscriptContractError("Community-1 model binding is missing")
    expected_revisions = {
        "asr": config.get("model_revision"),
        "semantic_verifier": config.get("semantic_verifier_revision"),
        "alignment": config.get("align_model_revision"),
        "diarization": config.get("diarization_model_revision"),
    }
    if not isinstance(model_revisions, Mapping) or dict(model_revisions) != expected_revisions:
        raise TranscriptContractError("model revision binding does not match config")
    if not isinstance(runtime, Mapping):
        raise TranscriptContractError("transcript CUDA runtime binding is missing")
    expected_versions = {
        "whisperx": config.get("whisperx_version"),
        "faster_whisper": config.get("faster_whisper_version"),
        "pyannote_audio": config.get("pyannote_audio_version"),
    }
    for key, expected in expected_versions.items():
        if expected is None or runtime.get(key) != expected:
            raise TranscriptContractError(f"runtime {key} version does not match config")
    torch_version = str(runtime.get("torch") or "")
    if runtime.get("device") != "cuda" or "+cpu" in torch_version or "+cu" not in torch_version:
        raise TranscriptContractError("transcript was not produced by CUDA PyTorch")
    if (
        runtime.get("compute_type") != config.get("compute_type")
        or runtime.get("batch_size") != config.get("batch_size")
    ):
        raise TranscriptContractError("effective inference config does not match cache config")
    if not runtime.get("cuda") or not runtime.get("gpu"):
        raise TranscriptContractError("CUDA/GPU runtime identity is missing")
    if not isinstance(alignment, Mapping) or (
        alignment.get("model") != models.get("alignment")
        or alignment.get("timed_word_coverage") != 1.0
    ):
        raise TranscriptContractError("forced-alignment runtime binding is invalid")
    computed_alignment_quality = analyze_alignment_quality(transcript)
    stored_alignment_quality = {
        key: alignment.get(key) for key in computed_alignment_quality
    }
    if stored_alignment_quality != computed_alignment_quality:
        raise TranscriptContractError(
            "forced-alignment quality report does not match transcript"
        )
    if computed_alignment_quality["status"] != "pass":
        raise TranscriptContractError("forced-alignment quality requires review")
    if not isinstance(diarization_status, Mapping) or (
        diarization_status.get("status") != "pass"
        or diarization_status.get("model") != DEFAULT_DIARIZATION_MODEL
        or diarization_status.get("exclusive") is not True
        or diarization_status.get("turn_count") != len(transcript["diarization"])
    ):
        raise TranscriptContractError("Community-1 exclusive diarization binding is invalid")
    if not isinstance(semantic_verification, Mapping) or (
        semantic_verification.get("status") != "pass"
        or semantic_verification.get("mode") != config.get("semantic_fusion_mode")
        or semantic_verification.get("revision")
        != config.get("semantic_fusion_revision")
        or semantic_verification.get("asr_mode") != config.get("vad_method")
        or semantic_verification.get("coverage_asr_mode") != "windowed_no_vad"
        or semantic_verification.get("semantic_source") != "semantic_verifier"
        or not isinstance(semantic_verification.get("contextual_token_count"), int)
        or semantic_verification.get("contextual_token_count", 0) < 1
        or not isinstance(semantic_verification.get("coverage_token_count"), int)
        or semantic_verification.get("coverage_token_count", 0) < 1
        or semantic_verification.get("cue_words")
        != sorted(_normalized_cue_tokens(config.get("recording_cues")))
        or not isinstance(semantic_verification.get("recoveries"), list)
    ):
        raise TranscriptContractError("semantic cue verification binding is invalid")
    if not isinstance(acoustic_timing, Mapping) or (
        acoustic_timing.get("status") != "pass"
        or acoustic_timing.get("blocking_outlier_count") != 0
        or acoustic_timing.get("revision") != config.get("acoustic_snap_revision")
        or acoustic_timing.get("source_sha256") != metadata.get("source_sha256")
        or acoustic_timing.get("rnnoise_model_sha256") != EXPECTED_RNNOISE_MODEL_HASH
        or not isinstance(acoustic_timing.get("parameters"), Mapping)
        or not isinstance(acoustic_timing.get("evidence"), list)
        or not isinstance(acoustic_timing.get("semantic_recovery_evidence"), list)
    ):
        raise TranscriptContractError("acoustic timing validation binding is invalid")


def validate_provisional_normative_transcript(
    transcript: Mapping[str, Any],
    *,
    max_overlap_seconds: float = 0.25,
) -> list[dict[str, Any]]:
    """Validate a transcript whose only open issue is scopeable activity.

    Step 02 necessarily runs before the editorial EDL exists.  A fully bound
    CUDA/WhisperX transcript may therefore remain provisionally usable when
    its only blockers are well-formed, unattributed acoustic components.  The
    selected-interval gate must still resolve every such blocker before XML.

    Returns the blockers retained for later interval audit.  A globally
    normative transcript returns an empty list.
    """
    try:
        validate_normative_transcript(
            transcript,
            max_overlap_seconds=max_overlap_seconds,
        )
        return []
    except TranscriptContractError as original_error:
        metadata = transcript.get("_alano_cut")
        acoustic_timing = (
            metadata.get("acoustic_timing")
            if isinstance(metadata, Mapping)
            else None
        )
        if not isinstance(acoustic_timing, Mapping) or acoustic_timing.get("status") != "review":
            raise original_error

    blockers = acoustic_timing.get("blocking_outliers")
    if (
        not isinstance(blockers, list)
        or acoustic_timing.get("blocking_outlier_count") != len(blockers)
        or not blockers
    ):
        raise TranscriptContractError("acoustic blocker list is invalid")

    audited: list[dict[str, Any]] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping) or blocker.get("type") != "unattributed_bilateral_activity":
            raise TranscriptContractError("non-scopeable acoustic blocker requires review")
        component = blocker.get("component")
        if not isinstance(component, Mapping):
            raise TranscriptContractError("acoustic blocker component is invalid")
        try:
            blocker_start = float(component["start"])
            blocker_end = float(component["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TranscriptContractError("acoustic blocker interval is invalid") from exc
        if (
            not math.isfinite(blocker_start)
            or not math.isfinite(blocker_end)
            or blocker_start < 0.0
            or blocker_end <= blocker_start
        ):
            raise TranscriptContractError("acoustic blocker interval is invalid")
        audited.append(dict(blocker))

    # Prove that the original strict failure is exclusively the auditable
    # acoustic status.  Every other schema/model/runtime/alignment field still
    # passes the complete normative validator.
    scoped = copy.deepcopy(dict(transcript))
    scoped_acoustic = scoped["_alano_cut"]["acoustic_timing"]
    scoped_acoustic["status"] = "pass"
    scoped_acoustic["blocking_outlier_count"] = 0
    scoped_acoustic["blocking_outliers"] = []
    validate_normative_transcript(
        scoped,
        max_overlap_seconds=max_overlap_seconds,
    )
    return audited


def validate_normative_transcript_for_intervals(
    transcript: Mapping[str, Any],
    selected_intervals: Iterable[tuple[float, float]],
    *,
    max_overlap_seconds: float = 0.25,
) -> list[dict[str, Any]]:
    """Validate a source transcript for the intervals actually used by an EDL.

    A source take may legitimately contain unexplained slate/countdown speech
    that the edit never selects.  The global transcript remains ``review`` for
    audit and cache purposes, while final readiness may proceed only when every
    acoustic blocker is a well-formed orphan entirely outside all selected
    source intervals.  Any blocker touching selected audio remains fatal.

    Returns the audited, out-of-scope blockers.  A globally normative
    transcript returns an empty list.
    """
    blockers = validate_provisional_normative_transcript(
        transcript,
        max_overlap_seconds=max_overlap_seconds,
    )
    if not blockers:
        return []

    intervals: list[tuple[float, float]] = []
    for raw_start, raw_end in selected_intervals:
        start = float(raw_start)
        end = float(raw_end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0.0 or end <= start:
            raise TranscriptContractError("selected transcript interval is invalid")
        intervals.append((start, end))
    if not intervals:
        raise TranscriptContractError("selected transcript intervals are required")

    audited: list[dict[str, Any]] = []
    for blocker in blockers:
        component = blocker.get("component")
        blocker_start = float(component["start"])
        blocker_end = float(component["end"])
        if any(
            blocker_end > selected_start and blocker_start < selected_end
            for selected_start, selected_end in intervals
        ):
            raise TranscriptContractError(
                "unattributed acoustic activity overlaps selected source audio"
            )
        audited.append(dict(blocker))

    return audited


def is_cache_valid(
    cache: Mapping[str, Any] | str | os.PathLike[str],
    *,
    source_sha256: str,
    config: WhisperXConfig,
) -> bool:
    """Return true for a strict or scopeable-provisional bound cache."""

    try:
        if isinstance(cache, (str, os.PathLike)):
            value = json.loads(Path(cache).read_text(encoding="utf-8"))
        else:
            value = cache
        if not isinstance(value, Mapping):
            return False
        metadata = value.get("_alano_cut")
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("schema_version") != TRANSCRIPT_SCHEMA_VERSION:
            return False
        if metadata.get("transcription_provider") != TRANSCRIPTION_PROVIDER:
            return False
        if metadata.get("source_sha256") != source_sha256.lower():
            return False
        if metadata.get("config_sha256") != config.sha256:
            return False
        if metadata.get("config") != config.to_dict():
            return False
        validate_provisional_normative_transcript(value)
        return True
    except (OSError, json.JSONDecodeError, TranscriptContractError, TypeError, ValueError):
        return False


def write_json_atomic(path: str | os.PathLike[str], data: Mapping[str, Any]) -> None:
    """Write canonical JSON with flush/fsync and an atomic same-volume replace."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Serialize before creating a temp file so a bad value cannot touch disk.
    payload = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        temp_path = None
        # POSIX needs the directory entry flushed for crash durability.  Windows
        # commonly rejects directory fsync, so this is intentionally best effort.
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "DEFAULT_ACOUSTIC_SNAP_REVISION",
    "DEFAULT_DIARIZATION_MODEL",
    "DEFAULT_PORTUGUESE_HOTWORDS",
    "DEFAULT_PORTUGUESE_INITIAL_PROMPT",
    "DEFAULT_PORTUGUESE_ALIGN_MODEL",
    "DEFAULT_SEMANTIC_FUSION_REVISION",
    "TRANSCRIPT_SCHEMA_VERSION",
    "TRANSCRIPTION_PROVIDER",
    "TranscriptContractError",
    "WhisperXConfig",
    "config_hash",
    "convert_whisperx_result",
    "analyze_alignment_quality",
    "is_cache_valid",
    "normalize_speaker_id",
    "sha256_file",
    "validate_transcript",
    "validate_normative_transcript",
    "validate_provisional_normative_transcript",
    "validate_normative_transcript_for_intervals",
    "write_json_atomic",
]
