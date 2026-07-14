"""Heavyweight CUDA worker for local WhisperX transcription.

This module is intentionally executed by the dedicated WhisperX runtime, not
imported by Alano Cut's lightweight helper environment.  Configuration is read
from JSON and the Hugging Face token is read only from the process environment;
secrets are never accepted on the command line or written to the result.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Iterable, Mapping


DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
PORTUGUESE_ALIGN_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"
SAMPLE_RATE = 16_000
SEMANTIC_VERIFIER_MODEL = "small"
SEMANTIC_FUSION_REVISION = "windowed-consensus-v1"
DEFAULT_RECORDING_CUES = frozenset(
    {"corta", "cortar", "volta", "refaz", "regrava", "gravando"}
)
_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _gpu_cleanup(torch: Any, *objects: Any) -> None:
    # References owned by the caller are deleted there; this helper makes the
    # collection/cache boundary explicit between the three GPU stages.
    del objects
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()


def _is_oom(error: BaseException) -> bool:
    message = str(error).lower()
    return "out of memory" in message or "cuda_error_out_of_memory" in message


def _normalized_words(text: object) -> list[str]:
    return [match.group(0).casefold() for match in _WORD_RE.finditer(str(text or ""))]


def _alphanumeric_key(text: object) -> str:
    return "".join(
        character
        for character in str(text or "").casefold()
        if character.isalnum()
    )


def build_aligned_word_hints(
    aligned_words: Iterable[Mapping[str, Any]],
    verifier_segments: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind faster-whisper timestamps to the exact WhisperX word sequence.

    Faster-whisper may tokenize ``4.1`` as ``4`` + ``.1`` and ``e-mail`` as
    ``e`` + ``-mail`` while WhisperX emits one word.  Both sequences originate
    from the same coverage transcript, so matching their concatenated
    alphanumeric character streams gives an auditable, lossless binding without
    fuzzy text guesses.
    """
    source_words: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(verifier_segments):
        for source_word_index, word in enumerate(segment.get("words") or []):
            key = _alphanumeric_key(word.get("word"))
            start = float(word.get("start", math.nan))
            end = float(word.get("end", math.nan))
            if not key or not math.isfinite(start) or not math.isfinite(end) or end <= start:
                continue
            source_words.append(
                {
                    "key": key,
                    "text": str(word.get("word") or ""),
                    "start": start,
                    "end": end,
                    "probability": float(word.get("probability") or 0.0),
                    "segment_index": segment_index,
                    "source_word_index": source_word_index,
                }
            )

    target_words: list[dict[str, Any]] = []
    for aligned_word_index, word in enumerate(aligned_words):
        key = _alphanumeric_key(word.get("word", word.get("text")))
        if not key:
            raise RuntimeError(
                f"aligned word {aligned_word_index} has no alphanumeric content"
            )
        target_words.append(
            {
                "key": key,
                "text": str(word.get("word", word.get("text")) or ""),
                "aligned_word_index": aligned_word_index,
            }
        )

    def spans(records: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
        stream = ""
        result: list[tuple[int, int]] = []
        for record in records:
            start = len(stream)
            stream += str(record["key"])
            result.append((start, len(stream)))
        return stream, result

    source_stream, source_spans = spans(source_words)
    target_stream, target_spans = spans(target_words)
    if not source_stream or source_stream != target_stream:
        raise RuntimeError(
            "coverage word hints do not match the aligned alphanumeric sequence"
        )

    hints: list[dict[str, Any]] = []
    source_cursor = 0
    for target, (target_start, target_end) in zip(target_words, target_spans):
        while source_cursor < len(source_spans) and source_spans[source_cursor][1] <= target_start:
            source_cursor += 1
        matching_indices: list[int] = []
        index = source_cursor
        while index < len(source_spans) and source_spans[index][0] < target_end:
            if source_spans[index][1] > target_start:
                matching_indices.append(index)
            index += 1
        if not matching_indices:
            raise RuntimeError(
                f"aligned word {target['aligned_word_index']} has no verifier timing hint"
            )
        matches = [source_words[index] for index in matching_indices]
        hints.append(
            {
                "aligned_word_index": int(target["aligned_word_index"]),
                "text": target["text"],
                "start": round(min(float(item["start"]) for item in matches), 6),
                "end": round(max(float(item["end"]) for item in matches), 6),
                "probability": round(
                    min(float(item["probability"]) for item in matches), 6
                ),
                "source_word_count": len(matches),
            }
        )
    return hints


def _materialize_faster_whisper_segments(
    segments: Iterable[Any], *, include_words: bool
) -> list[dict[str, Any]]:
    """Detach a faster-whisper generator from its GPU model."""
    materialized: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.text or "")
        if not text.strip():
            continue
        item: dict[str, Any] = {
            "start": round(float(segment.start), 6),
            "end": round(float(segment.end), 6),
            "text": text,
        }
        if include_words:
            item["words"] = [
                {
                    "word": str(word.word),
                    "start": round(float(word.start), 6),
                    "end": round(float(word.end), 6),
                    "probability": round(float(word.probability), 6),
                }
                for word in (segment.words or [])
                if float(word.end) > float(word.start)
            ]
        materialized.append(item)
    return materialized


def _window_start_samples(
    total_samples: int,
    *,
    sample_rate: int = SAMPLE_RATE,
    window_seconds: float = 30.0,
    overlap_seconds: float = 15.0,
) -> list[int]:
    """Cover an audio stream with deterministic full-context windows."""
    if total_samples <= 0 or sample_rate <= 0:
        raise ValueError("audio and sample rate must be positive")
    window_samples = round(window_seconds * sample_rate)
    overlap_samples = round(overlap_seconds * sample_rate)
    if window_samples <= 0 or overlap_samples < 0 or overlap_samples >= window_samples:
        raise ValueError("window overlap must be non-negative and shorter than the window")
    if total_samples <= window_samples:
        return [0]
    step_samples = window_samples - overlap_samples
    last_full_start = total_samples - window_samples
    starts = list(range(0, last_full_start + 1, step_samples))
    if starts[-1] != last_full_start:
        starts.append(last_full_start)
    return starts


