"""Refine EDL boundaries using transcript anchors and raw/RNNoise voice activity.

CLI usage:
    python helpers/refine_edl_boundaries.py <edl> --transcripts <dir> --report <path>
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path to support direct execution as a script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import copy
import hashlib
import json
import os
import subprocess
from fractions import Fraction
from typing import Any

import numpy as np

from helpers.timing import format_fps_fraction, parse_fps_fraction, time_to_frame, frame_to_time
from helpers.internal_silence import (
    INTERNAL_SILENCE_OVERRIDE_FIELD,
    INTERNAL_SILENCE_SPLIT_POLICY,
    INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS,
    compute_internal_silence_event_id,
    decimal_timestamp,
    evaluate_internal_silence_contract,
)
from helpers.audio_analysis import (
    EXPECTED_MODEL_HASH,
    get_current_denoiser_id,
    get_ffmpeg_version,
    is_ffmpeg_arnndn_available,
    verify_model_hash,
    check_vfr,
    get_source_fingerprint,
    extract_analysis_audio,
    get_combined_activity,
    DEFAULT_VAD_PARAMS,
    run_vad_hysteresis,
    load_pcm_data,
)




def load_words(transcripts_dir: Path, source_id: str) -> list[dict[str, Any]]:
    """Load and return valid word dictionaries from the transcript JSON file."""
    path = transcripts_dir / f"{source_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Error parsing transcript JSON for {source_id}: {e}")

    words = []
    for w in data.get("words", []):
        if w.get("type") != "word":
            continue
        if w.get("start") is None or w.get("end") is None:
            continue
        words.append(w)

    if not words:
        raise ValueError(f"No valid words found in transcript {path}")

    return words


def find_overlapping_words(
    words: list[dict[str, Any]],
    start: float,
    end: float
) -> list[dict[str, Any]]:
    """Return words that overlap with the edit interval [start, end]."""
    overlapping = []
    for w in words:
        w_start = float(w["start"])
        w_end = float(w["end"])
        if w_end > start and w_start < end:
            overlapping.append(w)
    return sorted(overlapping, key=lambda w: float(w["start"]))


def split_ranges_on_internal_silence(
    ranges: list[dict[str, Any]],
    words_by_source: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split selected lexical spans at every internal gap strictly over 300 ms.

    This transformation runs before acoustic boundary refinement.  Each child
    receives its own immutable lexical selection bounds, so the normal snapper
    subsequently resolves all four sides of the newly created jump cut.  A
    pause survives only through an explicit, reasoned per-gap override.
    """
    if not isinstance(ranges, list):
        raise ValueError("EDL ranges must be a list")
    expanded: list[dict[str, Any]] = []
    events_by_id: dict[str, dict[str, Any]] = {}
    event_occurrences: dict[str, int] = {}

    def register_event(event: object, label: str) -> None:
        if not isinstance(event, dict):
            raise ValueError(f"{label} audit_event must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"{label} audit_event has an invalid event_id")
        if (
            event.get("threshold_ms") != 300.0
            or event.get("comparison") != "strictly_greater_than"
            or event.get("action") not in {"split", "preserved_by_explicit_overrides"}
        ):
            raise ValueError(f"{label} audit_event is incomplete")
        expected_event_id = compute_internal_silence_event_id(
            event.get("source"),
            event.get("original_selection"),
            event.get("gaps"),
            event.get("occurrence"),
        )
        if event_id != expected_event_id:
            raise ValueError(f"{label} audit_event event_id is not reproducible")
        existing = events_by_id.get(event_id)
        if existing is not None and existing != event:
            raise ValueError(f"{label} audit_event conflicts with another lineage")
        events_by_id.setdefault(event_id, copy.deepcopy(event))

    # Persisted event snapshots make a second run reproduce the exact audit
    # event even when a child retains only an explicitly preserved gap.
    for range_index, range_data in enumerate(ranges):
        if not isinstance(range_data, dict):
            continue
        lineage = range_data.get("internal_silence_split")
        if not isinstance(lineage, dict):
            continue
        audit_event = lineage.get("audit_event")
        if audit_event is None:
            raise ValueError(
                f"range {range_index} internal_silence_split is missing audit_event"
            )
        if lineage.get("event_id") != (
            audit_event.get("event_id") if isinstance(audit_event, dict) else None
        ):
            raise ValueError(
                f"range {range_index} internal_silence_split event_id is stale"
            )
        register_event(audit_event, f"range {range_index} internal_silence_split")
        occurrence_key = compute_internal_silence_event_id(
            audit_event["source"],
            audit_event["original_selection"],
            audit_event["gaps"],
            0,
        )
        event_occurrences[occurrence_key] = max(
            event_occurrences.get(occurrence_key, 0),
            int(audit_event["occurrence"]) + 1,
        )

    for parent_index, range_data in enumerate(ranges):
        if not isinstance(range_data, dict):
            raise ValueError(f"range {parent_index} must be an object")
        source_id = range_data.get("source")
        if not isinstance(source_id, str) or source_id not in words_by_source:
            raise ValueError(f"range {parent_index} has an invalid source")

        try:
            original_start = decimal_timestamp(
                range_data.get("original_start", range_data["start"]),
                f"range {parent_index} original_start",
            )
            original_end = decimal_timestamp(
                range_data.get("original_end", range_data["end"]),
                f"range {parent_index} original_end",
            )
        except KeyError as exc:
            raise ValueError(f"range {parent_index} is missing start/end") from exc
        if original_end <= original_start:
            raise ValueError(f"range {parent_index} must have end greater than start")

        anchors = find_overlapping_words(
            words_by_source[source_id], float(original_start), float(original_end)
        )
        canonical_words = sorted(
            words_by_source[source_id], key=lambda word: float(word["start"])
        )
        canonical_index_by_identity = {
            id(word): index for index, word in enumerate(canonical_words)
        }
        first_word_index = canonical_index_by_identity[id(anchors[0])] if anchors else 0
        last_word_index = canonical_index_by_identity[id(anchors[-1])] if anchors else -1
        detected = (
            evaluate_internal_silence_contract(
                range_data, canonical_words, first_word_index, last_word_index
            )
            if anchors
            else []
        )
        split_gaps = [gap for gap in detected if not gap["preserve_override"]]
        split_after = [
            gap["left_word_index"] - first_word_index for gap in split_gaps
        ]

        if not detected:
            expanded.append(dict(range_data))
            continue

        existing_lineage = range_data.get("internal_silence_split")
        existing_audit_event = (
            existing_lineage.get("audit_event")
            if isinstance(existing_lineage, dict)
            else None
        )
        if isinstance(existing_audit_event, dict):
            persisted_preserved = [
                gap
                for gap in existing_audit_event.get("gaps", [])
                if isinstance(gap, dict)
                and gap.get("preserve_override") is True
                and first_word_index <= gap.get("left_word_index", -1)
                and gap.get("right_word_index", -1) <= last_word_index
            ]
            current_preserved = [
                gap for gap in detected if gap.get("preserve_override") is True
            ]
            if persisted_preserved != current_preserved:
                raise ValueError(
                    f"range {parent_index} preserved internal-silence override "
                    "does not match its persisted audit_event"
                )
        if existing_audit_event is not None and not split_gaps:
            expanded.append(dict(range_data))
            continue

        original_selection = {
            "start": float(original_start),
            "end": float(original_end),
        }
        occurrence_key = compute_internal_silence_event_id(
            source_id, original_selection, detected, 0
        )
        occurrence = event_occurrences.get(occurrence_key, 0)
        event_occurrences[occurrence_key] = occurrence + 1
        event_id = compute_internal_silence_event_id(
            source_id, original_selection, detected, occurrence
        )
        event = {
            "event_id": event_id,
            "parent_range_index": parent_index,
            "source": source_id,
            "original_selection": original_selection,
            "occurrence": occurrence,
            "threshold_ms": float(INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS * 1000),
            "comparison": "strictly_greater_than",
            "action": "split" if split_gaps else "preserved_by_explicit_overrides",
            "gaps": detected,
        }
        register_event(event, f"range {parent_index}")

        if not split_gaps:
            preserved = dict(range_data)
            preserved["internal_silence_split"] = {
                "policy": INTERNAL_SILENCE_SPLIT_POLICY,
                "event_id": event_id,
                "parent_range_index": parent_index,
                "segment_index": 0,
                "segment_count": 1,
                "audit_event": copy.deepcopy(event),
            }
            expanded.append(preserved)
            continue

        segment_bounds = [0] + [index + 1 for index in split_after] + [len(anchors)]
        segment_count = len(segment_bounds) - 1
        for segment_index, (anchor_start, anchor_stop) in enumerate(
            zip(segment_bounds, segment_bounds[1:])
        ):
            segment_words = anchors[anchor_start:anchor_stop]
            child = dict(range_data)
            child_constraints = dict(range_data.get("boundary_constraints", {}))
            # This is refiner-generated evidence tied to the old parent edge.
            child_constraints.pop("end", None)
            preserved_in_segment = [
                {
                    "left_word_index": gap["left_word_index"],
                    "left_word": gap["left_word"],
                    "right_word_index": gap["right_word_index"],
                    "right_word": gap["right_word"],
                    "reason": gap["override_reason"],
                }
                for gap in detected
                if gap["preserve_override"]
                and first_word_index + anchor_start <= gap["left_word_index"]
                and gap["right_word_index"] < first_word_index + anchor_stop
            ]
            if preserved_in_segment:
                child_constraints[INTERNAL_SILENCE_OVERRIDE_FIELD] = preserved_in_segment
            else:
                child_constraints.pop(INTERNAL_SILENCE_OVERRIDE_FIELD, None)
            if child_constraints:
                child["boundary_constraints"] = child_constraints
            else:
                child.pop("boundary_constraints", None)
            for stale_key in (
                "source_in_frame",
                "source_out_frame",
                "lexical_anchors",
                "review_status",
                "review_required",
            ):
                child.pop(stale_key, None)

            child_start = original_start if segment_index == 0 else decimal_timestamp(
                segment_words[0].get("start"),
                f"range {parent_index} split segment start",
            )
            # Keep half of the discarded gap available to the acoustic
            # refiner while staying away from both lexical components.
            # Ending exactly at the left word's ASR timestamp would provide
            # no tail budget and force a low-confidence clamp.
            if segment_index == segment_count - 1:
                child_end = original_end
            else:
                left_end = decimal_timestamp(
                    segment_words[-1].get("end"),
                    f"range {parent_index} split left word end",
                )
                right_start = decimal_timestamp(
                    anchors[anchor_stop].get("start"),
                    f"range {parent_index} split right word start",
                )
                child_end = left_end + (right_start - left_end) / 2
            child.update({
                "original_start": float(child_start),
                "original_end": float(child_end),
                "start": float(child_start),
                "end": float(child_end),
                "quote": " ".join(
                    str(word.get("text") or "").strip()
                    for word in segment_words
                    if str(word.get("text") or "").strip()
                ),
                "internal_silence_split": {
                    "policy": INTERNAL_SILENCE_SPLIT_POLICY,
                    "event_id": event_id,
                    "parent_range_index": parent_index,
                    "segment_index": segment_index,
                    "segment_count": segment_count,
                    "audit_event": copy.deepcopy(event),
                },
            })
            expanded.append(child)

    return expanded, list(events_by_id.values())


def calculate_sha256_of_string(content: str) -> str:
    """Return the SHA-256 hash of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_atomic(dest_path: Path, content: str) -> None:
    """Write content to a file atomically via temp file, flush, fsync, and os.replace."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # Replace replaces target on Windows if it exists
        os.replace(str(temp_path), str(dest_path))
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e


def is_cue_word(word_text: str) -> bool:
    """Return True if the word is a generic recording cue (e.g. corta, cortar)."""
    cleaned = word_text.strip().lower().rstrip(".,?!:;()")
    return cleaned in {"corta", "cortar"}


def get_local_metrics(
    rms_raw: np.ndarray,
    rms_rnn: np.ndarray,
    nf_raw: np.ndarray,
    start_time: float,
    end_time: float,
    exclude_ranges: list[tuple[float, float]] | None = None
) -> tuple[float | None, float | None]:
    """Calculate the average SNR and correlation in a local time window, excluding specified time ranges."""
    start_idx = max(0, int(start_time * 200))
    end_idx = max(0, min(len(rms_raw), int(end_time * 200)))
    if start_idx >= end_idx:
        return None, None

    # Create mask of indices to include
    mask = np.ones(end_idx - start_idx, dtype=bool)
    if exclude_ranges:
        for ex_start, ex_end in exclude_ranges:
            ex_start_idx = max(0, int(ex_start * 200))
            ex_end_idx = max(0, min(len(rms_raw), int(ex_end * 200)))
            # Translate to relative indices in slice
            rel_start = max(0, ex_start_idx - start_idx)
            rel_end = min(end_idx - start_idx, ex_end_idx - start_idx)
            if rel_start < rel_end:
                mask[rel_start:rel_end] = False

    slice_raw = rms_raw[start_idx:end_idx][mask]
    slice_rnn = rms_rnn[start_idx:end_idx][mask]
    slice_nf = nf_raw[start_idx:end_idx][mask]

    corr_val = None
    if len(slice_raw) > 5 and np.std(slice_raw) > 1e-4 and np.std(slice_rnn) > 1e-4:
        c = np.corrcoef(slice_raw, slice_rnn)[0, 1]
        if np.isfinite(c):
            corr_val = float(c)

    snr_val = None
    if len(slice_raw) > 0:
        s_val = float(np.mean(slice_raw) - np.mean(slice_nf))
        if np.isfinite(s_val):
            snr_val = s_val

    return snr_val, corr_val


def is_connected_crossing(activity_sig: np.ndarray, T: float) -> bool:
    """Return True if T falls strictly inside a single contiguous active VAD component (activity before and after)."""
    n = len(activity_sig)
    in_comp = False
    start_idx = 0
    for i in range(n):
        if activity_sig[i]:
            if not in_comp:
                start_idx = i
                in_comp = True
        else:
            if in_comp:
                c_start = start_idx * 0.005
                c_end = i * 0.005
                if c_start < T and c_end > T:
                    return True
                in_comp = False
    if in_comp:
        c_start = start_idx * 0.005
        c_end = n * 0.005
        if c_start < T and c_end > T:
            return True
    return False


def is_anchor_connected_crossing(
    activity_sig: np.ndarray,
    boundary_time: float,
    anchor_start: float,
    anchor_end: float,
) -> bool:
    """Return True only when the boundary crosses the anchor's VAD component.

    A disconnected breath or noise transient after the selected word must not
    veto an otherwise safe lexical cut.  Render-level audio QC still inspects
    the resulting join for a real discontinuity.
    """
    component_start: int | None = None
    for index in range(len(activity_sig) + 1):
        active = index < len(activity_sig) and bool(activity_sig[index])
        if active and component_start is None:
            component_start = index
        elif not active and component_start is not None:
            start_time = component_start * 0.005
            end_time = index * 0.005
            if (
                start_time < boundary_time < end_time
                and start_time < anchor_end
                and end_time > anchor_start
            ):
                return True
            component_start = None
    return False


def first_disconnected_component_before(
    activity_sig: np.ndarray,
    anchor_start: float,
    anchor_end: float,
    limit_time: float,
) -> tuple[float, float] | None:
    """Find post-anchor activity that would otherwise remain in the range."""
    component_start: int | None = None
    for index in range(len(activity_sig) + 1):
        active = index < len(activity_sig) and bool(activity_sig[index])
        if active and component_start is None:
            component_start = index
        elif not active and component_start is not None:
            start_time = component_start * 0.005
            end_time = index * 0.005
            overlaps_anchor = start_time < anchor_end and end_time > anchor_start
            if not overlaps_anchor and start_time >= anchor_end and start_time < limit_time:
                return start_time, end_time
            component_start = None
    return None


def get_pre_onset_attack_risk(
    rms_raw: np.ndarray,
    nf_raw: np.ndarray,
    word_start: float,
    selected_onset: float,
) -> tuple[bool, dict[str, Any]]:
    """Detect weak raw activity that a later VAD onset would cut away.

    Only RMS windows fully before ``selected_onset`` participate.  A single
    strong transient is enough to veto the trim; lower-level activity must be
    connected for at least 10 ms so isolated noise does not masquerade as a
    lexical attack.
    """
    hop_s = float(DEFAULT_VAD_PARAMS["hop_ms"]) / 1000.0
    window_s = float(DEFAULT_VAD_PARAMS["window_ms"]) / 1000.0
    low_threshold_db = float(DEFAULT_VAD_PARAMS["threshold_low_db"])
    high_threshold_db = float(DEFAULT_VAD_PARAMS["threshold_high_db"])
    min_connected_bins = max(1, int(np.ceil(0.010 / hop_s)))

    start_idx = max(0, int(np.floor(word_start / hop_s)))
    last_full_window_idx = int(np.floor((selected_onset - window_s) / hop_s))
    end_idx = min(len(rms_raw), len(nf_raw), last_full_window_idx + 1)

    diagnostics: dict[str, Any] = {
        "checked": selected_onset > word_start and end_idx > start_idx,
        "window": [word_start, selected_onset],
        "rms_bin_range": [start_idx, max(start_idx, end_idx)],
        "low_threshold_db": low_threshold_db,
        "high_threshold_db": high_threshold_db,
        "minimum_connected_ms": min_connected_bins * float(DEFAULT_VAD_PARAMS["hop_ms"]),
        "peak_excess_db": None,
        "longest_low_activity_ms": 0.0,
        "strong_transient": False,
    }
    if not diagnostics["checked"]:
        return False, diagnostics

    excess = rms_raw[start_idx:end_idx] - nf_raw[start_idx:end_idx]
    finite = excess[np.isfinite(excess)]
    if finite.size == 0:
        return False, diagnostics

    peak_excess_db = float(np.max(finite))
    low_active = np.isfinite(excess) & (excess >= low_threshold_db)
    longest_run = 0
    current_run = 0
    for is_active in low_active:
        if bool(is_active):
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0

    strong_transient = peak_excess_db >= high_threshold_db
    connected_attack = longest_run >= min_connected_bins
    diagnostics.update({
        "peak_excess_db": peak_excess_db,
        "longest_low_activity_ms": longest_run * float(DEFAULT_VAD_PARAMS["hop_ms"]),
        "strong_transient": strong_transient,
    })
    return bool(strong_transient or connected_attack), diagnostics


def get_scored_refined_bound(
    activity_sig: np.ndarray,
    w_start: float,
    w_end: float,
    prev_end: float | None,
    next_start: float | None
) -> tuple[float, float, bool, list[dict[str, Any]]]:
    """Find and score contiguous active VAD components overlapping the lexical word [w_start, w_end]."""
    # We use a search tolerance of 40ms to handle transcript alignment uncertainty
    pad_s = 0.04
    w_start_padded = w_start - pad_s
    w_end_padded = w_end + pad_s

    # Find all contiguous active components
    components = []
    n = len(activity_sig)
    in_comp = False
    start_idx = 0
    for i in range(n):
        if activity_sig[i]:
            if not in_comp:
                start_idx = i
                in_comp = True
        else:
            if in_comp:
                components.append((start_idx, i))
                in_comp = False
    if in_comp:
        components.append((start_idx, n))

    candidates: list[dict[str, Any]] = []
    word_len = max(0.001, w_end - w_start)

    # Narrow down components overlapping the padded word boundaries
    for c_start_idx, c_end_idx in components:
        c_start = c_start_idx * 0.005
        c_end = c_end_idx * 0.005
        if c_end > w_start_padded and c_start < w_end_padded:
            overlap = max(0.0, min(c_end, w_end) - max(c_start, w_start))
            duration = c_end - c_start
            coverage = overlap / word_len
            dist_to_start = abs(c_start - w_start)

            # A component found only in the uncertainty padding is useful
            # diagnostic evidence, but it cannot anchor a lexical boundary.
            rejection_reasons: list[str] = []
            if overlap <= 0.0:
                rejection_reasons.append("no_lexical_overlap")

            # Require meaningful lexical support without rejecting a real,
            # short plosive/sibilant solely because of absolute duration.
            support_ok = coverage >= 0.20 or (
                overlap >= 0.040 and dist_to_start <= 0.020
            )
            if overlap > 0.0 and not support_ok:
                rejection_reasons.append("weak_lexical_support")

            score = (coverage * 100.0) - (dist_to_start * 20.0)

            # Collision penalty
            collision_penalty = 0.0
            if prev_end is not None and c_start < prev_end:
                collision_penalty -= 100.0
            if next_start is not None and c_end > next_start:
                collision_penalty -= 100.0

            score += collision_penalty

            candidates.append({
                "start": c_start,
                "end": c_end,
                "score": score,
                "overlap": overlap,
                "coverage": coverage,
                "duration": duration,
                "eligible": not rejection_reasons,
                "is_selected": False,
                "rejection_reasons": rejection_reasons,
                "reasons": (
                    f"overlap={overlap:.3f}, coverage={coverage:.3f}, "
                    f"dur={duration:.3f}, coll_pen={collision_penalty:.1f}"
                ),
            })

    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    if not eligible:
        return w_start, w_end, False, candidates

    # Sort candidates by score descending
    eligible.sort(key=lambda x: (x["score"], x["coverage"], -x["start"]), reverse=True)
    best_cand = eligible[0]
    best_cand["is_selected"] = True

    return best_cand["start"], best_cand["end"], True, candidates


def find_prelexical_transient_bridge(
    candidates: list[dict[str, Any]],
    word_start: float,
    prev_end: float | None,
    max_gap_s: float = 0.060,
) -> dict[str, Any] | None:
    """Return a raw transient that plausibly carries the selected attack.

    A plosive can finish just before the ASR word timestamp and therefore be
    rejected by the ordinary lexical-overlap scorer.  The detector contract
    explicitly protects a transient followed by speech within 60 ms.  It may
    be used only when it starts after the preceding transcript word.
    """
    bridged = []
    for candidate in candidates:
        start = float(candidate["start"])
        end = float(candidate["end"])
        gap = word_start - end
        if (
            start < word_start
            and end <= word_start
            and 0.0 <= gap <= max_gap_s
            and 0.010 <= end - start <= 0.120
            and (prev_end is None or start >= prev_end)
        ):
            bridged.append((gap, start, candidate))
    if not bridged:
        return None
    return min(bridged, key=lambda item: (item[0], item[1]))[2]


def is_exact_one_frame_disconnected_tail_constraint(
    constraint: object,
) -> bool:
    """Return whether the only permitted shortened tail is fully evidenced."""
    return (
        isinstance(constraint, dict)
        and constraint.get("reason") == "disconnected_post_word_activity"
        and type(constraint.get("required_tail_frames")) is int
        and constraint.get("required_tail_frames") == 2
        and type(constraint.get("available_tail_frames")) is int
        and constraint.get("available_tail_frames") == 1
    )


def has_valid_tail_budget(
    available_tail_frames: object,
    *,
    allow_one_frame_exception: bool = False,
) -> bool:
    """Require two frames, except for the audited disconnected one-frame case."""
    return (
        type(available_tail_frames) is int
        and (
            available_tail_frames >= 2
            or (allow_one_frame_exception and available_tail_frames == 1)
        )
    )


def ensure_immutable_backup(
    backup_path: Path,
    input_raw_bytes: bytes,
    input_hash: str,
) -> None:
    """Create and sync a content-addressed backup, or verify its exact bytes."""
    try:
        with open(backup_path, "xb") as backup_file:
            backup_file.write(input_raw_bytes)
            backup_file.flush()
            os.fsync(backup_file.fileno())
    except FileExistsError:
        pass

    try:
        backup_bytes = backup_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"cannot read immutable EDL backup: {exc}") from exc
    backup_hash = hashlib.sha256(backup_bytes).hexdigest()
    if backup_hash != input_hash or backup_bytes != input_raw_bytes:
        raise RuntimeError(
            "immutable EDL backup does not match the current input bytes"
        )




