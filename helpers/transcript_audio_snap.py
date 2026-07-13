"""Acoustically validate and refine WhisperX word timestamps.

WhisperX forced alignment is retained as lexical evidence, but CTC blank frames
are not trusted as speech.  This module pairs raw and RNNoise activity, snaps
only boundaries supported by both detectors, and reports unexplained speech as
a blocking review item.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

try:
    from helpers.audio_analysis import (
        DEFAULT_VAD_PARAMS,
        EXPECTED_MODEL_HASH,
        extract_analysis_audio,
        get_combined_activity,
        get_ffmpeg_version,
        get_source_fingerprint,
        is_ffmpeg_arnndn_available,
        verify_model_hash,
    )
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from audio_analysis import (  # type: ignore[no-redef]
        DEFAULT_VAD_PARAMS,
        EXPECTED_MODEL_HASH,
        extract_analysis_audio,
        get_combined_activity,
        get_ffmpeg_version,
        get_source_fingerprint,
        is_ffmpeg_arnndn_available,
        verify_model_hash,
    )


_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ActivityComponent:
    index: int
    start: float
    end: float
    bilateral_start: float
    bilateral_end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class WordSnapConfig:
    hop_seconds: float = 0.005
    minimum_bilateral_seconds: float = 0.020
    minimum_orphan_seconds: float = 0.060
    robust_silence_seconds: float = 0.080
    component_merge_gap_seconds: float = 0.040
    weak_raw_extension_seconds: float = 0.060
    anchor_tolerance_seconds: float = 0.150
    tail_tolerance_seconds: float = 0.250
    semantic_recovery_window_seconds: float = 2.500
    verifier_hint_tolerance_seconds: float = 0.500
    verifier_hint_shift_seconds: float = 0.350
    word_envelope_gap_seconds: float = 0.150


def _token(value: object) -> str:
    match = _WORD_RE.search(str(value or ""))
    return match.group(0).casefold() if match else ""


def _runs(signal: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    index = 0
    while index < len(signal):
        if not bool(signal[index]):
            index += 1
            continue
        end = index + 1
        while end < len(signal) and bool(signal[end]):
            end += 1
        result.append((index, end))
        index = end
    return result


def build_bilateral_components(
    raw_activity: np.ndarray,
    rnn_activity: np.ndarray,
    *,
    config: WordSnapConfig = WordSnapConfig(),
) -> list[ActivityComponent]:
    """Return raw/RNNoise-paired components on the raw timeline."""
    length = min(len(raw_activity), len(rnn_activity))
    raw = np.asarray(raw_activity[:length], dtype=bool)
    rnn = np.asarray(rnn_activity[:length], dtype=bool)
    bilateral_runs = _runs(raw & rnn)
    raw_runs = _runs(raw)
    rnn_runs = _runs(rnn)
    minimum_frames = max(1, math.ceil(config.minimum_bilateral_seconds / config.hop_seconds))

    expanded: list[tuple[int, int, int, int]] = []
    for both_start, both_end in bilateral_runs:
        if both_end - both_start < minimum_frames:
            continue
        supporting_raw = [
            run for run in raw_runs if run[1] > both_start and run[0] < both_end
        ]
        supporting_rnn = [
            run for run in rnn_runs if run[1] > both_start and run[0] < both_end
        ]
        if not supporting_raw or not supporting_rnn:
            continue
        start = min(run[0] for run in supporting_raw + supporting_rnn)
        end = max(run[1] for run in supporting_raw + supporting_rnn)
        weak_extension_frames = math.ceil(
            config.weak_raw_extension_seconds / config.hop_seconds
        )
        start = max(start, both_start - weak_extension_frames)
        end = min(end, both_end + weak_extension_frames)
        expanded.append((start, end, both_start, both_end))

    merge_frames = math.floor(config.component_merge_gap_seconds / config.hop_seconds)
    merged: list[list[int]] = []
    for start, end, both_start, both_end in expanded:
        if merged and start - merged[-1][1] <= merge_frames:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] = min(merged[-1][2], both_start)
            merged[-1][3] = max(merged[-1][3], both_end)
        else:
            merged.append([start, end, both_start, both_end])

    return [
        ActivityComponent(
            index=index,
            start=round(start * config.hop_seconds, 6),
            end=round(end * config.hop_seconds, 6),
            bilateral_start=round(both_start * config.hop_seconds, 6),
            bilateral_end=round(both_end * config.hop_seconds, 6),
        )
        for index, (start, end, both_start, both_end) in enumerate(merged)
    ]


def _overlap(start: float, end: float, component: ActivityComponent) -> float:
    return max(0.0, min(end, component.end) - max(start, component.start))


def _interval_overlap(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> float:
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _select_component(
    start: float,
    end: float,
    components: list[ActivityComponent],
    *,
    config: WordSnapConfig,
    tolerance_seconds: float | None = None,
) -> int | None:
    tolerance = (
        config.anchor_tolerance_seconds
        if tolerance_seconds is None
        else tolerance_seconds
    )
    candidates: list[tuple[int, float, float, int]] = []
    for component in components:
        overlap = _overlap(start, end, component)
        if overlap > 0.0:
            distance = 0.0
        elif end <= component.start:
            distance = component.start - end
        else:
            distance = start - component.end
        if overlap <= 0.0 and distance > tolerance:
            continue
        contains_start = int(component.start <= start <= component.end)
        candidates.append((contains_start, overlap, -distance, -component.index))
    if not candidates:
        return None
    return -max(candidates)[3]


def _speaker_supported(
    component: ActivityComponent, diarization: Iterable[Mapping[str, Any]]
) -> bool:
    return any(
        _interval_overlap(
            component.start,
            component.end,
            float(turn["start"]),
            float(turn["end"]),
        )
        > 0.0
        for turn in diarization
    )


def refine_word_timestamps(
    words: list[dict[str, Any]],
    components: list[ActivityComponent],
    *,
    diarization: Iterable[Mapping[str, Any]],
    semantic_recoveries: Iterable[Mapping[str, Any]] = (),
    verifier_word_hints: Iterable[Mapping[str, Any]] = (),
    config: WordSnapConfig = WordSnapConfig(),
) -> dict[str, Any]:
    """Refine word dictionaries in place and return deterministic evidence."""
    assignments: list[int | None] = []
    assignment_reason = ["forced_anchor"] * len(words)
    for word in words:
        forced_start = float(word["start"])
        forced_end = float(word["end"])
        word.setdefault("forced_alignment_start", forced_start)
        word.setdefault("forced_alignment_end", forced_end)
        assignments.append(
            _select_component(forced_start, forced_end, components, config=config)
        )

    hint_by_index: dict[int, Mapping[str, Any]] = {}
    for hint in verifier_word_hints:
        aligned_word_index = int(hint.get("aligned_word_index", -1))
        if aligned_word_index < 0 or aligned_word_index >= len(words):
            raise RuntimeError("verifier word hint index is outside the aligned words")
        if aligned_word_index in hint_by_index:
            raise RuntimeError("duplicate verifier word hint index")
        hint_start = float(hint.get("start", math.nan))
        hint_end = float(hint.get("end", math.nan))
        if not math.isfinite(hint_start) or not math.isfinite(hint_end) or hint_end <= hint_start:
            raise RuntimeError("verifier word hint interval is invalid")
        hint_by_index[aligned_word_index] = hint
    if hint_by_index and len(hint_by_index) != len(words):
        raise RuntimeError("verifier word hints do not cover every aligned word")

    for index, word in enumerate(words):
        hint = hint_by_index.get(index)
        if hint is None:
            continue
        hint_start = float(hint["start"])
        hint_end = float(hint["end"])
        hint_component = _select_component(
            hint_start,
            hint_end,
            components,
            config=config,
            tolerance_seconds=config.verifier_hint_tolerance_seconds,
        )
        if hint_component is None:
            continue
        forced_start = float(word["forced_alignment_start"])
        forced_end = float(word["forced_alignment_end"])
        forced_midpoint = (forced_start + forced_end) / 2.0
        hint_midpoint = (hint_start + hint_end) / 2.0
        current_component = assignments[index]
        if (
            current_component != hint_component
            and abs(forced_midpoint - hint_midpoint)
            >= config.verifier_hint_shift_seconds
        ):
            assignments[index] = hint_component
            assignment_reason[index] = "verifier_hint_component_shift"
            continue
        forced_duration = forced_end - forced_start
        hint_duration = hint_end - hint_start
        component_duration = components[hint_component].duration
        if (
            current_component == hint_component
            and forced_duration - hint_duration >= config.verifier_hint_shift_seconds
            and component_duration <= max(2.0, hint_duration + 1.0)
        ):
            assignment_reason[index] = "verifier_hint_boundary"

    def attributed(excluding: int | None = None) -> set[int]:
        return {
            component_index
            for word_index, component_index in enumerate(assignments)
            if component_index is not None and word_index != excluding
        }

    recovery_evidence: list[dict[str, Any]] = []
    semantic_hint_intervals: dict[int, tuple[float, float]] = {}
    matched_recovery_word_indices: set[int] = set()
    for recovery in semantic_recoveries:
        if recovery.get("requires_component_recovery") is False:
            continue
        cue = str(recovery.get("cue") or "").casefold()
        verifier_midpoint = (
            float(recovery.get("verifier_start") or 0.0)
            + float(recovery.get("verifier_end") or 0.0)
        ) / 2.0
        matches = [
            index
            for index, word in enumerate(words)
            if index not in matched_recovery_word_indices
            and _token(word.get("word", word.get("text"))) == cue
        ]
        if not matches:
            continue
        cue_index = min(
            matches,
            key=lambda index: abs(
                (
                    float(words[index]["forced_alignment_start"])
                    + float(words[index]["forced_alignment_end"])
                )
                / 2.0
                - verifier_midpoint
            ),
        )
        matched_recovery_word_indices.add(cue_index)
        semantic_hint_intervals[cue_index] = (
            float(recovery.get("verifier_start") or 0.0),
            float(recovery.get("verifier_end") or 0.0),
        )
        current_component = assignments[cue_index]
        if current_component is None:
            continue
        previous_end = (
            float(words[cue_index - 1]["forced_alignment_end"])
            if cue_index > 0
            else max(0.0, components[current_component].start - config.semantic_recovery_window_seconds)
        )
        used = attributed(excluding=cue_index)
        candidates = [
            component
            for component in components
            if (component.index not in used or component.index == current_component)
            and component.end > previous_end - config.anchor_tolerance_seconds
            and abs(
                ((component.start + component.end) / 2.0) - verifier_midpoint
            )
            <= config.semantic_recovery_window_seconds
            and component.duration >= config.minimum_orphan_seconds
        ]
        if not candidates:
            continue
        recovered_component = min(
            candidates,
            key=lambda component: (
                abs(((component.start + component.end) / 2.0) - verifier_midpoint),
                abs(component.start - float(recovery.get("verifier_start") or 0.0)),
                component.index,
            ),
        ).index
        if recovered_component == current_component:
            continue
        assignments[cue_index] = recovered_component
        assignment_reason[cue_index] = "semantic_cue_component_shift"
        recovery_evidence.append(
            {
                "word_index": cue_index,
                "text": words[cue_index].get("word", words[cue_index].get("text")),
                "from_component": current_component,
                "to_component": recovered_component,
                "reason": "semantic_cue_component_shift",
            }
        )

        previous_component = recovered_component
        for following_index in range(cue_index + 1, min(len(words), cue_index + 5)):
            current = assignments[following_index]
            if current is None or current <= previous_component:
                break
            used = attributed(excluding=following_index)
            missing = [
                component_index
                for component_index in range(previous_component + 1, current)
                if component_index not in used
                and components[component_index].duration >= config.minimum_orphan_seconds
            ]
            if not missing:
                if (
                    following_index == cue_index + 1
                    and current == previous_component + 1
                    and components[current].start - components[previous_component].end
                    >= config.robust_silence_seconds
                ):
                    assignment_reason[following_index] = "semantic_sequence_onset_shift"
                    recovery_evidence.append(
                        {
                            "word_index": following_index,
                            "text": words[following_index].get("word", words[following_index].get("text")),
                            "from_component": current,
                            "to_component": current,
                            "reason": "semantic_sequence_onset_shift",
                        }
                    )
                break
            shifted = missing[0]
            assignments[following_index] = shifted
            assignment_reason[following_index] = "semantic_sequence_component_shift"
            recovery_evidence.append(
                {
                    "word_index": following_index,
                    "text": words[following_index].get("word", words[following_index].get("text")),
                    "from_component": current,
                    "to_component": shifted,
                    "reason": "semantic_sequence_component_shift",
                }
            )
            previous_component = shifted

    component_spans: list[set[int]] = [
        ({component_index} if component_index is not None else set())
        for component_index in assignments
    ]
    component_owners: dict[int, set[int]] = {}
    for word_index, component_index in enumerate(assignments):
        if component_index is not None:
            component_owners.setdefault(component_index, set()).add(word_index)
    claimed_components = set(component_owners)
    for component in components:
        if component.index in claimed_components:
            continue
        candidates: list[tuple[float, float, int]] = []
        for word_index, word in enumerate(words):
            if assignments[word_index] is None:
                continue
            intervals = [
                (
                    float(word["forced_alignment_start"]),
                    float(word["forced_alignment_end"]),
                )
            ]
            if word_index in semantic_hint_intervals:
                intervals.append(semantic_hint_intervals[word_index])
            overlap = max(
                _interval_overlap(start, end, component.start, component.end)
                for start, end in intervals
            )
            if overlap <= 0.0:
                continue
            midpoint_distance = min(
                abs(
                    ((start + end) / 2.0)
                    - ((component.start + component.end) / 2.0)
                )
                for start, end in intervals
            )
            candidates.append((overlap, -midpoint_distance, -word_index))
        if not candidates:
            continue
        _, _, negative_word_index = max(candidates)
        word_index = -negative_word_index
        anchor_index = assignments[word_index]
        if anchor_index is None:
            continue
        anchor_owners = component_owners.get(anchor_index, {word_index})
        if (
            component.index > anchor_index
            and word_index != max(anchor_owners)
        ) or (
            component.index < anchor_index
            and word_index != min(anchor_owners)
        ):
            # A component shared by several words may only be extended forward
            # by its last owner or backward by its first owner. Otherwise an
            # internal word would inherit the whole phrase boundary and move
            # backwards/forwards across its neighbours.
            continue
        lower = min(anchor_index, component.index)
        upper = max(anchor_index, component.index)
        if any(
            owner != word_index
            for component_index in range(lower + 1, upper)
            for owner in component_owners.get(component_index, ())
        ):
            continue
        current_span = component_spans[word_index]
        if component.index < min(current_span):
            envelope_gap = components[min(current_span)].start - component.end
        elif component.index > max(current_span):
            envelope_gap = component.start - components[max(current_span)].end
        else:
            envelope_gap = 0.0
        if envelope_gap > config.word_envelope_gap_seconds:
            # A long CTC interval may cross an omitted word. Only acoustically
            # connected components can form one lexical word's envelope; a
            # robust gap remains unattributed and triggers targeted recovery.
            continue
        component_spans[word_index].add(component.index)
        component_owners.setdefault(component.index, set()).add(word_index)
        if assignment_reason[word_index] == "forced_anchor":
            assignment_reason[word_index] = "acoustic_component_envelope"

    evidence: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        forced_start = float(word["forced_alignment_start"])
        forced_end = float(word["forced_alignment_end"])
        component_index = assignments[index]
        if component_index is None:
            blocking.append(
                {
                    "type": "word_without_bilateral_anchor",
                    "word_index": index,
                    "text": word.get("word", word.get("text")),
                    "start": forced_start,
                    "end": forced_end,
                }
            )
            continue
        span_indices = sorted(component_spans[index] or {component_index})
        first_component = components[span_indices[0]]
        last_component = components[span_indices[-1]]
        component = ActivityComponent(
            index=component_index,
            start=first_component.start,
            end=last_component.end,
            bilateral_start=min(
                components[item].bilateral_start for item in span_indices
            ),
            bilateral_end=max(
                components[item].bilateral_end for item in span_indices
            ),
        )
        start = forced_start
        end = forced_end
        reason = assignment_reason[index]

        if reason == "acoustic_component_envelope":
            first_owners = component_owners.get(span_indices[0], {index})
            last_owners = component_owners.get(span_indices[-1], {index})
            if index == min(first_owners):
                start = component.start
            if index == max(last_owners):
                end = component.end
        elif reason in {
            "semantic_cue_component_shift",
            "semantic_sequence_component_shift",
            "verifier_hint_component_shift",
            "verifier_hint_boundary",
        }:
            start, end = component.start, component.end
        else:
            if reason == "semantic_sequence_onset_shift":
                start = component.start
            forced_overlap = _overlap(forced_start, forced_end, component)
            if (
                reason != "semantic_sequence_onset_shift"
                and forced_start < component.start
                and component.start - forced_start <= config.tail_tolerance_seconds
            ):
                if forced_overlap < config.minimum_bilateral_seconds:
                    duration = max(config.minimum_bilateral_seconds, forced_end - forced_start)
                    start = component.start
                    end = min(component.end, start + duration)
                else:
                    start = component.start
            elif reason != "semantic_sequence_onset_shift" and component.start < forced_start:
                previous_end = float(words[index - 1].get("end", 0.0)) if index else -math.inf
                if (
                    forced_start - component.start <= config.tail_tolerance_seconds
                    and previous_end <= component.start - config.robust_silence_seconds
                ):
                    start = component.start

            following_index = span_indices[-1] + 1
            following_component = (
                components[following_index]
                if following_index < len(components)
                else None
            )
            robust_silence_after = (
                following_component is None
                or following_component.start - component.end >= config.robust_silence_seconds
            )
            next_word_start = (
                float(words[index + 1]["forced_alignment_start"])
                if index + 1 < len(words)
                else math.inf
            )
            next_word_is_later = next_word_start - component.end >= config.robust_silence_seconds
            if robust_silence_after and component.end < forced_end - config.robust_silence_seconds:
                end = component.end
            elif (
                robust_silence_after
                and next_word_is_later
                and 0.0 < component.end - forced_end <= config.tail_tolerance_seconds
            ):
                end = component.end

        if end <= start:
            blocking.append(
                {
                    "type": "invalid_acoustic_interval",
                    "word_index": index,
                    "text": word.get("word", word.get("text")),
                    "start": start,
                    "end": end,
                }
            )
            continue
        word["start"] = round(start, 6)
        word["end"] = round(end, 6)
        word["timing_source"] = (
            "forced_alignment_acoustic" if (start, end) != (forced_start, forced_end) else "forced_alignment"
        )
        if (start, end) != (forced_start, forced_end):
            evidence.append(
                {
                    "word_index": index,
                    "text": word.get("word", word.get("text")),
                    "forced": {"start": forced_start, "end": forced_end},
                    "acoustic": {"start": word["start"], "end": word["end"]},
                    "component": asdict(component),
                    "component_indices": span_indices,
                    "reason": reason,
                }
            )

    attributed_components: set[int] = set()
    for word in words:
        word_start = float(word["start"])
        word_end = float(word["end"])
        for component in components:
            if _overlap(word_start, word_end, component) >= config.minimum_bilateral_seconds:
                attributed_components.add(component.index)

    diarization_records = list(diarization)
    orphans = [
        {
            "type": "unattributed_bilateral_activity",
            "component": asdict(component),
        }
        for component in components
        if component.index not in attributed_components
        and component.duration >= config.minimum_orphan_seconds
        and _speaker_supported(component, diarization_records)
    ]
    blocking.extend(orphans)
    blocking.extend(
        item
        for item in recovery_evidence
        if item["reason"] == "semantic_cue_component_shift"
        and components[item["to_component"]].duration < config.minimum_orphan_seconds
    )
    return {
        "status": "review" if blocking else "pass",
        "blocking_outlier_count": len(blocking),
        "blocking_outliers": blocking,
        "adjusted_word_count": len(evidence),
        "evidence": evidence,
        "semantic_recovery_evidence": recovery_evidence,
        "component_count": len(components),
        "parameters": asdict(config),
    }


def _flatten_word_references(aligned: Mapping[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in aligned.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words", []):
            if isinstance(word, dict) and word.get("start") is not None and word.get("end") is not None:
                words.append(word)
    return words


def refine_aligned_result(
    source_path: Path,
    aligned: dict[str, Any],
    *,
    diarization: Iterable[Mapping[str, Any]],
    semantic_verification: Mapping[str, Any] | None,
    analysis_dir: Path,
    config: WordSnapConfig = WordSnapConfig(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run cached raw/RNNoise analysis and refine an aligned worker result."""
    source = source_path.resolve(strict=True)
    model_path = Path(__file__).resolve().parent / "models" / "cb.rnnn"
    verify_model_hash(model_path)
    if not is_ffmpeg_arnndn_available():
        raise RuntimeError("FFmpeg arnndn is required for transcript acoustic validation")

    words = _flatten_word_references(aligned)
    if not words:
        raise RuntimeError("WhisperX returned no aligned words")
    analysis_dir.mkdir(parents=True, exist_ok=True)
    provisional_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="aligned_words_",
            suffix=".json",
            dir=analysis_dir,
            delete=False,
        ) as handle:
            json.dump({"words": words}, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            provisional_path = Path(handle.name)

        ffmpeg_identity = get_ffmpeg_version()
        fingerprint = get_source_fingerprint(
            source,
            EXPECTED_MODEL_HASH,
            DEFAULT_VAD_PARAMS,
            ffmpeg_identity,
            provisional_path,
        )
        raw_pcm, rnn_pcm = extract_analysis_audio(
            source,
            analysis_dir,
            source.stem,
            fingerprint,
            model_path,
            provisional_path,
        )
        raw_activity, rnn_activity, lag, *_ = get_combined_activity(
            raw_pcm, rnn_pcm, DEFAULT_VAD_PARAMS, words
        )
    finally:
        if provisional_path is not None and provisional_path.exists():
            provisional_path.unlink()

    components = build_bilateral_components(raw_activity, rnn_activity, config=config)
    report = refine_word_timestamps(
        words,
        components,
        diarization=diarization,
        semantic_recoveries=(semantic_verification or {}).get("recoveries", []),
        config=config,
    )
    report.update(
        {
            "source_sha256": aligned.get("source_sha256"),
            "analysis_fingerprint": fingerprint,
            "rnnoise_model_sha256": EXPECTED_MODEL_HASH,
            "rnnoise_lag_ms": lag * DEFAULT_VAD_PARAMS["hop_ms"],
            "ffmpeg": ffmpeg_identity,
        }
    )

    for segment in aligned.get("segments", []):
        segment_words = segment.get("words", []) if isinstance(segment, dict) else []
        if segment_words:
            segment["start"] = min(float(word["start"]) for word in segment_words)
            segment["end"] = max(float(word["end"]) for word in segment_words)
    return aligned, report


__all__ = [
    "ActivityComponent",
    "WordSnapConfig",
    "build_bilateral_components",
    "refine_aligned_result",
    "refine_word_timestamps",
]