def _deduplicate_recording_cue_segments(
    candidates: Iterable[dict[str, Any]],
    *,
    duplicate_tolerance_seconds: float = 0.350,
) -> list[dict[str, Any]]:
    """Collapse the same cue decoded in overlapping windows."""
    clusters: list[list[dict[str, Any]]] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            float(item["words"][0]["start"]),
            float(item["words"][0]["end"]),
            _normalized_words(item["words"][0].get("word"))[0],
        ),
    )
    for candidate in ordered:
        word = candidate["words"][0]
        token = _normalized_words(word.get("word"))[0]
        midpoint = (float(word["start"]) + float(word["end"])) / 2.0
        duplicate_index: int | None = None
        for index, cluster in enumerate(clusters):
            existing_word = cluster[0]["words"][0]
            existing_token = _normalized_words(existing_word.get("word"))[0]
            existing_midpoint = (
                float(existing_word["start"]) + float(existing_word["end"])
            ) / 2.0
            if (
                token == existing_token
                and abs(midpoint - existing_midpoint) <= duplicate_tolerance_seconds
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            clusters.append([candidate])
            continue
        clusters[duplicate_index].append(candidate)

    selected: list[dict[str, Any]] = []
    for cluster in clusters:
        best = max(
            cluster,
            key=lambda item: (
                float(item["words"][0].get("probability") or 0.0),
                -abs(
                    float(item["words"][0]["end"])
                    - float(item["words"][0]["start"])
                ),
                -float(item["words"][0]["start"]),
            ),
        )
        best = {
            **best,
            "consensus_count": len(
                {float(item.get("window_start", -1.0)) for item in cluster}
            ),
        }
        selected.append(best)
    return sorted(
        selected,
        key=lambda item: (
            float(item["words"][0]["start"]),
            float(item["words"][0]["end"]),
        ),
    )


def transcribe_windowed_recording_cues(
    model: Any,
    audio: Any,
    *,
    language: str,
    beam_size: int,
    cue_words: set[str] | frozenset[str] = DEFAULT_RECORDING_CUES,
    minimum_probability: float = 0.50,
    window_seconds: float = 30.0,
    overlap_seconds: float = 15.0,
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Decode only auditable recording-cue hypotheses in short windows.

    Ordinary small-model text is retained solely as lexical context for a cue;
    it never replaces the large-v3 transcript. Short, overlapping windows make
    isolated director cues recoverable without trusting a second full transcript.
    """
    starts = _window_start_samples(
        len(audio),
        window_seconds=window_seconds,
        overlap_seconds=overlap_seconds,
    )
    window_samples = round(window_seconds * SAMPLE_RATE)
    candidates: list[dict[str, Any]] = []
    decoded_token_count = 0
    for start_sample in starts:
        base_seconds = start_sample / SAMPLE_RATE
        window_audio = audio[start_sample : start_sample + window_samples]
        generator, _ = model.transcribe(
            window_audio,
            language=language,
            beam_size=beam_size,
            best_of=beam_size,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=True,
            temperature=0.0,
        )
        segments = _materialize_faster_whisper_segments(generator, include_words=True)
        decoded_token_count += sum(
            len(_normalized_words(segment.get("text"))) for segment in segments
        )
        for segment in segments:
            segment_start = round(base_seconds + float(segment["start"]), 6)
            segment_end = round(base_seconds + float(segment["end"]), 6)
            for word in segment.get("words") or []:
                normalized = _normalized_words(word.get("word"))
                cue = normalized[0] if normalized else ""
                probability = float(word.get("probability") or 0.0)
                if cue not in cue_words or probability < minimum_probability:
                    continue
                candidates.append(
                    {
                        "start": segment_start,
                        "end": segment_end,
                        "text": str(segment.get("text") or ""),
                        "words": [
                            {
                                **word,
                                "start": round(base_seconds + float(word["start"]), 6),
                                "end": round(base_seconds + float(word["end"]), 6),
                            }
                        ],
                        "window_start": round(base_seconds, 6),
                    }
                )
    deduplicated = _deduplicate_recording_cue_segments(candidates)
    return deduplicated, {
        "window_count": len(starts),
        "window_seconds": window_seconds,
        "overlap_seconds": overlap_seconds,
        "decoded_token_count": decoded_token_count,
        "candidate_count": len(candidates),
        "deduplicated_candidate_count": len(deduplicated),
    }


def _intervals_overlap(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
    *,
    padding: float = 0.0,
) -> bool:
    return first_end + padding > second_start and second_end + padding > first_start


def _insert_cue_before_anchor(text: str, cue: str, anchor: str) -> str | None:
    pattern = re.compile(rf"(?<!\w){re.escape(anchor)}(?!\w)", re.IGNORECASE)
    match = pattern.search(text)
    if match is None:
        return None
    display = cue.capitalize() if not text[: match.start()].strip() else cue
    return f"{text[:match.start()]}{display}, {text[match.start():]}"


def merge_missing_recording_cues(
    primary_segments: list[dict[str, Any]],
    verifier_segments: list[dict[str, Any]],
    *,
    cue_words: set[str] | frozenset[str] = DEFAULT_RECORDING_CUES,
    minimum_probability: float = 0.50,
    minimum_missing_cue_consensus: int = 2,
    temporal_tolerance_seconds: float = 2.5,
    alignment_padding_seconds: float = 2.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge only high-confidence recording cues omitted by the main ASR.

    The small verifier is deliberately not allowed to replace ordinary words.
    It may insert a known cue only when the surrounding verifier segment shares
    lexical anchors with a temporally adjacent primary segment.  WhisperX then
    force-aligns the consolidated text; acoustic validation remains mandatory.
    """
    merged = [dict(segment) for segment in primary_segments]
    recoveries: list[dict[str, Any]] = []

    for verifier_segment in verifier_segments:
        verifier_tokens = _normalized_words(verifier_segment.get("text"))
        verifier_words = verifier_segment.get("words") or []
        for word_index, verifier_word in enumerate(verifier_words):
            cue = (_normalized_words(verifier_word.get("word")) or [""])[0]
            probability = float(verifier_word.get("probability") or 0.0)
            if cue not in cue_words or probability < minimum_probability:
                continue
            cue_start = float(verifier_word["start"])
            cue_end = float(verifier_word["end"])

            existing_candidates: list[tuple[float, int]] = []
            for primary_index, primary in enumerate(merged):
                if cue not in _normalized_words(primary.get("text")):
                    continue
                if _intervals_overlap(
                    float(primary["start"]),
                    float(primary["end"]),
                    cue_start,
                    cue_end,
                    padding=temporal_tolerance_seconds,
                ):
                    distance = abs(
                        ((float(primary["start"]) + float(primary["end"])) / 2.0)
                        - ((cue_start + cue_end) / 2.0)
                    )
                    existing_candidates.append((distance, primary_index))
            if existing_candidates:
                _, primary_index = min(existing_candidates)
                primary = merged[primary_index]
                cue_outside_segment = (
                    cue_start < float(primary["start"])
                    or cue_end > float(primary["end"])
                )
                if cue_outside_segment:
                    primary["start"] = round(
                        min(float(primary["start"]), cue_start - alignment_padding_seconds),
                        6,
                    )
                    primary["end"] = round(
                        max(float(primary["end"]), cue_end + alignment_padding_seconds),
                        6,
                    )
                    recoveries.append(
                        {
                            "cue": cue,
                            "probability": round(probability, 6),
                            "verifier_start": round(cue_start, 6),
                            "verifier_end": round(cue_end, 6),
                            "primary_segment_index": primary_index,
                            "placement": "existing_cue_context_expanded",
                            "requires_component_recovery": True,
                            "consensus_count": int(
                                verifier_segment.get("consensus_count") or 1
                            ),
                        }
                    )
                continue

            if (
                int(verifier_segment.get("consensus_count") or 1)
                < minimum_missing_cue_consensus
            ):
                continue

            noncue_verifier = {token for token in verifier_tokens if token not in cue_words}
            candidates: list[tuple[int, float, float, int]] = []
            for primary_index, primary in enumerate(merged):
                primary_tokens = _normalized_words(primary.get("text"))
                shared = len(noncue_verifier.intersection(primary_tokens))
                if shared == 0:
                    continue
                overlap = max(
                    0.0,
                    min(float(primary["end"]), float(verifier_segment["end"]))
                    - max(float(primary["start"]), float(verifier_segment["start"])),
                )
                midpoint_distance = abs(
                    ((float(primary["start"]) + float(primary["end"])) / 2.0)
                    - ((float(verifier_segment["start"]) + float(verifier_segment["end"])) / 2.0)
                )
                if overlap > 0.0 or midpoint_distance <= temporal_tolerance_seconds:
                    candidates.append((shared, overlap, -midpoint_distance, primary_index))
            if not candidates:
                continue
            _, _, _, target_index = max(candidates)
            target = merged[target_index]
            target_tokens = _normalized_words(target.get("text"))

            next_anchor: str | None = None
            cue_token_index = next(
                (index for index, token in enumerate(verifier_tokens) if token == cue),
                -1,
            )
            for token in verifier_tokens[cue_token_index + 1 :]:
                if token in target_tokens and token not in cue_words:
                    next_anchor = token
                    break

            placement = "before_anchor"
            updated_text = (
                _insert_cue_before_anchor(str(target["text"]), cue, next_anchor)
                if next_anchor
                else None
            )
            if updated_text is None:
                previous_anchor = next(
                    (
                        token
                        for token in reversed(verifier_tokens[:cue_token_index])
                        if token in target_tokens and token not in cue_words
                    ),
                    None,
                )
                if previous_anchor is None:
                    continue
                placement = "after_segment"
                base = str(target["text"]).rstrip()
                punctuation = base[-1] if base and base[-1] in ".!?" else "."
                if punctuation != ".":
                    base = base[:-1]
                updated_text = f"{base.rstrip('.,;:')} {cue}.{punctuation if punctuation != '.' else ''}"

            target["text"] = updated_text
            target["start"] = round(
                min(float(target["start"]), cue_start - alignment_padding_seconds), 6
            )
            target["end"] = round(
                max(float(target["end"]), cue_end + alignment_padding_seconds), 6
            )
            recoveries.append(
                {
                    "cue": cue,
                    "probability": round(probability, 6),
                    "verifier_start": round(cue_start, 6),
                    "verifier_end": round(cue_end, 6),
                    "primary_segment_index": target_index,
                    "placement": placement,
                    "requires_component_recovery": True,
                    "consensus_count": int(
                        verifier_segment.get("consensus_count") or 1
                    ),
                }
            )

    merged.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    return merged, recoveries


def reconcile_aligned_recording_cues(
    aligned_segments: list[dict[str, Any]],
    verifier_segments: list[dict[str, Any]],
    *,
    cue_words: set[str] | frozenset[str] = DEFAULT_RECORDING_CUES,
    minimum_probability: float = 0.50,
    minimum_missing_cue_consensus: int = 2,
    existing_cue_tolerance_seconds: float = 5.0,
    alignment_padding_seconds: float = 2.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile windowed cue votes after the first forced-alignment pass.

    Word timestamps make one-to-one cue matching reliable. Missing consensus
    cues are inserted at their chronological word boundary, so ordinary text
    from the verifier is never copied into the authoritative transcript.
    """
    merged = [
        {**segment, "words": [dict(word) for word in segment.get("words", [])]}
        for segment in aligned_segments
    ]
    existing: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(merged):
        for word_index, word in enumerate(segment.get("words", [])):
            normalized = _normalized_words(word.get("word"))
            token = normalized[0] if normalized else ""
            if token in cue_words and _valid_timed_interval(word.get("start"), word.get("end")):
                existing.append(
                    {
                        "cue": token,
                        "segment_index": segment_index,
                        "word_index": word_index,
                        "start": float(word["start"]),
                        "end": float(word["end"]),
                    }
                )

    used_existing: set[int] = set()
    insertions: dict[int, list[tuple[int, str, int]]] = {}
    recoveries: list[dict[str, Any]] = []
    ordered_verifier = sorted(
        verifier_segments,
        key=lambda item: (
            float(item["words"][0]["start"]),
            float(item["words"][0]["end"]),
        ),
    )
    for verifier_order, verifier_segment in enumerate(ordered_verifier):
        verifier_word = (verifier_segment.get("words") or [{}])[0]
        normalized = _normalized_words(verifier_word.get("word"))
        cue = normalized[0] if normalized else ""
        probability = float(verifier_word.get("probability") or 0.0)
        if cue not in cue_words or probability < minimum_probability:
            continue
        cue_start = float(verifier_word["start"])
        cue_end = float(verifier_word["end"])
        cue_midpoint = (cue_start + cue_end) / 2.0
        candidates = [
            (
                abs(((item["start"] + item["end"]) / 2.0) - cue_midpoint),
                index,
                item,
            )
            for index, item in enumerate(existing)
            if index not in used_existing and item["cue"] == cue
        ]
        if candidates:
            distance, existing_index, item = min(candidates)
            if distance <= existing_cue_tolerance_seconds:
                used_existing.add(existing_index)
                if distance > 0.150:
                    segment = merged[int(item["segment_index"])]
                    segment["start"] = round(
                        max(0.0, min(float(segment["start"]), cue_start - alignment_padding_seconds)),
                        6,
                    )
                    segment["end"] = round(
                        max(float(segment["end"]), cue_end + alignment_padding_seconds),
                        6,
                    )
                    recoveries.append(
                        {
                            "cue": cue,
                            "probability": round(probability, 6),
                            "verifier_start": round(cue_start, 6),
                            "verifier_end": round(cue_end, 6),
                            "primary_segment_index": int(item["segment_index"]),
                            "placement": "existing_cue_realign",
                            "requires_component_recovery": True,
                            "consensus_count": int(
                                verifier_segment.get("consensus_count") or 1
                            ),
                        }
                    )
                continue

        consensus_count = int(verifier_segment.get("consensus_count") or 1)
        if consensus_count < minimum_missing_cue_consensus:
            continue
        timed_words = [
            (segment_index, word_index, word)
            for segment_index, segment in enumerate(merged)
            for word_index, word in enumerate(segment.get("words", []))
            if _valid_timed_interval(word.get("start"), word.get("end"))
        ]
        following = next(
            (
                item
                for item in timed_words
                if float(item[2]["start"]) >= cue_midpoint
            ),
            None,
        )
        if following is not None:
            segment_index, insertion_index, _ = following
            placement = "before_aligned_word"
        elif timed_words:
            segment_index, last_word_index, _ = timed_words[-1]
            insertion_index = last_word_index + 1
            placement = "after_aligned_word"
        else:
            raise RuntimeError("aligned transcript has no timed word for cue insertion")
        insertions.setdefault(segment_index, []).append(
            (insertion_index, cue, verifier_order)
        )
        segment = merged[segment_index]
        segment["start"] = round(
            max(0.0, min(float(segment["start"]), cue_start - alignment_padding_seconds)),
            6,
        )
        segment["end"] = round(
            max(float(segment["end"]), cue_end + alignment_padding_seconds), 6
        )
        recoveries.append(
            {
                "cue": cue,
                "probability": round(probability, 6),
                "verifier_start": round(cue_start, 6),
                "verifier_end": round(cue_end, 6),
                "primary_segment_index": segment_index,
                "placement": placement,
                "requires_component_recovery": True,
                "consensus_count": consensus_count,
            }
        )

    for segment_index, pending in insertions.items():
        segment = merged[segment_index]
        tokens = [str(word.get("word") or "").strip() for word in segment.get("words", [])]
        for insertion_index, cue, verifier_order in sorted(
            pending, key=lambda item: (item[0], item[2]), reverse=True
        ):
            display = cue.capitalize() + ","
            tokens.insert(insertion_index, display)
        segment["text"] = " ".join(token for token in tokens if token).strip()
    previous_start = 0.0
    for segment in merged:
        # Alignment padding is a search window, not editorial order. Never let
        # it move a later lexical segment ahead of its predecessor.
        segment["start"] = round(max(previous_start, float(segment["start"])), 6)
        if float(segment["end"]) <= float(segment["start"]):
            raise RuntimeError("cue reconciliation produced an empty segment")
        previous_start = float(segment["start"])
    return merged, recoveries


def build_literal_coverage_segments(
    verifier_segments: list[dict[str, Any]],
    *,
    audio_duration: float,
    cue_words: set[str] | frozenset[str] = DEFAULT_RECORDING_CUES,
    alignment_padding_seconds: float = 1.5,
    minimum_cue_probability: float = 0.50,
    boundary_guard_seconds: float = 0.10,
    maximum_fused_duration_seconds: float = 45.0,
    maximum_fused_tokens: int = 320,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build disjoint padded alignment windows from authoritative segments.

    Segment bounds are search windows only; WhisperX and the acoustic gate
    determine final word times. WhisperX aligns each segment independently, so
    windows must be disjoint or later words can align before earlier ones.
    Native-overlap pairs are fused and padding-only overlaps are partitioned.
    """
    if not math.isfinite(audio_duration) or audio_duration <= 0.0:
        raise RuntimeError("audio duration must be positive and finite")
    if alignment_padding_seconds < 0.0 or boundary_guard_seconds < 0.0:
        raise RuntimeError("alignment padding and boundary guard cannot be negative")

    chunks: list[dict[str, Any]] = []
    pending_recoveries: list[dict[str, Any]] = []
    previous_native_start = -math.inf
    for segment_index, segment in enumerate(verifier_segments):
        native_start = float(segment["start"])
        native_end = float(segment["end"])
        if not math.isfinite(native_start) or not math.isfinite(native_end):
            raise RuntimeError("semantic coverage segment times must be finite")
        if native_start < previous_native_start:
            raise RuntimeError("semantic coverage segments are in reverse time order")
        previous_native_start = native_start

        text = str(segment.get("text") or "")
        if native_end <= native_start or not text.strip():
            continue

        valid_words = [
            word
            for word in segment.get("words") or []
            if math.isfinite(float(word.get("start", math.nan)))
            and math.isfinite(float(word.get("end", math.nan)))
            and float(word["end"]) > float(word["start"])
        ]
        first_anchor = min(
            native_start,
            float(valid_words[0]["start"]) if valid_words else native_start,
        )
        last_anchor = max(
            native_end,
            float(valid_words[-1]["end"]) if valid_words else native_end,
        )
        first_anchor = max(0.0, first_anchor)
        last_anchor = min(audio_duration, last_anchor)
        if last_anchor <= first_anchor:
            continue

        source_indices = [segment_index]
        if chunks and first_anchor < float(chunks[-1]["last_anchor"]) - 1e-9:
            previous = chunks[-1]
            fused_start = min(float(previous["first_anchor"]), first_anchor)
            fused_end = max(float(previous["last_anchor"]), last_anchor)
            fused_text = f'{str(previous["text"]).rstrip()} {text.lstrip()}'
            if fused_end - fused_start > maximum_fused_duration_seconds:
                raise RuntimeError("native-overlap coverage chunk exceeds duration limit")
            if len(_normalized_words(fused_text)) > maximum_fused_tokens:
                raise RuntimeError("native-overlap coverage chunk exceeds token limit")
            previous["first_anchor"] = fused_start
            previous["last_anchor"] = fused_end
            previous["text"] = fused_text
            previous["source_indices"].append(segment_index)
        else:
            chunks.append(
                {
                    "first_anchor": first_anchor,
                    "last_anchor": last_anchor,
                    "text": text,
                    "source_indices": source_indices,
                }
            )

        for word in segment.get("words") or []:
            normalized = _normalized_words(word.get("word"))
            cue = normalized[0] if normalized else ""
            probability = float(word.get("probability") or 0.0)
            if cue not in cue_words or probability < minimum_cue_probability:
                continue
            pending_recoveries.append(
                {
                    "cue": cue,
                    "probability": round(probability, 6),
                    "verifier_start": round(float(word["start"]), 6),
                    "verifier_end": round(float(word["end"]), 6),
                    "source_segment_index": segment_index,
                    "placement": "coverage_model_cue",
                    "requires_component_recovery": True,
                }
            )

    coverage: list[dict[str, Any]] = []
    source_to_chunk: dict[int, int] = {}
    for chunk_index, chunk in enumerate(chunks):
        for source_index in chunk["source_indices"]:
            source_to_chunk[int(source_index)] = chunk_index
        coverage.append(
            {
                "start": max(
                    0.0, float(chunk["first_anchor"]) - alignment_padding_seconds
                ),
                "end": min(
                    audio_duration,
                    float(chunk["last_anchor"]) + alignment_padding_seconds,
                ),
                "text": chunk["text"],
                "first_anchor": float(chunk["first_anchor"]),
                "last_anchor": float(chunk["last_anchor"]),
            }
        )

    for index in range(1, len(coverage)):
        previous = coverage[index - 1]
        current = coverage[index]
        if float(previous["end"]) <= float(current["start"]):
            continue

        previous_anchor = float(previous["last_anchor"])
        current_anchor = float(current["first_anchor"])
        anchor_gap = current_anchor - previous_anchor
        if anchor_gap < -1e-9:
            raise RuntimeError("native-overlap coverage chunks were not fused")
        if anchor_gap <= 2.0 * boundary_guard_seconds:
            boundary = previous_anchor + anchor_gap / 2.0
        else:
            lower = previous_anchor + boundary_guard_seconds
            upper = current_anchor - boundary_guard_seconds
            preferred = current_anchor - alignment_padding_seconds
            boundary = min(max(preferred, lower), upper)

        boundary = round(boundary, 6)
        previous["end"] = boundary
        current["start"] = boundary

    for item in coverage:
        item["start"] = round(float(item.pop("start")), 6)
        item["end"] = round(float(item.pop("end")), 6)
        item.pop("first_anchor")
        item.pop("last_anchor")
        if float(item["end"]) <= float(item["start"]):
            raise RuntimeError("semantic coverage produced an empty alignment window")

    recoveries: list[dict[str, Any]] = []
    for recovery in pending_recoveries:
        source_index = int(recovery.pop("source_segment_index"))
        recovery["primary_segment_index"] = source_to_chunk[source_index]
        recoveries.append(recovery)
    return coverage, recoveries


def _suppress_in_memory_torchcodec_warning() -> None:
    r"""Hide Pyannote's decoder warning when this worker supplies PCM directly.

    Pyannote's warning starts with a newline and spans multiple lines.  A plain
    ``.*`` warning filter therefore does not match it.  ``[\s\S]`` deliberately
    covers newlines while keeping unrelated warnings visible.
    """
    warnings.filterwarnings(
        "ignore",
        message=r"[\s\S]*torchcodec is not installed correctly[\s\S]*",
    )


def _speaker_name(value: object) -> str:
    raw = str(value or "SPEAKER_00")
    digits = "".join(char for char in raw if char.isdigit())
    return f"speaker_{int(digits) if digits else 0}"


def _annotation_turns(annotation: Any) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        start = float(segment.start)
        end = float(segment.end)
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        turns.append(
            {
                "start": round(start, 6),
                "end": round(end, 6),
                "speaker": _speaker_name(speaker),
            }
        )
    turns.sort(key=lambda item: (item["start"], item["end"], item["speaker"]))
    return turns


def _best_speaker(start: float, end: float, turns: Iterable[dict[str, Any]]) -> str | None:
    midpoint = (start + end) / 2.0
    best_score: tuple[float, float] | None = None
    best_speaker: str | None = None
    for turn in turns:
        turn_start = float(turn["start"])
        turn_end = float(turn["end"])
        overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
        if overlap <= 0.0:
            continue
        distance = abs(midpoint - ((turn_start + turn_end) / 2.0))
        score = (overlap, -distance)
        # Turns are sorted. Retaining the first exact tie avoids assigning a
        # segment to the lexicographically largest speaker by accident.
        if best_score is None or score > best_score:
            best_score = score
            best_speaker = str(turn["speaker"])
    return best_speaker


def _valid_timed_interval(start: object, end: object) -> bool:
    if (
        not isinstance(start, (int, float))
        or isinstance(start, bool)
        or not isinstance(end, (int, float))
        or isinstance(end, bool)
    ):
        return False
    return math.isfinite(float(start)) and math.isfinite(float(end)) and float(end) > float(start)


def assign_speakers(
    aligned: dict[str, Any], turns: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assign exclusive Community-1 speakers without a pandas dependency."""
    assignments: list[tuple[dict[str, Any], str]] = []
    for segment_index, segment in enumerate(aligned.get("segments", [])):
        if not isinstance(segment, dict):
            continue
        segment_start = segment.get("start")
        segment_end = segment.get("end")
        if _valid_timed_interval(segment_start, segment_end):
            speaker = _best_speaker(float(segment_start), float(segment_end), turns)
            if speaker is None:
                raise RuntimeError(
                    "Community-1 returned no overlapping speaker turn for "
                    f"segment[{segment_index}] ({float(segment_start):.6f}-"
                    f"{float(segment_end):.6f})"
                )
            assignments.append((segment, speaker))
        for word_index, word in enumerate(segment.get("words", [])):
            if not isinstance(word, dict):
                continue
            start = word.get("start")
            end = word.get("end")
            if not _valid_timed_interval(start, end):
                continue
            speaker = _best_speaker(float(start), float(end), turns)
            if speaker is None:
                raise RuntimeError(
                    "Community-1 returned no overlapping speaker turn for "
                    f"segment[{segment_index}].word[{word_index}] "
                    f"({float(start):.6f}-{float(end):.6f})"
                )
            assignments.append((word, speaker))
    for item, speaker in assignments:
        item["speaker"] = speaker
    return aligned


def _validate_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("configuration must be a JSON object")
    if config.get("device", "cuda") != "cuda":
        raise ValueError("the normative WhisperX worker requires device='cuda'")
    diarization_mode = config.get("diarization_mode", "community-1")
    if diarization_mode not in {"community-1", "none"}:
        raise ValueError("diarization_mode must be 'community-1' or 'none'")
    if diarization_mode == "community-1":
        if config.get("diarization_model", DIARIZATION_MODEL) != DIARIZATION_MODEL:
            raise ValueError(f"diarization_model must be {DIARIZATION_MODEL}")
    elif config.get("diarization_model") is not None:
        raise ValueError("diarization_model must be null when diarization is disabled")
    batch_size = config.get("batch_size", 2)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    beam_size = config.get("beam_size", 5)
    if not isinstance(beam_size, int) or isinstance(beam_size, bool) or beam_size < 1:
        raise ValueError("beam_size must be a positive integer")
    expected_vad = "pyannote" if diarization_mode == "community-1" else "silero"
    if config.get("vad_method", expected_vad) != expected_vad:
        raise ValueError(
            f"vad_method must be {expected_vad!r} for diarization_mode={diarization_mode!r}"
        )
    if not str(config.get("semantic_verifier_model") or "").strip():
        raise ValueError("semantic_verifier_model is required")
    if config.get("semantic_fusion_mode") != "guarded_union":
        raise ValueError("semantic_fusion_mode must be guarded_union")
    if config.get("semantic_fusion_revision") != SEMANTIC_FUSION_REVISION:
        raise ValueError(
            f"semantic_fusion_revision must be {SEMANTIC_FUSION_REVISION}"
        )
    if not _normalized_words(config.get("recording_cues")):
        raise ValueError("recording_cues must contain at least one cue")
    revision_keys = [
        "asr_model_revision",
        "semantic_verifier_revision",
        "align_model_revision",
    ]
    if diarization_mode == "community-1":
        revision_keys.append("diarization_model_revision")
    elif config.get("diarization_model_revision") is not None:
        raise ValueError(
            "diarization_model_revision must be null when diarization is disabled"
        )
    for key in revision_keys:
        if not str(config.get(key) or "").strip():
            raise ValueError(f"{key} is required for reproducible local transcription")
    for key in ("num_speakers", "min_speakers", "max_speakers"):
        value = config.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise ValueError(f"{key} must be a positive integer")
        if diarization_mode == "none" and value is not None:
            raise ValueError(f"{key} requires speaker diarization")
    if config.get("num_speakers") is not None and (
        config.get("min_speakers") is not None or config.get("max_speakers") is not None
    ):
        raise ValueError("num_speakers cannot be combined with min/max_speakers")
    return config


def run(audio_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    # pyannote emits a TorchCodec warning at import time on Windows/FFmpeg 8.
    # We deliberately pass a predecoded in-memory waveform, so that decoder is
    # never used and the warning is not actionable for this worker.
    _suppress_in_memory_torchcodec_warning()
    import numpy as np
    import torch
    import whisperx
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    if not torch.cuda.is_available() or "+cpu" in torch.__version__:
        raise RuntimeError("CUDA PyTorch is required; CPU fallback is disabled")
    if torch.cuda.get_device_capability(0) < (7, 0):
        raise RuntimeError("the detected NVIDIA GPU does not support efficient float16 inference")

    diarization_mode = str(config.get("diarization_mode") or "community-1")
    diarization_enabled = diarization_mode == "community-1"
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if diarization_enabled and not token:
        raise RuntimeError("HF_TOKEN is required for Community-1 diarization")

    asr_model = str(config.get("asr_model") or "large-v3")
    asr_model_revision = str(config["asr_model_revision"])
    semantic_verifier_model = str(
        config.get("semantic_verifier_model") or SEMANTIC_VERIFIER_MODEL
    )
    semantic_verifier_revision = str(config["semantic_verifier_revision"])
    language = str(config.get("language") or "pt")
    requested_compute = str(config.get("compute_type") or "float16")
    batch_size = int(config.get("batch_size") or 2)
    beam_size = int(config.get("beam_size") or 5)
    initial_prompt = config.get("initial_prompt")
    hotwords = config.get("hotwords")
    cue_words = {
        token
        for token in _normalized_words(config.get("recording_cues") or "")
        if token
    } or set(DEFAULT_RECORDING_CUES)
    align_model_revision = str(config["align_model_revision"])
    diarization_model_revision = (
        str(config["diarization_model_revision"]) if diarization_enabled else None
    )
    cache_root = Path(
        config.get("model_cache_dir")
        or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AlanoCut" / "models")
    )
    cache_root.mkdir(parents=True, exist_ok=True)

    audio = whisperx.load_audio(str(audio_path))
    if not isinstance(audio, np.ndarray) or audio.ndim != 1 or audio.size == 0:
        raise RuntimeError("WhisperX failed to decode non-empty mono audio")

    phase_seconds: dict[str, float] = {}
    peak_vram: dict[str, int] = {}
    effective_compute = requested_compute
    semantic_recoveries: list[dict[str, Any]] = []

    vad_model = None
    if not diarization_enabled:
        try:
            from helpers.pinned_silero import load_pinned_silero
        except ModuleNotFoundError as exc:
            if exc.name != "helpers":
                raise
            from pinned_silero import load_pinned_silero
        vad_model = load_pinned_silero(cache_root)

    stage_started = time.perf_counter()
    model = None
    try:
        primary_repo = (
            asr_model
            if "/" in asr_model
            else f"Systran/faster-whisper-{asr_model}"
        )
        primary_snapshot = snapshot_download(
            repo_id=primary_repo,
            revision=asr_model_revision,
            cache_dir=str(cache_root / "faster-whisper"),
        )
        try:
            model = whisperx.load_model(
                primary_snapshot,
                "cuda",
                compute_type=effective_compute,
                language=language,
                vad_model=vad_model,
                vad_method=str(config.get("vad_method") or "pyannote"),
                asr_options={
                    "beam_size": beam_size,
                    "best_of": beam_size,
                    "initial_prompt": initial_prompt,
                    "hotwords": hotwords,
                },
                download_root=str(cache_root / "faster-whisper"),
            )
        except Exception as error:
            if not _is_oom(error) or effective_compute == "int8_float16":
                raise
            effective_compute = "int8_float16"
            torch.cuda.empty_cache()
            model = whisperx.load_model(
                primary_snapshot,
                "cuda",
                compute_type=effective_compute,
                language=language,
                vad_model=vad_model,
                vad_method=str(config.get("vad_method") or "pyannote"),
                asr_options={
                    "beam_size": beam_size,
                    "best_of": beam_size,
                    "initial_prompt": initial_prompt,
                    "hotwords": hotwords,
                },
                download_root=str(cache_root / "faster-whisper"),
            )
        current_batch = batch_size
        while True:
            try:
                asr_result = model.transcribe(
                    audio,
                    batch_size=current_batch,
                    language=language,
                    print_progress=True,
                )
                break
            except Exception as error:
                if not _is_oom(error) or current_batch == 1:
                    raise
                current_batch = max(1, current_batch // 2)
                torch.cuda.empty_cache()
        batch_size = current_batch
        primary_segments = [dict(segment) for segment in asr_result.get("segments", [])]
        if not primary_segments:
            raise RuntimeError("large-v3 produced no speech segments")
        peak_vram["asr"] = int(torch.cuda.max_memory_allocated())
    finally:
        if model is not None:
            del model
        _gpu_cleanup(torch)
    phase_seconds["asr"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    verifier_model = None
    try:
        verifier_model = WhisperModel(
            semantic_verifier_model,
            device="cuda",
            compute_type=effective_compute,
            download_root=str(cache_root / "faster-whisper-secondary"),
            revision=semantic_verifier_revision,
        )
        verifier_segments, verifier_stats = transcribe_windowed_recording_cues(
            verifier_model,
            audio,
            language=language,
            beam_size=beam_size,
            cue_words=cue_words,
        )
        contextual_token_count = sum(
            len(_normalized_words(segment.get("text"))) for segment in primary_segments
        )
        coverage_token_count = int(verifier_stats["decoded_token_count"])
        primary_segments, _ = build_literal_coverage_segments(
            primary_segments,
            audio_duration=len(audio) / SAMPLE_RATE,
            cue_words=frozenset(),
        )
        if not primary_segments:
            raise RuntimeError("guarded ASR union produced no speech segments")
        peak_vram["semantic_verifier"] = int(torch.cuda.max_memory_allocated())
    finally:
        if verifier_model is not None:
            del verifier_model
        _gpu_cleanup(torch)
    phase_seconds["semantic_verifier"] = round(
        time.perf_counter() - stage_started, 3
    )

    detected_language = str(asr_result.get("language") or language)
    align_model_name = config.get("align_model")
    if align_model_name is None and detected_language == "pt":
        align_model_name = PORTUGUESE_ALIGN_MODEL

    stage_started = time.perf_counter()
    align_model = None
    try:
        align_download_args = {
            "repo_id": str(align_model_name),
            "revision": align_model_revision,
            "cache_dir": str(cache_root / "alignment"),
        }
        if token:
            align_download_args["token"] = token
        align_snapshot = snapshot_download(**align_download_args)
        align_model, align_metadata = whisperx.load_align_model(
            language_code=detected_language,
            device="cuda",
            model_name=align_snapshot,
            model_dir=str(cache_root / "alignment"),
            model_cache_only=True,
        )
        aligned = whisperx.align(
            primary_segments,
            align_model,
            align_metadata,
            audio,
            "cuda",
            interpolate_method="nearest",
            return_char_alignments=False,
            print_progress=True,
        )
        reconciled_segments, semantic_recoveries = reconcile_aligned_recording_cues(
            [dict(segment) for segment in aligned.get("segments", [])],
            verifier_segments,
            cue_words=cue_words,
        )
        if semantic_recoveries:
            reconciled_segments, _ = build_literal_coverage_segments(
                reconciled_segments,
                audio_duration=len(audio) / SAMPLE_RATE,
                cue_words=frozenset(),
            )
            aligned = whisperx.align(
                reconciled_segments,
                align_model,
                align_metadata,
                audio,
                "cuda",
                interpolate_method="nearest",
                return_char_alignments=False,
                print_progress=True,
            )
        peak_vram["alignment"] = int(torch.cuda.max_memory_allocated())
    finally:
        if align_model is not None:
            del align_model
        _gpu_cleanup(torch)
    phase_seconds["alignment"] = round(time.perf_counter() - stage_started, 3)
    turns: list[dict[str, Any]] = []
    exclusive = None
    if diarization_enabled:
        stage_started = time.perf_counter()
        diarization_pipeline = None
        try:
            from pyannote.audio import Pipeline

            diarization_pipeline = Pipeline.from_pretrained(
                DIARIZATION_MODEL,
                revision=diarization_model_revision,
                token=token,
                cache_dir=str(cache_root / "pyannote"),
            )
            if diarization_pipeline is None:
                raise RuntimeError("Community-1 could not be loaded; verify model access")
            diarization_pipeline.to(torch.device("cuda"))
            waveform = torch.from_numpy(audio[None, :])
            kwargs = {
                key: config.get(key)
                for key in ("num_speakers", "min_speakers", "max_speakers")
                if config.get(key) is not None
            }
            diarization_output = diarization_pipeline(
                {"waveform": waveform, "sample_rate": SAMPLE_RATE}, **kwargs
            )
            exclusive = getattr(diarization_output, "exclusive_speaker_diarization", None)
            annotation = (
                exclusive
                if exclusive is not None
                else getattr(diarization_output, "speaker_diarization", None)
            )
            if annotation is None:
                raise RuntimeError("Community-1 returned no speaker diarization")
            turns = _annotation_turns(annotation)
            if not turns:
                raise RuntimeError("Community-1 returned no speaker turns")
            peak_vram["diarization"] = int(torch.cuda.max_memory_allocated())
        finally:
            if diarization_pipeline is not None:
                del diarization_pipeline
            _gpu_cleanup(torch)
        phase_seconds["diarization"] = round(time.perf_counter() - stage_started, 3)
    else:
        phase_seconds["diarization"] = 0.0

    return {
        "worker_schema_version": 2,
        "source_sha256": _sha256(audio_path),
        "language": detected_language,
        "segments": aligned.get("segments", []),
        "word_segments": aligned.get("word_segments", []),
        "diarization": turns,
        "models": {
            "asr": asr_model,
            "semantic_verifier": semantic_verifier_model,
            "alignment": align_model_name,
            "diarization": DIARIZATION_MODEL if diarization_enabled else None,
        },
        "model_revisions": {
            "asr": asr_model_revision,
            "semantic_verifier": semantic_verifier_revision,
            "alignment": align_model_revision,
            "diarization": diarization_model_revision,
        },
        "semantic_verification": {
            "status": "pass",
            "mode": str(config.get("semantic_fusion_mode") or "guarded_union"),
            "revision": SEMANTIC_FUSION_REVISION,
            "asr_mode": str(config.get("vad_method") or "pyannote"),
            "coverage_asr_mode": "windowed_no_vad",
            "semantic_source": "semantic_verifier",
            "contextual_token_count": contextual_token_count,
            "coverage_token_count": coverage_token_count,
            "window_count": verifier_stats["window_count"],
            "window_seconds": verifier_stats["window_seconds"],
            "overlap_seconds": verifier_stats["overlap_seconds"],
            "candidate_count": verifier_stats["candidate_count"],
            "deduplicated_candidate_count": verifier_stats[
                "deduplicated_candidate_count"
            ],
            "cue_words": sorted(cue_words),
            "recoveries": semantic_recoveries,
        },
        "runtime": {
            "whisperx": _version("whisperx"),
            "faster_whisper": _version("faster-whisper"),
            "pyannote_audio": _version("pyannote.audio"),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0),
            "compute_type": effective_compute,
            "batch_size": batch_size,
            "device": "cuda",
        },
        "diarization_exclusive": exclusive is not None,
        "phase_seconds": phase_seconds,
        "peak_vram_bytes": peak_vram,
    }


def _redact(message: str) -> str:
    for variable in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(variable)
        if value:
            message = message.replace(value, "[REDACTED]")
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description="Alano Cut WhisperX CUDA worker")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        audio_path = args.audio.resolve(strict=True)
        config_path = args.config.resolve(strict=True)
        config = _validate_config(json.loads(config_path.read_text(encoding="utf-8")))
        result = run(audio_path, config)
        _atomic_json(args.output.resolve(), result)
        return 0
    except Exception as error:
        message = _redact(str(error)).replace("\r", " ").replace("\n", " ")
        print(f"WhisperX worker failed ({type(error).__name__}): {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