def main() -> None:
    parser = argparse.ArgumentParser(description="Refine EDL boundaries using transcripts and waveforms.")
    parser.add_argument("edl", type=Path, help="Path to input edl.json")
    parser.add_argument("--transcripts", type=Path, required=True, help="Directory containing transcripts")
    parser.add_argument("--report", type=Path, required=True, help="Path to write the refinement JSON report")
    args = parser.parse_args()

    # Preflight Check 1: Input EDL existence
    edl_path = args.edl.resolve()
    if not edl_path.exists():
        print(f"Fatal Error: EDL file not found at {edl_path}", file=sys.stderr)
        sys.exit(1)

    try:
        # Read the raw contents of EDL before loading to calculate exact input hash
        input_raw_bytes = edl_path.read_bytes()
        input_raw_content = input_raw_bytes.decode("utf-8")
        input_hash = hashlib.sha256(input_raw_bytes).hexdigest()
        edl = json.loads(input_raw_content)
    except Exception as e:
        print(f"Fatal Error: Cannot load EDL JSON: {e}", file=sys.stderr)
        sys.exit(1)

    edit_dir = edl_path.parent
    model_path = Path(__file__).parent / "models" / "cb.rnnn"

    # Preflight Check 2: Model hash verification
    try:
        verify_model_hash(model_path)
    except Exception as e:
        print(f"Fatal Error: Model preflight failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Preflight Check 3: FFmpeg availability and arnndn filter
    try:
        ffmpeg_id = get_ffmpeg_version()
    except Exception as e:
        print(f"Fatal Error: FFmpeg not available: {e}", file=sys.stderr)
        sys.exit(1)

    if not is_ffmpeg_arnndn_available():
        print("Fatal Error: FFmpeg 'arnndn' filter is unavailable.", file=sys.stderr)
        sys.exit(1)

    # Resolve source media paths and check existence, VFR, and mixed FPS
    sources = edl.get("sources", {})
    resolved_sources = {}
    fps_set = set()

    for source_id, rel_path in sources.items():
        src_path = Path(rel_path)
        if not src_path.is_absolute():
            src_path = (edit_dir / src_path).resolve()

        # Preflight Check 4: Source media exists
        if not src_path.exists():
            print(f"Fatal Error: Source media missing for {source_id}: {src_path}", file=sys.stderr)
            sys.exit(1)

        resolved_sources[source_id] = src_path

        # Preflight Check 5: Source is not VFR
        try:
            if check_vfr(src_path):
                print(f"Fatal Error: Source {source_id} has Variable Frame Rate (VFR) which is unsupported.", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Fatal Error: VFR check failed for {source_id}: {e}", file=sys.stderr)
            sys.exit(1)

        # Preflight Check 6: Extract FPS and check for mixed rational FPS
        cmd_fps = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "json", str(src_path)
        ]
        try:
            out_fps = subprocess.check_output(cmd_fps, text=True)
            data_fps = json.loads(out_fps)
            streams = data_fps.get("streams", [])
            if streams:
                r_fps = streams[0].get("r_frame_rate")
                if r_fps and r_fps != "0/0":
                    fps_set.add(parse_fps_fraction(r_fps))
        except Exception as e:
            print(f"Fatal Error: Failed to probe FPS for {source_id}: {e}", file=sys.stderr)
            sys.exit(1)

    if len(fps_set) > 1:
        print(f"Fatal Error: Mixed rational FPS detected: {fps_set}", file=sys.stderr)
        sys.exit(1)

    # Establish sequence frame rate
    if len(fps_set) == 1:
        fps = list(fps_set)[0]
    else:
        # Fallback to EDL metadata sequence_fps
        seq_fps = edl.get("metadata", {}).get("sequence_fps")
        if seq_fps:
            try:
                fps = parse_fps_fraction(seq_fps)
            except Exception as e:
                print(f"Fatal Error: Invalid sequence_fps metadata: {seq_fps} ({e})", file=sys.stderr)
                sys.exit(1)
        else:
            print("Fatal Error: No frame rate found in sources or EDL metadata.", file=sys.stderr)
            sys.exit(1)

    # Preflight Check 7: Transcript availability & anchors
    transcripts_dir = args.transcripts.resolve()
    if not transcripts_dir.exists():
        print(f"Fatal Error: Transcripts directory does not exist: {transcripts_dir}", file=sys.stderr)
        sys.exit(1)

    words_by_source = {}
    for source_id in resolved_sources:
        try:
            words_by_source[source_id] = load_words(transcripts_dir, source_id)
        except Exception as e:
            print(f"Fatal Error: Transcript anchor preflight failed for {source_id}: {e}", file=sys.stderr)
            sys.exit(1)

    # ------------------ Process Refinement ------------------
    try:
        ranges, internal_silence_events = split_ranges_on_internal_silence(
            edl.get("ranges", []), words_by_source
        )
    except Exception as e:
        print(f"Fatal Error: Internal silence split preflight failed: {e}", file=sys.stderr)
        sys.exit(1)
    boundary_evidence = []
    source_fingerprints = {}
    has_low_confidence = False

    # Extract & VAD process each unique source used in the EDL
    activity_by_source = {}
    rnn_activity_by_source = {}
    lag_by_source = {}
    rms_raw_by_source = {}
    rms_rnn_by_source = {}
    nf_raw_by_source = {}
    nf_rnn_by_source = {}
    raw_samples_by_source = {}
    analysis_dir = edit_dir / "audio_analysis"

    for source_id, src_path in resolved_sources.items():
        transcript_path = transcripts_dir / f"{source_id}.json"

        # Fingerprint the analysis setup including transcript hash
        fingerprint = get_source_fingerprint(
            src_path,
            EXPECTED_MODEL_HASH,
            DEFAULT_VAD_PARAMS,
            ffmpeg_id,
            transcript_path
        )
        source_fingerprints[source_id] = fingerprint

        # Extract and apply RNNoise (uses cached pcm files if available)
        try:
            raw_pcm, rnn_pcm = extract_analysis_audio(
                src_path,
                analysis_dir,
                source_id,
                fingerprint,
                model_path,
                transcript_path
            )
            # Run VAD and align signals
            activity_raw, activity_rnn_aligned, lag, rms_raw, rms_rnn_aligned, nf_raw, nf_rnn_aligned = get_combined_activity(
                raw_pcm, rnn_pcm, DEFAULT_VAD_PARAMS, words_by_source[source_id]
            )
            activity_by_source[source_id] = activity_raw
            rnn_activity_by_source[source_id] = activity_rnn_aligned
            lag_by_source[source_id] = lag
            rms_raw_by_source[source_id] = rms_raw
            rms_rnn_by_source[source_id] = rms_rnn_aligned
            nf_raw_by_source[source_id] = nf_raw
            nf_rnn_by_source[source_id] = nf_rnn_aligned
            raw_samples_by_source[source_id] = load_pcm_data(raw_pcm)
        except Exception as e:
            print(f"Fatal Error: Audio analysis processing failed for {source_id}: {e}", file=sys.stderr)
            sys.exit(1)


    # Iterate over EDL ranges and refine boundaries
    updated_ranges = []
    for idx, r in enumerate(ranges):
        source_id = r["source"]

        # Idempotence: load or initialize original boundaries
        orig_start = float(r.get("original_start", r["start"]))
        orig_end = float(r.get("original_end", r["end"]))

        F_orig_in = time_to_frame(orig_start, fps, "round")
        F_orig_out = time_to_frame(orig_end, fps, "round")

        words = words_by_source[source_id]

        # Overlapping words (anchors)
        anchors = find_overlapping_words(words, orig_start, orig_end)
        if not anchors:
            # No-anchor case:
            start_conf = "low"
            end_conf = "low"
            final_in_frame = F_orig_in
            final_out_frame = F_orig_out
            final_start_s = orig_start
            final_end_s = orig_end
            review_required = True
            review_status = "low"
            has_low_confidence = True

            # Copy original range fields and update with refinement info
            r_new = dict(r)
            r_new.update({
                "original_start": orig_start,
                "original_end": orig_end,
                "start": final_start_s,
                "end": final_end_s,
                "source_in_frame": final_in_frame,
                "source_out_frame": final_out_frame,
                "lexical_anchors": None,
                "review_status": review_status,
                "review_required": review_required
            })
            updated_ranges.append(r_new)

            # Record boundary evidence
            boundary_evidence.append({
                "range_index": idx,
                "source": source_id,
                "original": {"start": orig_start, "end": orig_end, "in_frame": F_orig_in, "out_frame": F_orig_out},
                "anchor_words": None,
                "neighbor_words": None,
                "vad_refined": None,
                "clamped": None,
                "tail_frames": None,
                "snr_db": None,
                "correlation": None,
                "confidence": {"start": start_conf, "end": end_conf},
                "spread": None,
                "final_frames": {"in": final_in_frame, "out": final_out_frame},
                "final_times": {"start": final_start_s, "end": final_end_s}
            })
            continue

        first_word = anchors[0]
        last_word = anchors[-1]

        # Preceding / following rejected words in transcript sequence
        all_words_sorted = sorted(words, key=lambda w: float(w["start"]))

        first_word_idx = all_words_sorted.index(first_word)
        prev_word = all_words_sorted[first_word_idx - 1] if first_word_idx > 0 else None

        last_word_idx = all_words_sorted.index(last_word)
        next_word = all_words_sorted[last_word_idx + 1] if last_word_idx < len(all_words_sorted) - 1 else None

        # Refine boundaries using the activities
        activity_raw = activity_by_source[source_id]
        activity_rnn = rnn_activity_by_source[source_id]

        rms_raw = rms_raw_by_source[source_id]
        rms_rnn = rms_rnn_by_source[source_id]
        nf_raw = nf_raw_by_source[source_id]
        nf_rnn = nf_rnn_by_source[source_id]

        # Correlation check in the EDL range
        start_frame_vad = max(0, min(len(rms_raw), int(orig_start * 200)))
        end_frame_vad = max(0, min(len(rms_raw), int(orig_end * 200)))
        slice_raw = rms_raw[start_frame_vad:end_frame_vad]
        slice_rnn = rms_rnn[start_frame_vad:end_frame_vad]
        slice_nf = nf_raw[start_frame_vad:end_frame_vad]

        corr_val = None
        if len(slice_raw) > 5 and np.std(slice_raw) > 1e-4 and np.std(slice_rnn) > 1e-4:
            c = np.corrcoef(slice_raw, slice_rnn)[0, 1]
            if np.isfinite(c):
                corr_val = float(c)

        # SNR check
        snr_val = None
        if len(slice_raw) > 0:
            s_val = float(np.mean(slice_raw) - np.mean(slice_nf))
            if np.isfinite(s_val):
                snr_val = s_val

        prev_end = float(prev_word["end"]) if prev_word else None
        next_start = float(next_word["start"]) if next_word else None

        # Start side window: around first word start
        start_win_t1 = float(first_word["start"]) - 0.5
        start_win_t2 = float(first_word["end"]) + 0.5
        # End side window: around last word end
        end_win_t1 = float(last_word["start"]) - 0.5
        end_win_t2 = float(last_word["end"]) + 0.5

        # Compute exclusions for local metrics (exclude all other words in transcript)
        start_excludes = []
        for w in all_words_sorted:
            if w != first_word:
                start_excludes.append((float(w["start"]), float(w["end"])))

        end_excludes = []
        for w in all_words_sorted:
            if w != last_word:
                end_excludes.append((float(w["start"]), float(w["end"])))

        # Measure local metrics per side
        start_snr, start_corr = get_local_metrics(rms_raw, rms_rnn, nf_raw, start_win_t1, start_win_t2, start_excludes)
        end_snr, end_corr = get_local_metrics(rms_raw, rms_rnn, nf_raw, end_win_t1, end_win_t2, end_excludes)


        # Find refined start bound (from first word)
        comp_start_raw, comp_end_raw, has_start_raw, start_raw_candidates = get_scored_refined_bound(
            activity_raw, float(first_word["start"]), float(first_word["end"]), prev_end, next_start
        )
        comp_start_rnn, comp_end_rnn, has_start_rnn, start_rnn_candidates = get_scored_refined_bound(
            activity_rnn, float(first_word["start"]), float(first_word["end"]), prev_end, next_start
        )
        transient_bridge = find_prelexical_transient_bridge(
            start_raw_candidates,
            float(first_word["start"]),
            prev_end,
        )

        t_onset_raw = comp_start_raw if has_start_raw else float(first_word["start"])
        t_onset_rnn = comp_start_rnn if has_start_rnn else float(first_word["start"])

        # Prefer the earliest defensible detector onset.  RNNoise may suppress a
        # weak attack that remains visible in the raw waveform; averaging the
        # two would move the edit inside that attack.
        if has_start_raw and has_start_rnn:
            t_onset = min(t_onset_raw, t_onset_rnn)
        elif has_start_raw:
            t_onset = t_onset_raw
        elif has_start_rnn:
            t_onset = t_onset_rnn
        else:
            t_onset = float(first_word["start"])

        # Establish that this range really contains later bilateral speech.
        # This is stronger than trusting an isolated quiet ASR word and lets
        # the mandatory post-render transcript QC safely close the loop.
        has_later_speech = False
        if len(anchors) > 1:
            for later_word in anchors[1:]:
                _, _, later_raw, _ = get_scored_refined_bound(
                    activity_raw,
                    float(later_word["start"]),
                    float(later_word["end"]),
                    None,
                    None,
                )
                _, _, later_rnn, _ = get_scored_refined_bound(
                    activity_rnn,
                    float(later_word["start"]),
                    float(later_word["end"]),
                    None,
                    None,
                )
                if later_raw and later_rnn:
                    has_later_speech = True
                    break

        # Determine start boundary collisions and guards
        has_start_collision = False
        cue_guarded_start = False
        is_clamped_start = False
        start_rejection_reasons: list[str] = []
        start_notes: list[str] = []

        # Exact neighbor and attack limits in source frames. A cue ending in
        # the same frame as the selected word cannot be perfectly separated
        # in FCP7; attack preservation wins and the sub-frame coexistence is
        # reported instead of moving the cut inside the word.
        F_prev_limit = time_to_frame(prev_end, fps, "ceil") if prev_end is not None else 0
        F_attack_limit = time_to_frame(float(first_word["start"]), fps, "floor") if first_word else 0

        is_sub_frame_overlap = (prev_end is not None and F_prev_limit > F_attack_limit)
        is_overlapping_lexical = (prev_end is not None and prev_end > float(first_word["start"]))
        preserve_cue_attack_frame = False

        if is_sub_frame_overlap:
            if is_cue_word(prev_word["text"]) and not is_overlapping_lexical:
                # Cue guard allows preserving the word attack
                t_onset = float(first_word["start"])
                cue_guarded_start = True
                preserve_cue_attack_frame = True
                start_notes.append("sub_frame_cue_coexistence")
            else:
                # Non-cue collision or lexical overlap -> keep original and low/review
                t_onset = orig_start
                is_clamped_start = True
                if is_overlapping_lexical:
                    start_rejection_reasons.append("overlapping_lexical_intervals")
                else:
                    start_rejection_reasons.append("non_cue_collision")
        elif prev_word is not None:
            # Check for VAD-level collision
            raw_collides = has_start_raw and (comp_start_raw < prev_end)
            rnn_collides = has_start_rnn and (comp_start_rnn < prev_end)

            if raw_collides or rnn_collides:
                has_start_collision = True
                if is_cue_word(prev_word["text"]):
                    # Cue guard
                    t_onset = float(first_word["start"])
                    cue_guarded_start = True
                else:
                    # Non-cue collision -> keep original and low/review
                    t_onset = orig_start
                    is_clamped_start = True
                    start_rejection_reasons.append("non_cue_collision")

        # Convert start time to frame
        F_in = time_to_frame(t_onset, fps, "floor")

        if preserve_cue_attack_frame:
            F_in = F_attack_limit

        # Frame limit guard. Never clamp a defensible cue guard past the
        # lexical attack; an ordinary ambiguity remains low/original.
        if prev_word is not None and F_in < F_prev_limit and not cue_guarded_start:
            F_in = F_prev_limit
            is_clamped_start = True
            if "frame_clamp_collision" not in start_rejection_reasons:
                start_rejection_reasons.append("frame_clamp_collision")

        # Check lexical fallback for start if VAD missed it (quiet first word)
        is_lexical_fallback_start = False
        is_preview_guarded_start_fallback = False
        if not has_start_raw or not has_start_rnn:
            # Use local start metrics!
            local_metrics_ok = (start_snr is not None and start_corr is not None and start_snr >= 8.0 and start_corr >= 0.8)
            no_prev_collision = (prev_word is None or prev_end <= float(first_word["start"]))
            collision_exists = (has_start_collision or is_clamped_start or cue_guarded_start)

            if has_later_speech and local_metrics_ok and no_prev_collision and not collision_exists:
                t_onset = float(first_word["start"])
                is_lexical_fallback_start = True
                F_in = time_to_frame(t_onset, fps, "floor")
                if prev_word is not None and F_in < F_prev_limit:
                    F_in = F_prev_limit
                    is_lexical_fallback_start = False
                    is_clamped_start = True
                    start_rejection_reasons.append("frame_clamp_collision")

            # Conservative lexical fallback for real-world quiet attacks.
            # It never starts later than the ASR word and is accepted only
            # when later words have bilateral detector support. The mandatory
            # preview re-transcription then verifies the actual first token.
            if (
                not is_lexical_fallback_start
                and has_later_speech
                and len(anchors) >= 3
                and no_prev_collision
                and not collision_exists
            ):
                guarded_onset = (
                    float(transient_bridge["start"])
                    if transient_bridge is not None
                    else float(first_word["start"])
                )
                guarded_frame = time_to_frame(guarded_onset, fps, "floor")
                if prev_word is None or guarded_frame >= F_prev_limit:
                    t_onset = guarded_onset
                    F_in = guarded_frame
                    is_lexical_fallback_start = True
                    is_preview_guarded_start_fallback = True
                    start_notes.append(
                        "raw_transient_bridge"
                        if transient_bridge is not None
                        else "preview_transcript_guarded_lexical_fallback"
                    )

        # Find refined end bound (from last word)
        end_notes: list[str] = []
        comp_start_raw_end, comp_end_raw, has_end_raw, end_raw_candidates = get_scored_refined_bound(
            activity_raw, float(last_word["start"]), float(last_word["end"]), prev_end, next_start
        )
        comp_start_rnn_end, comp_end_rnn, has_end_rnn, end_rnn_candidates = get_scored_refined_bound(
            activity_rnn, float(last_word["start"]), float(last_word["end"]), prev_end, next_start
        )
        t_offset_raw = comp_end_raw if has_end_raw else float(last_word["end"])
        t_offset_rnn = comp_end_rnn if has_end_rnn else float(last_word["end"])

        # Prefer the acoustic speech offset. If raw includes post-speech noise/artifacts
        # that extend past RNNoise clean speech decay (with silence >= 150ms after rnn decay),
        # prefer the clean RNNoise speech offset.
        if has_end_raw and has_end_rnn:
            if comp_end_raw > comp_end_rnn and (comp_end_raw - comp_end_rnn) >= 0.15:
                idx_rnn_decay = int(comp_end_rnn * 1000 / 5.0)
                idx_rnn_check = int(min(comp_end_raw, comp_end_rnn + 0.30) * 1000 / 5.0)
                if idx_rnn_decay < len(activity_rnn) and not np.any(activity_rnn[idx_rnn_decay:idx_rnn_check]):
                    t_offset = comp_end_rnn
                    if "rnnoise_post_speech_noise_filtered" not in end_notes:
                        end_notes.append("rnnoise_post_speech_noise_filtered")
                else:
                    t_offset = max(comp_end_raw, comp_end_rnn)
            else:
                t_offset = max(comp_end_raw, comp_end_rnn)
        elif has_end_raw:
            t_offset = comp_end_raw
        elif has_end_rnn:
            t_offset = comp_end_rnn
        else:
            t_offset = float(last_word["end"])

        # Acoustic Out-point Snapping:
        # If VAD detected the acoustic decay of the speech before last_word["end"],
        # check if there is sustained silence (>=150ms) before last_word["end"].
        # If so, snap to the acoustic offset (eliminates ASR over-estimation).
        # If the gap is small (<150ms), protect the ending conservatively against jitter.
        # If VAD detected speech extending past last_word["end"], extend t_offset
        # to avoid cutting off final syllables (eliminates ASR under-estimation).
        trailing_silence = float(last_word["end"]) - t_offset if (has_end_raw or has_end_rnn) else 0.0

        if (has_end_raw or has_end_rnn) and trailing_silence >= 0.15:
            end_notes.append("asr_overestimation_tail_snapped")
        elif (has_end_raw or has_end_rnn) and t_offset > float(last_word["end"]):
            end_notes.append("asr_underestimation_tail_extended")
        elif t_offset < float(last_word["end"]):
            t_offset = float(last_word["end"])

        # Check for collision with next word
        has_end_collision = False
        is_clamped_end = False
        end_rejection_reasons = []
        is_end_overlapping_lexical = (
            next_word is not None and next_start < float(last_word["end"])
        )

        F_next_limit = time_to_frame(next_start, fps, "floor") if next_start is not None else 999999

        # Overlapping lexical intervals check
        if is_end_overlapping_lexical:
            t_offset = orig_end
            is_clamped_end = True
            end_rejection_reasons.append("overlapping_lexical_intervals")
        elif next_word is not None:
            # Check for VAD-level collision
            raw_collides = has_end_raw and (comp_end_raw > next_start)
            rnn_collides = has_end_rnn and (comp_end_rnn > next_start)

            if raw_collides or rnn_collides:
                has_end_collision = True
                if is_cue_word(next_word["text"]):
                    # Cue guard
                    t_offset = float(last_word["end"])
                else:
                    # Non-cue collision -> keep original and low/review
                    t_offset = orig_end
                    is_clamped_end = True
                    end_rejection_reasons.append("non_cue_collision")

        # Convert to frame and check frame limits
        F_out = time_to_frame(t_offset, fps, "ceil") + 2

        if next_word is not None and F_out > F_next_limit:
            F_out = F_next_limit
            if t_offset > next_start:
                if is_cue_word(next_word["text"]):
                    pass
                else:
                    is_clamped_end = True
                    end_rejection_reasons.append("frame_clamp_collision")

        # Cuts last word? Only if F_out is placed before the acoustic end of the word
        cuts_last_word = (F_out < time_to_frame(t_offset, fps, "ceil"))

        # Verify tail padding
        tail_frames = F_out - time_to_frame(t_offset, fps, "ceil")
        has_insufficient_tail = (tail_frames < 2)

        # Check if VAD or clamping/insufficient tail makes VAD invalid
        vad_invalid = (
            not (has_end_raw and has_end_rnn)
            or has_insufficient_tail
            or is_clamped_end
            or (t_offset_raw < float(last_word["end"]) and (float(last_word["end"]) - t_offset_raw) < 0.15)
            or (t_offset_rnn < float(last_word["end"]) and (float(last_word["end"]) - t_offset_rnn) < 0.15)
        )

        orig_cuts_last_word = (orig_end < float(last_word["end"]))

        # Check connected activity at the actual frame boundaries. Activity
        # merely ending close to a cut is not a crossing.
        orig_boundary_time = float(Fraction(F_orig_out, 1) / fps)
        refined_boundary_time = float(Fraction(F_out, 1) / fps)
        has_tail_crossing_orig = (
            is_anchor_connected_crossing(
                activity_raw,
                orig_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
            or is_anchor_connected_crossing(
                activity_rnn,
                orig_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
        )
        has_tail_crossing_refined = (
            is_anchor_connected_crossing(
                activity_raw,
                refined_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
            or is_anchor_connected_crossing(
                activity_rnn,
                refined_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
        )

        # Defensible offset evidence:
        if has_end_raw and has_end_rnn:
            offset_evidence = max(t_offset_raw, t_offset_rnn)
        elif has_end_raw:
            offset_evidence = t_offset_raw
        elif has_end_rnn:
            offset_evidence = t_offset_rnn
        else:
            offset_evidence = float(last_word["end"])

        F_evidence = time_to_frame(offset_evidence, fps, "ceil")
        has_two_frame_tail_lexical = (F_orig_out >= F_evidence + 2)

        # Check if original out-point is medium lexical-safe
        is_lexical_safe_end = False
        is_preview_guarded_end_fallback = False
        end_boundary_constraint = None
        if vad_invalid:
            metrics_ok = (end_snr is not None and end_corr is not None and end_snr >= 8.0 and end_corr >= 0.8)
            contains_last_word = (orig_end >= float(last_word["end"]))
            excludes_next_word = (next_word is None or F_orig_out <= F_next_limit)
            no_tail_crossing = not has_tail_crossing_orig

            if metrics_ok and contains_last_word and excludes_next_word and no_tail_crossing and not orig_cuts_last_word and has_two_frame_tail_lexical:
                is_lexical_safe_end = True

        if is_lexical_safe_end:
            F_out = F_orig_out
            t_offset = offset_evidence
            tail_guard_measured_from = "offset_evidence"
            tail_guard_base_time = offset_evidence

            # Recompute tail padding and cuts last word properties after override
            tail_frames = F_out - time_to_frame(tail_guard_base_time, fps, "ceil")
            has_insufficient_tail = (tail_frames < 2)
            cuts_last_word = (F_out < time_to_frame(float(last_word["end"]), fps, "ceil"))
        else:
            tail_guard_measured_from = "refined_offset"
            tail_guard_base_time = t_offset

        # A multi-word selected span can use a conservative lexical endpoint
        # even when noisy VAD evidence is unstable. The endpoint is never
        # before the ASR word end, normally includes two full frames, and can
        # be shortened only by the exact next-word frame limit. The rendered
        # transcript remains the final lexical authority.
        if not is_lexical_safe_end and has_later_speech and len(anchors) >= 3:
            preview_out_frame = F_evidence + 2
            available_tail_frames = 2
            constrained_by_next = False
            if next_word is not None and preview_out_frame > F_next_limit:
                preview_out_frame = F_next_limit
                available_tail_frames = preview_out_frame - F_evidence
                constrained_by_next = True
            contains_lexical_end = preview_out_frame >= time_to_frame(
                float(last_word["end"]), fps, "ceil"
            )
            excludes_next = next_word is None or preview_out_frame <= F_next_limit
            preview_boundary_time = float(Fraction(preview_out_frame, 1) / fps)
            preview_crossing = (
                is_anchor_connected_crossing(
                    activity_raw,
                    preview_boundary_time,
                    float(last_word["start"]),
                    float(last_word["end"]),
                )
                or is_anchor_connected_crossing(
                    activity_rnn,
                    preview_boundary_time,
                    float(last_word["start"]),
                    float(last_word["end"]),
                )
            )
            if (
                contains_lexical_end
                and excludes_next
                and not preview_crossing
                and not has_end_collision
                and not is_end_overlapping_lexical
                and has_valid_tail_budget(available_tail_frames)
            ):
                F_out = preview_out_frame
                t_offset = offset_evidence
                tail_frames = available_tail_frames
                has_insufficient_tail = available_tail_frames < 2
                cuts_last_word = False
                is_clamped_end = False
                is_preview_guarded_end_fallback = True
                tail_guard_measured_from = "offset_evidence"
                tail_guard_base_time = offset_evidence
                end_notes.append("preview_transcript_guarded_lexical_fallback")
                if constrained_by_next:
                    end_boundary_constraint = {
                        "reason": "next_word_frame_limit",
                        "required_tail_frames": 2,
                        "available_tail_frames": max(0, available_tail_frames),
                        "next_word_index": last_word_idx + 1,
                        "next_word_text": next_word["text"],
                        "next_word_start": float(next_word["start"]),
                    }
                    end_notes.append("neighbor_constrained_tail")

        # A breath/noise component disconnected from the last selected word
        # is not lexical tail and must not survive inside the left range.  If
        # the normal two-frame endpoint would include it, move to the last
        # frame boundary before that component.  A one-frame tail is allowed
        # only through this explicit constraint and remains subject to the
        # independently rendered preview QC.
        proposed_boundary_time = float(Fraction(F_out, 1) / fps)
        disconnected_candidates = []
        for signal_name, activity_signal in (
            ("raw", activity_raw),
            ("rnnoise", activity_rnn),
        ):
            component = first_disconnected_component_before(
                activity_signal,
                float(last_word["start"]),
                float(last_word["end"]),
                proposed_boundary_time,
            )
            if component is not None:
                disconnected_candidates.append((component[0], component[1], signal_name))
        if disconnected_candidates:
            first_component_start = min(item[0] for item in disconnected_candidates)
            first_component_end = max(
                item[1]
                for item in disconnected_candidates
                if item[0] == first_component_start
            )
            safe_out_frame = time_to_frame(first_component_start, fps, "floor")
            minimum_lexical_frame = time_to_frame(float(last_word["end"]), fps, "ceil")
            if safe_out_frame >= max(minimum_lexical_frame, F_evidence):
                F_out = min(F_out, safe_out_frame)
                available_tail_frames = F_out - F_evidence
                t_offset = offset_evidence
                tail_frames = available_tail_frames
                has_insufficient_tail = available_tail_frames < 2
                cuts_last_word = False
                is_clamped_end = False
                is_lexical_safe_end = False
                is_preview_guarded_end_fallback = True
                tail_guard_measured_from = "offset_evidence"
                tail_guard_base_time = offset_evidence
                end_boundary_constraint = {
                    "reason": "disconnected_post_word_activity",
                    "required_tail_frames": 2,
                    "available_tail_frames": available_tail_frames,
                    "activity_start": first_component_start,
                    "activity_end": first_component_end,
                    "signals": sorted({
                        item[2]
                        for item in disconnected_candidates
                        if item[0] == first_component_start
                    }),
                }
                if has_valid_tail_budget(
                    available_tail_frames,
                    allow_one_frame_exception=True,
                ):
                    end_notes.append("disconnected_post_word_activity_guard")
                else:
                    # Zero available tail is not a narrower version of the
                    # exception: this limit must remain original and block.
                    is_preview_guarded_end_fallback = False
                    end_boundary_constraint = None
                    end_rejection_reasons.append(
                        "disconnected_activity_without_one_frame_tail"
                    )
            else:
                is_clamped_end = True
                end_rejection_reasons.append("disconnected_activity_collision")

        # The candidate may have changed after lexical/disconnected guards.
        refined_boundary_time = float(Fraction(F_out, 1) / fps)
        has_tail_crossing_refined = (
            is_anchor_connected_crossing(
                activity_raw,
                refined_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
            or is_anchor_connected_crossing(
                activity_rnn,
                refined_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
        )


        # Threshold sweep for agreement and stability independently
        sweep_deltas = [(-1.0, -0.5), (0.0, 0.0), (1.0, 0.5)]
        gap_frames = int(DEFAULT_VAD_PARAMS["gap_fill_ms"] / DEFAULT_VAD_PARAMS["hop_ms"])
        trans_frames = int(DEFAULT_VAD_PARAMS["transient_protection_ms"] / DEFAULT_VAD_PARAMS["hop_ms"])

        F_in_raw_vals = []
        F_in_rnn_vals = []
        F_out_raw_vals = []
        F_out_rnn_vals = []

        baseline_start_ok = False
        baseline_end_ok = False

        start_sweep_records = []
        end_sweep_records = []

        for s_idx, (delta_high, delta_low) in enumerate(sweep_deltas):
            high_t = DEFAULT_VAD_PARAMS["threshold_high_db"] + delta_high
            low_t = DEFAULT_VAD_PARAMS["threshold_low_db"] + delta_low

            act_raw_s = run_vad_hysteresis(
                rms_raw, nf_raw,
                high_t, low_t,
                gap_frames, trans_frames
            )
            act_rnn_s = run_vad_hysteresis(
                rms_rnn, nf_rnn,
                high_t, low_t,
                gap_frames, trans_frames
            )

            # Resolve start sweep
            t_onset_raw_s, _, has_start_raw_s, _ = get_scored_refined_bound(
                act_raw_s, float(first_word["start"]), float(first_word["end"]), prev_end, next_start
            )
            t_onset_rnn_s, _, has_start_rnn_s, _ = get_scored_refined_bound(
                act_rnn_s, float(first_word["start"]), float(first_word["end"]), prev_end, next_start
            )

            start_sweep_records.append({
                "delta": (delta_high, delta_low),
                "has_raw": has_start_raw_s,
                "has_rnn": has_start_rnn_s,
                "t_raw": t_onset_raw_s if has_start_raw_s else None,
                "t_rnn": t_onset_rnn_s if has_start_rnn_s else None
            })

            if has_start_raw_s and has_start_rnn_s:
                F_in_raw_s = time_to_frame(t_onset_raw_s, fps, "floor")
                F_in_rnn_s = time_to_frame(t_onset_rnn_s, fps, "floor")
                F_in_raw_vals.append(F_in_raw_s)
                F_in_rnn_vals.append(F_in_rnn_s)
                if s_idx == 1: # Baseline index
                    baseline_start_ok = True

            # Resolve end sweep
            _, t_offset_raw_s, has_end_raw_s, _ = get_scored_refined_bound(
                act_raw_s, float(last_word["start"]), float(last_word["end"]), prev_end, next_start
            )
            _, t_offset_rnn_s, has_end_rnn_s, _ = get_scored_refined_bound(
                act_rnn_s, float(last_word["start"]), float(last_word["end"]), prev_end, next_start
            )

            end_sweep_records.append({
                "delta": (delta_high, delta_low),
                "has_raw": has_end_raw_s,
                "has_rnn": has_end_rnn_s,
                "t_raw": t_offset_raw_s if has_end_raw_s else None,
                "t_rnn": t_offset_rnn_s if has_end_rnn_s else None
            })

            if has_end_raw_s and has_end_rnn_s:
                F_out_raw_s = time_to_frame(t_offset_raw_s, fps, "ceil") + 2
                F_out_rnn_s = time_to_frame(t_offset_rnn_s, fps, "ceil") + 2
                F_out_raw_vals.append(F_out_raw_s)
                F_out_rnn_vals.append(F_out_rnn_s)
                if s_idx == 1: # Baseline index
                    baseline_end_ok = True

        # Determine start sweep stability
        if baseline_start_ok and len(F_in_raw_vals) == 3:
            spread_raw = max(F_in_raw_vals) - min(F_in_raw_vals)
            spread_rnn = max(F_in_rnn_vals) - min(F_in_rnn_vals)
            spread_in = max(F_in_raw_vals + F_in_rnn_vals) - min(F_in_raw_vals + F_in_rnn_vals)
            sweep_start_ok = True

            # If raw VAD caught pre-speech breath/inhalation (comp_start_raw < comp_start_rnn)
            # but RNNoise is stable and isolated the true voiced attack (spread_rnn <= 2):
            # Prefer the clean RNNoise speech onset instead of treating the start as unstable!
            if (
                has_start_rnn
                and comp_start_raw < comp_start_rnn
                and spread_rnn <= 2
                and comp_start_rnn <= float(first_word["start"])
                and (prev_end is None or comp_start_rnn >= prev_end)
            ):
                t_onset = comp_start_rnn
                F_in = time_to_frame(t_onset, fps, "floor")
                spread_in = spread_rnn
                if "rnnoise_pre_speech_breath_filtered" not in start_notes:
                    start_notes.append("rnnoise_pre_speech_breath_filtered")
        else:
            spread_in = 9999
            sweep_start_ok = False

        # Determine end sweep stability
        if baseline_end_ok and len(F_out_raw_vals) == 3:
            spread_raw_out = max(F_out_raw_vals) - min(F_out_raw_vals)
            spread_rnn_out = max(F_out_rnn_vals) - min(F_out_rnn_vals)
            spread_out = max(F_out_raw_vals + F_out_rnn_vals) - min(F_out_raw_vals + F_out_rnn_vals)
            sweep_end_ok = True

            # If raw VAD caught post-speech noise/decay artifacts (comp_end_raw > comp_end_rnn)
            # but RNNoise is stable and isolated the true speech offset (spread_rnn_out <= 2):
            if (
                has_end_rnn
                and comp_end_raw > comp_end_rnn
                and (comp_end_raw - comp_end_rnn) >= 0.15
                and spread_rnn_out <= 2
            ):
                idx_rnn_decay = int(comp_end_rnn * 1000 / 5.0)
                idx_rnn_check = int(min(comp_end_raw, comp_end_rnn + 0.30) * 1000 / 5.0)
                if idx_rnn_decay < len(activity_rnn) and not np.any(activity_rnn[idx_rnn_decay:idx_rnn_check]):
                    t_offset = comp_end_rnn
                    F_out = time_to_frame(t_offset, fps, "ceil") + 2
                    spread_out = spread_rnn_out
                    if "rnnoise_post_speech_noise_filtered" not in end_notes:
                        end_notes.append("rnnoise_post_speech_noise_filtered")
        else:
            spread_out = 9999
            sweep_end_ok = False

        # The detector pair may disagree by several frames, or the raw signal
        # may stay connected to a rejected direction immediately before the
        # selected take.  In those cases the transcript still gives us one
        # conservative, frame-exact boundary: floor(first_word.start).  It is
        # safe only when that frame is at/after ceil(previous_word.end), the
        # selected span contains later bilateral speech, and local evidence is
        # healthy.  The mandatory rendered-preview transcription is the final
        # authority, so this path is deliberately medium confidence.
        unstable_or_connected_start = (
            not sweep_start_ok
            or spread_in > 2
            or has_start_collision
            or is_clamped_start
        )
        lexical_frame_separates_neighbor = (
            prev_word is None
            or (
                not is_overlapping_lexical
                and F_prev_limit <= F_attack_limit
            )
        )
        lexical_guard_metrics_ok = (
            start_snr is not None
            and start_corr is not None
            and start_snr >= 8.0
            and start_corr >= 0.8
        )
        if (
            not is_lexical_fallback_start
            and not cue_guarded_start
            and unstable_or_connected_start
            and lexical_frame_separates_neighbor
            and lexical_guard_metrics_ok
            and has_later_speech
            and len(anchors) >= 3
            and (has_start_raw or has_start_rnn)
        ):
            t_onset = float(first_word["start"])
            F_in = F_attack_limit
            is_lexical_fallback_start = True
            is_preview_guarded_start_fallback = True
            is_clamped_start = False
            for obsolete_reason in ("non_cue_collision", "frame_clamp_collision"):
                while obsolete_reason in start_rejection_reasons:
                    start_rejection_reasons.remove(obsolete_reason)
            if "preview_transcript_guarded_lexical_fallback" not in start_notes:
                start_notes.append("preview_transcript_guarded_lexical_fallback")

        # Medium cue guard requires: both baseline detectors active, sweep stable and spread <= 2,
        # non-overlapping lexical timestamps, valid local metrics, and no attack cut.
        is_safe_cue_guard_start = False
        if cue_guarded_start:
            has_both_detectors = (has_start_raw and has_start_rnn)
            is_sweep_stable = (sweep_start_ok and spread_in <= 2)
            no_lexical_overlap = (prev_word is None or prev_end <= float(first_word["start"]))
            no_attack_cut = (F_in <= time_to_frame(float(first_word["start"]), fps, "floor"))

            if has_both_detectors and is_sweep_stable and no_lexical_overlap and no_attack_cut:
                is_safe_cue_guard_start = True

        # A non-cue neighbor may end between two frame boundaries while the
        # selected word begins later.  If the acoustic component itself does
        # not overlap the previous word, advancing to ceil(prev_end) is the
        # only frame-exact representation.  Treat that sub-frame quantization
        # as a medium-confidence note, not as a lexical collision.
        is_safe_subframe_neighbor_start = False
        if (
            prev_word is not None
            and not cue_guarded_start
            and not is_overlapping_lexical
            and has_start_raw
            and has_start_rnn
            and comp_start_raw >= prev_end
            and comp_start_rnn >= prev_end
            and time_to_frame(t_onset, fps, "floor") < F_prev_limit
            and F_prev_limit <= F_attack_limit
            and sweep_start_ok
            and spread_in <= 2
        ):
            is_safe_subframe_neighbor_start = True
            is_clamped_start = False
            if "frame_clamp_collision" in start_rejection_reasons:
                start_rejection_reasons.remove("frame_clamp_collision")
            start_notes.append("sub_frame_neighbor_quantization")

        # A stable VAD can still start inside a weak lexical attack.  Scan the
        # raw waveform below the normal entry threshold before approving any
        # trim that moves later than the transcript's first-word onset.
        pre_onset_attack_risk, pre_onset_attack_evidence = get_pre_onset_attack_risk(
            rms_raw,
            nf_raw,
            float(first_word["start"]),
            t_onset,
        )
        if cue_guarded_start or is_lexical_fallback_start or F_in <= F_attack_limit:
            pre_onset_attack_risk = False
            pre_onset_attack_evidence["bypassed_by_safe_boundary"] = True
        else:
            pre_onset_attack_evidence["bypassed_by_safe_boundary"] = False

        # Detector agreement is not sufficient evidence to discard the
        # transcript's lexical attack.  In particular, raw and RNNoise can
        # agree on the voiced body of a word after both missed its weak onset.
        # Such a proposal must fail closed instead of being promoted to a
        # high-confidence destructive trim.
        cuts_first_word_attack = F_in > F_attack_limit

        # Start confidence classification
        start_conf = "low"
        start_metrics_ok = (start_snr is not None and start_corr is not None and start_snr >= 8.0 and start_corr >= 0.8)

        if (
            (start_metrics_ok or is_preview_guarded_start_fallback)
            and (has_start_raw or cue_guarded_start or is_lexical_fallback_start)
            and (has_start_rnn or cue_guarded_start or is_lexical_fallback_start)
            and (sweep_start_ok or is_lexical_fallback_start or cue_guarded_start)
            and not pre_onset_attack_risk
            and not cuts_first_word_attack
        ):
            if is_lexical_fallback_start:
                start_conf = "medium"
            elif has_start_collision or cue_guarded_start:
                if is_safe_cue_guard_start:
                    start_conf = "medium"
                else:
                    start_conf = "low"
            elif is_safe_subframe_neighbor_start:
                start_conf = "medium"
            elif not is_clamped_start:
                if spread_in <= 1:
                    start_conf = "high"
                elif spread_in <= 2:
                    start_conf = "medium"

        # Record start rejection reasons
        if not start_metrics_ok and not is_preview_guarded_start_fallback:
            start_rejection_reasons.append("poor_local_metrics")
        if not (has_start_raw or cue_guarded_start or is_lexical_fallback_start) or not (has_start_rnn or cue_guarded_start or is_lexical_fallback_start):
            start_rejection_reasons.append("vad_missed")
        if not (sweep_start_ok or is_lexical_fallback_start or cue_guarded_start):
            start_rejection_reasons.append("unstable_sweep")
        if pre_onset_attack_risk:
            start_rejection_reasons.append("raw_activity_before_selected_onset")
        if cuts_first_word_attack:
            start_rejection_reasons.append("cuts_first_word_attack")

        # End confidence classification
        if (
            is_preview_guarded_end_fallback
            and not sweep_end_ok
            and end_boundary_constraint is None
        ):
            # The downstream transcript can resolve a noisy-but-stable
            # endpoint, not a detector that disappears under threshold sweep.
            is_preview_guarded_end_fallback = False
            end_boundary_constraint = None
            end_notes = []
        end_conf = "low"
        if is_lexical_safe_end or is_preview_guarded_end_fallback:
            end_conf = "medium"
        else:
            end_metrics_ok = (end_snr is not None and end_corr is not None and end_snr >= 8.0 and end_corr >= 0.8)
            if (
                end_metrics_ok
                and has_end_raw
                and has_end_rnn
                and not is_clamped_end
                and not has_insufficient_tail
                and not cuts_last_word
                and not has_tail_crossing_refined
                and sweep_end_ok
            ):
                if spread_out <= 1:
                    end_conf = "high"
                elif spread_out <= 2:
                    end_conf = "medium"

        # Record end rejection reasons
        if not is_lexical_safe_end and not is_preview_guarded_end_fallback:
            end_metrics_ok = (end_snr is not None and end_corr is not None and end_snr >= 8.0 and end_corr >= 0.8)
            if not end_metrics_ok:
                end_rejection_reasons.append("poor_local_metrics")
            if not has_end_raw or not has_end_rnn:
                end_rejection_reasons.append("vad_missed")
            if is_clamped_end:
                end_rejection_reasons.append("clamped_end")
            if has_insufficient_tail:
                end_rejection_reasons.append("insufficient_tail")
            if cuts_last_word:
                end_rejection_reasons.append("cuts_last_word")
            if not sweep_end_ok:
                end_rejection_reasons.append("unstable_sweep")
            if has_tail_crossing_orig:
                end_rejection_reasons.append("original_tail_crossing")
            if has_tail_crossing_refined:
                end_rejection_reasons.append("refined_tail_crossing")

        # Apply High/Medium endpoints, Low stays original independently
        if start_conf in ("high", "medium"):
            final_in_frame = F_in
            final_start_s = frame_to_time(final_in_frame, fps)
        else:
            final_in_frame = F_orig_in
            final_start_s = orig_start

        if end_conf in ("high", "medium"):
            final_out_frame = F_out
            final_end_s = frame_to_time(final_out_frame, fps)
        else:
            final_out_frame = F_orig_out
            final_end_s = orig_end

        final_boundary_time = float(Fraction(final_out_frame, 1) / fps)
        has_tail_crossing_final = (
            is_anchor_connected_crossing(
                activity_raw,
                final_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
            or is_anchor_connected_crossing(
                activity_rnn,
                final_boundary_time,
                float(last_word["start"]),
                float(last_word["end"]),
            )
        )
        tail_frames = final_out_frame - time_to_frame(offset_evidence, fps, "ceil")

        review_required = (start_conf == "low" or end_conf == "low")
        review_status = "approved" if not review_required else "low"
        if review_required:
            has_low_confidence = True

        # Copy original range fields and update with refinement info
        r_new = dict(r)
        boundary_constraints = dict(r.get("boundary_constraints", {})) if isinstance(r.get("boundary_constraints"), dict) else {}
        if end_boundary_constraint is not None:
            boundary_constraints["end"] = end_boundary_constraint
        else:
            boundary_constraints.pop("end", None)
        r_new.update({
            "original_start": orig_start,
            "original_end": orig_end,
            "start": final_start_s,
            "end": final_end_s,
            "source_in_frame": final_in_frame,
            "source_out_frame": final_out_frame,
            "lexical_anchors": {
                "first": {
                    "word_index": first_word_idx,
                    "text": first_word["text"],
                    "start": float(first_word["start"]),
                    "end": float(first_word["end"]),
                    "acoustic_onset": t_onset,
                },
                "last": {
                    "word_index": last_word_idx,
                    "text": last_word["text"],
                    "start": float(last_word["start"]),
                    "end": float(last_word["end"]),
                    "acoustic_offset": offset_evidence,
                },
            },
            "boundary_constraints": boundary_constraints,
            "review_status": review_status,
            "review_required": review_required
        })
        updated_ranges.append(r_new)

        # Record boundary evidence
        # Correct final decision strings
        final_decision_start = "retained_original_low"
        if start_conf in ("high", "medium"):
            final_decision_start = "lexical_fallback" if is_lexical_fallback_start else ("cue_guarded" if cue_guarded_start else "refined")

        final_decision_end = "retained_original_low"
        if end_conf in ("high", "medium"):
            if is_preview_guarded_end_fallback:
                final_decision_end = "preview_guarded_lexical"
            else:
                final_decision_end = "lexical_safe" if is_lexical_safe_end else "refined"

        # Exact pre-roll and attack-cut calculations for the endpoint written.
        onset_frame_floor = time_to_frame(t_onset, fps, "floor")
        final_in_time = float(Fraction(final_in_frame, 1) / fps)
        first_word_start = float(first_word["start"])
        pre_roll_frames = max(0, onset_frame_floor - final_in_frame)
        pre_roll_ms = max(0.0, (first_word_start - final_in_time) * 1000.0)
        attack_cut_frames = max(0, final_in_frame - F_attack_limit)
        attack_cut_ms = max(0.0, (final_in_time - first_word_start) * 1000.0)
        sub_frame_cue_ms = (
            max(0.0, (prev_end - final_in_time) * 1000.0)
            if cue_guarded_start and prev_end is not None
            else 0.0
        )

        # Tail guard is always measured from the most conservative evidence.
        tail_guard_measured_from_val = "offset_evidence"
        tail_guard_base_time_val = offset_evidence

        boundary_evidence.append({
            "range_index": idx,
            "source": source_id,
            "original": {"start": orig_start, "end": orig_end, "in_frame": F_orig_in, "out_frame": F_orig_out},
            "anchor_words": {
                "first": {"text": first_word["text"], "start": float(first_word["start"]), "end": float(first_word["end"])},
                "last": {"text": last_word["text"], "start": float(last_word["start"]), "end": float(last_word["end"])}
            },
            "neighbor_words": {
                "prev": {"text": prev_word["text"], "start": float(prev_word["start"]), "end": float(prev_word["end"])} if prev_word else None,
                "next": {"text": next_word["text"], "start": float(next_word["start"]), "end": float(next_word["end"])} if next_word else None
            },
            "local_metrics": {
                "start": {
                    "snr_db": start_snr,
                    "correlation": start_corr
                },
                "end": {
                    "snr_db": end_snr,
                    "correlation": end_corr
                }
            },
            "start_side": {
                "candidates": start_raw_candidates,
                "selected_component": {
                    "start": comp_start_raw if has_start_raw else None,
                    "end": comp_end_raw if has_start_raw else None
                },
                "raw_candidates": start_raw_candidates,
                "rnn_candidates": start_rnn_candidates,
                "raw_selected_component": {
                    "start": comp_start_raw if has_start_raw else None,
                    "end": comp_end_raw if has_start_raw else None
                },
                "rnn_selected_component": {
                    "start": comp_start_rnn if has_start_rnn else None,
                    "end": comp_end_rnn if has_start_rnn else None
                },
                "final_combined_selection": {
                    "start": t_onset
                },
                "lexical_fallback": is_lexical_fallback_start,
                "preview_guarded_fallback": is_preview_guarded_start_fallback,
                "cue_guard": cue_guarded_start,
                "collision": has_start_collision,
                "is_clamped": is_clamped_start,
                "exact_neighbor_frame_limit": F_prev_limit,
                "attack_guard_ms": pre_roll_ms,
                "attack_guard_frames": pre_roll_frames,
                "pre_roll_ms": pre_roll_ms,
                "pre_roll_frames": pre_roll_frames,
                "attack_cut_ms": attack_cut_ms,
                "attack_cut_frames": attack_cut_frames,
                "proposed_cuts_first_word_attack": cuts_first_word_attack,
                "sub_frame_cue_coexistence_ms": sub_frame_cue_ms,
                "pre_onset_attack_risk": pre_onset_attack_risk,
                "pre_onset_attack_evidence": pre_onset_attack_evidence,
                "sweep_results": {
                    "ok": sweep_start_ok,
                    "spread": int(spread_in) if (sweep_start_ok and spread_in != 9999) else None,
                    "records": start_sweep_records
                },
                "notes": start_notes,
                "rejection_reasons": start_rejection_reasons,
                "final_decision": final_decision_start,
                "local_metric_window": [start_win_t1, start_win_t2],
                "local_metric_excludes": start_excludes
            },
            "end_side": {
                "candidates": end_raw_candidates,
                "selected_component": {
                    "start": comp_start_raw_end if has_end_raw else None,
                    "end": comp_end_raw if has_end_raw else None
                },
                "raw_candidates": end_raw_candidates,
                "rnn_candidates": end_rnn_candidates,
                "raw_selected_component": {
                    "start": comp_start_raw_end if has_end_raw else None,
                    "end": comp_end_raw if has_end_raw else None
                },
                "rnn_selected_component": {
                    "start": comp_start_rnn_end if has_end_rnn else None,
                    "end": comp_end_rnn if has_end_rnn else None
                },
                "final_combined_selection": {
                    "end": t_offset
                },
                "lexical_fallback": is_lexical_safe_end or is_preview_guarded_end_fallback,
                "preview_guarded_fallback": is_preview_guarded_end_fallback,
                "boundary_constraint": end_boundary_constraint,
                "notes": end_notes,
                "cue_guard": has_end_collision and is_cue_word(next_word["text"]) if next_word else False,
                "collision": has_end_collision,
                "is_clamped": is_clamped_end,
                "exact_neighbor_frame_limit": F_next_limit if F_next_limit != 999999 else None,
                "tail_crossing": has_tail_crossing_final,
                "tail_crossing_orig": has_tail_crossing_orig,
                "tail_crossing_refined": has_tail_crossing_refined,
                "tail_guard_measured_from": tail_guard_measured_from_val,
                "tail_guard_base_time": tail_guard_base_time_val,
                "tail_guard_ms": float(tail_frames * 1000.0 / fps),
                "tail_guard_frames": int(tail_frames),
                "sweep_results": {
                    "ok": sweep_end_ok,
                    "spread": int(spread_out) if (sweep_end_ok and spread_out != 9999) else None,
                    "records": end_sweep_records
                },
                "rejection_reasons": end_rejection_reasons,
                "final_decision": final_decision_end,
                "local_metric_window": [end_win_t1, end_win_t2],
                "local_metric_excludes": end_excludes
            },
            "snr_db": snr_val,
            "correlation": corr_val,
            "confidence": {"start": start_conf, "end": end_conf},
            "spread": {
                "start": int(spread_in) if (sweep_start_ok and spread_in != 9999) else None,
                "end": int(spread_out) if (sweep_end_ok and spread_out != 9999) else None
            },
            "final_frames": {"in": final_in_frame, "out": final_out_frame},
            "final_times": {"start": final_start_s, "end": final_end_s},
            "tail_frames": tail_frames,
            "vad_refined": {
                "onset_raw": t_onset_raw,
                "offset_raw": t_offset_raw,
                "onset_rnn": t_onset_rnn,
                "offset_rnn": t_offset_rnn
            },
            "clamped": {"start": is_clamped_start, "end": is_clamped_end}
        })



    # Prepare updated EDL dict
    new_total_duration = round(sum(r["end"] - r["start"] for r in updated_ranges), 6)
    edl_new = dict(edl)
    edl_new["ranges"] = updated_ranges
    edl_new["total_duration_s"] = new_total_duration
    if "metadata" not in edl_new:
        edl_new["metadata"] = {}
    edl_new["metadata"]["sequence_fps"] = format_fps_fraction(fps)
    edl_new["metadata"]["refined_by"] = "alano-cut-snapper-f1.1"

    # Write Backup BEFORE modifying the input EDL file
    backups_dir = edit_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backups_dir / f"edl.{input_hash}.json"

    # Immutable create-exclusive dedup backup, verified before any replace.
    try:
        ensure_immutable_backup(backup_path, input_raw_bytes, input_hash)
    except RuntimeError as exc:
        print(f"Fatal Error: Backup preflight failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Serialize output files
    edl_output_str = json.dumps(edl_new, indent=2, ensure_ascii=False)
    output_hash = calculate_sha256_of_string(edl_output_str)

    # Compile report
    report = {
        "status": "review" if has_low_confidence else "pass",
        "settings": DEFAULT_VAD_PARAMS,
        "internal_silence_policy": {
            "policy": INTERNAL_SILENCE_SPLIT_POLICY,
            "threshold_ms": float(INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS * 1000),
            "comparison": "strictly_greater_than",
            "authority": "canonical_provider_words",
            "override_field": (
                f"boundary_constraints.{INTERNAL_SILENCE_OVERRIDE_FIELD}"
            ),
            "detected_gap_count": sum(len(event["gaps"]) for event in internal_silence_events),
            "split_gap_count": sum(
                sum(not gap["preserve_override"] for gap in event["gaps"])
                for event in internal_silence_events
            ),
            "preserved_gap_count": sum(
                sum(gap["preserve_override"] for gap in event["gaps"])
                for event in internal_silence_events
            ),
        },
        "internal_silence_events": internal_silence_events,
        "input_edl_hash": input_hash,
        "output_edl_hash": output_hash,
        "source_fingerprints": source_fingerprints,
        "confidence_summary": {
            "high": sum(1 for e in boundary_evidence if e["confidence"]["start"] == "high") + sum(1 for e in boundary_evidence if e["confidence"]["end"] == "high"),
            "medium": sum(1 for e in boundary_evidence if e["confidence"]["start"] == "medium") + sum(1 for e in boundary_evidence if e["confidence"]["end"] == "medium"),
            "low": sum(1 for e in boundary_evidence if e["confidence"]["start"] == "low") + sum(1 for e in boundary_evidence if e["confidence"]["end"] == "low")
        },
        "boundary_evidence": boundary_evidence
    }
    report_output_str = json.dumps(report, indent=2, ensure_ascii=False)

    # Write EDL and Report atomically with staging and replacement safety
    temp_report = args.report.with_suffix(args.report.suffix + f".{os.getpid()}.tmp")
    temp_edl = edl_path.with_suffix(edl_path.suffix + f".{os.getpid()}.tmp")

    # Read pre-existing contents for rollback in case of replacement failure
    report_backup_bytes = args.report.read_bytes() if args.report.exists() else None
    edl_backup_bytes = edl_path.read_bytes() if edl_path.exists() else None

    # Stage both files (write + flush + fsync)
    try:
        temp_report.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_report, "w", encoding="utf-8", newline="\n") as f:
            f.write(report_output_str)
            f.flush()
            os.fsync(f.fileno())

        temp_edl.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_edl, "w", encoding="utf-8", newline="\n") as f:
            f.write(edl_output_str)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        if temp_report.exists():
            temp_report.unlink()
        if temp_edl.exists():
            temp_edl.unlink()
        print(f"Fatal Error: Failed to stage files: {e}", file=sys.stderr)
        sys.exit(1)

    # Perform replacements with rollback capability
    report_replaced = False
    edl_replaced = False
    try:
        os.replace(str(temp_report), str(args.report))
        report_replaced = True
        os.replace(str(temp_edl), str(edl_path))
        edl_replaced = True
    except Exception as e:
        # Clean up any leftover temp files first
        for p in [temp_report, temp_edl]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        # Rollback report if replaced
        if report_replaced:
            try:
                if report_backup_bytes is not None:
                    args.report.write_bytes(report_backup_bytes)
                else:
                    if args.report.exists():
                        args.report.unlink()
            except Exception as rollback_err:
                print(f"Warning: Failed to rollback report file: {rollback_err}", file=sys.stderr)

        # Rollback EDL if replaced
        if edl_replaced:
            try:
                if edl_backup_bytes is not None:
                    edl_path.write_bytes(edl_backup_bytes)
                else:
                    if edl_path.exists():
                        edl_path.unlink()
            except Exception as rollback_err:
                print(f"Warning: Failed to rollback EDL file: {rollback_err}", file=sys.stderr)

        print(f"Fatal Error: Failed to replace files: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully refined EDL boundaries: {edl_path}")
    print(f"Wrote atomic report: {args.report}")
    print(f"Status: {report['status'].upper()}")

    # Determine exit code based on review status
    if has_low_confidence:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
