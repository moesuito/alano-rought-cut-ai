"""Readiness gate tool to verify if the edit is ready for XML export.

Checks freshness, hashes, and status across the four QC reports:
- Boundary QC (edl_boundary_qc.json)
- Audio QC (preview_audio_qc.json)
- Semantic QC (edl_semantic_qc.json)
- Preview Transcript QC (preview_transcript_qc.json)

Exit Codes:
- 0: Pass (READY - all checks pass and are fresh)
- 2: Review (REVIEW NEEDED - checks are fresh, but some warnings/reviews are flagged)
- 1: Fatal (FATAL ERROR - missing/stale reports, schema errors, or fail status)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.timing import frame_to_sample, parse_fps_fraction
from helpers.internal_silence import (
    INTERNAL_SILENCE_SPLIT_POLICY,
    INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS,
    compute_internal_silence_event_id,
    evaluate_internal_silence_contract,
)
from helpers.preview_audio_qc import tail_requirement_frames, tail_sample_count
from helpers.preview_transcript_qc import (
    DEFAULT_CUE_TERMS,
    align_token_records,
    build_report,
    duplicate_insertion_spans,
    duplicate_token_excess,
    normalize_text,
    token_ratio,
    tokenize,
)
from helpers.transcription_contract import (
    TranscriptContractError,
    validate_normative_transcript,
    validate_normative_transcript_for_intervals,
)


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_mtime(path: Path) -> float:
    """Get the modification time of a file. Returns 0.0 if not exists."""
    if not path.exists():
        return 0.0
    return path.stat().st_mtime


KNOWN_STATUSES = {"pass", "warning", "review", "fail", "error"}
STATUS_SEVERITY = {"pass": 0, "warning": 1, "review": 2, "fail": 3, "error": 3}
BOUNDARY_CONFIDENCES = {"high", "medium", "low"}


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mapped_tail_expectation(
    map_range: dict[str, object],
    fps_value: object,
    total_samples: int,
) -> dict[str, object]:
    """Derive an exact endpoint-relative tail solely from the timeline map."""
    source_frames = map_range.get("source_frames")
    cumulative = map_range.get("output_cumulative_sample_interval")
    if (
        not isinstance(source_frames, list)
        or len(source_frames) != 2
        or not all(_is_plain_int(value) for value in source_frames)
        or not isinstance(cumulative, list)
        or len(cumulative) != 2
        or not all(_is_plain_int(value) for value in cumulative)
    ):
        raise ValueError("timeline map tail intervals are invalid")

    source_in, source_out = source_frames
    cumulative_in, cumulative_out = cumulative
    frame_count = tail_requirement_frames(map_range)
    required_samples = tail_sample_count(fps_value, source_out, frame_count)
    window = [cumulative_out - required_samples, cumulative_out]
    coverage = (
        source_out - source_in >= frame_count
        and window[0] >= cumulative_in
        and cumulative_out <= total_samples
    )
    return {
        "frames": frame_count,
        "samples": required_samples,
        "window": window,
        "coverage": coverage,
    }


def validate_boundary_report(
    boundary_data: object,
    edl_ranges: list[dict[str, object]],
) -> list[str]:
    """Return structural/consistency errors for a refiner boundary report."""
    errors: list[str] = []
    if not isinstance(boundary_data, dict):
        return ["report is not a JSON object"]

    evidence = boundary_data.get("boundary_evidence")
    summary = boundary_data.get("confidence_summary")
    if not isinstance(evidence, list):
        errors.append("boundary_evidence is not a list")
        return errors
    if not isinstance(summary, dict):
        errors.append("confidence_summary is not an object")
        return errors
    if len(evidence) != len(edl_ranges):
        errors.append(
            f"boundary_evidence count {len(evidence)} does not match EDL range count {len(edl_ranges)}"
        )

    observed_counts = {"high": 0, "medium": 0, "low": 0}
    seen_indices: set[int] = set()
    for position, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"boundary_evidence[{position}] is not an object")
            continue
        range_index = item.get("range_index")
        if (
            not isinstance(range_index, int)
            or isinstance(range_index, bool)
            or range_index < 0
            or range_index >= len(edl_ranges)
        ):
            errors.append(f"boundary_evidence[{position}] has invalid range_index")
            continue
        if range_index in seen_indices:
            errors.append(f"boundary_evidence has duplicate range_index {range_index}")
            continue
        seen_indices.add(range_index)

        edl_range = edl_ranges[range_index]
        if item.get("source") != edl_range.get("source"):
            errors.append(f"boundary_evidence[{position}] source does not match EDL range {range_index}")

        final_frames = item.get("final_frames")
        if not isinstance(final_frames, dict):
            errors.append(f"boundary_evidence[{position}] final_frames is invalid")
        elif (
            final_frames.get("in") != edl_range.get("source_in_frame")
            or final_frames.get("out") != edl_range.get("source_out_frame")
        ):
            errors.append(f"boundary_evidence[{position}] final_frames do not match EDL range {range_index}")

        constraints = edl_range.get("boundary_constraints") or {}
        end_constraint = constraints.get("end") if isinstance(constraints, dict) else None
        end_side = item.get("end_side")
        reported_constraint = (
            end_side.get("boundary_constraint")
            if isinstance(end_side, dict)
            else None
        )
        if end_constraint is not None and reported_constraint != end_constraint:
            errors.append(
                f"boundary_evidence[{position}] end constraint does not match EDL range {range_index}"
            )
        one_frame_exception = (
            isinstance(end_constraint, dict)
            and end_constraint.get("reason") == "disconnected_post_word_activity"
            and type(end_constraint.get("required_tail_frames")) is int
            and end_constraint.get("required_tail_frames") == 2
            and type(end_constraint.get("available_tail_frames")) is int
            and end_constraint.get("available_tail_frames") == 1
        )
        if one_frame_exception and (
            item.get("tail_frames") != 1
            or not isinstance(end_side, dict)
            or end_side.get("tail_guard_frames") != 1
        ):
            errors.append(
                f"boundary_evidence[{position}] one-frame tail exception lacks exact acoustic evidence"
            )

        confidence = item.get("confidence")
        if not isinstance(confidence, dict):
            errors.append(f"boundary_evidence[{position}] confidence is invalid")
            continue
        for side in ("start", "end"):
            value = confidence.get(side)
            if value not in BOUNDARY_CONFIDENCES:
                errors.append(f"boundary_evidence[{position}] has invalid {side} confidence")
            else:
                observed_counts[value] += 1

        expected_review = "low" in {confidence.get("start"), confidence.get("end")}
        if edl_range.get("review_required") is not expected_review:
            errors.append(
                f"EDL range {range_index} review_required is inconsistent with boundary confidence"
            )

    for confidence_name, expected_count in observed_counts.items():
        reported_count = summary.get(confidence_name)
        if (
            not isinstance(reported_count, int)
            or isinstance(reported_count, bool)
            or reported_count != expected_count
        ):
            errors.append(
                f"confidence_summary.{confidence_name}={reported_count!r}, expected {expected_count}"
            )

    lineages_by_event: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for range_index, edl_range in enumerate(edl_ranges):
        lineage = edl_range.get("internal_silence_split")
        if lineage is None:
            continue
        if not isinstance(lineage, dict):
            errors.append(f"EDL range {range_index} internal_silence_split is invalid")
            continue
        event_id = lineage.get("event_id")
        segment_index = lineage.get("segment_index")
        segment_count = lineage.get("segment_count")
        if (
            not isinstance(event_id, str)
            or not event_id
            or lineage.get("policy") != INTERNAL_SILENCE_SPLIT_POLICY
            or not _is_plain_int(segment_index)
            or not _is_plain_int(segment_count)
            or segment_index < 0
            or segment_count < 1
            or segment_index >= segment_count
        ):
            errors.append(f"EDL range {range_index} internal-silence lineage is invalid")
            continue
        audit_event = lineage.get("audit_event")
        if not isinstance(audit_event, dict) or audit_event.get("event_id") != event_id:
            errors.append(
                f"EDL range {range_index} internal-silence lineage lacks a bound audit snapshot"
            )
            continue
        if audit_event.get("source") != edl_range.get("source"):
            errors.append(
                f"EDL range {range_index} internal-silence audit source is stale"
            )
        selection = audit_event.get("original_selection")
        try:
            selection_start = float(selection["start"])
            selection_end = float(selection["end"])
            child_start = float(edl_range.get("original_start", edl_range["start"]))
            child_end = float(edl_range.get("original_end", edl_range["end"]))
            if (
                not all(
                    math.isfinite(value)
                    for value in (selection_start, selection_end, child_start, child_end)
                )
                or selection_start > child_start + 1e-6
                or child_end > selection_end + 1e-6
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(
                f"EDL range {range_index} is not contained by its internal-silence selection"
            )
        lineages_by_event.setdefault(event_id, []).append((range_index, lineage))

    report_events = boundary_data.get("internal_silence_events")
    if report_events is None:
        report_events = []
    if not isinstance(report_events, list):
        errors.append("internal_silence_events is not a list")
        report_events = []
    events_by_id: dict[str, dict[str, object]] = {}
    for event_index, event in enumerate(report_events):
        if not isinstance(event, dict):
            errors.append(f"internal_silence_events[{event_index}] is invalid")
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in events_by_id:
            errors.append(
                f"internal_silence_events[{event_index}] has invalid/duplicate event_id"
            )
            continue
        if (
            not isinstance(event.get("source"), str)
            or event.get("action") not in {"split", "preserved_by_explicit_overrides"}
            or not isinstance(event.get("gaps"), list)
            or event.get("threshold_ms") not in {300.0, 350.0, 500.0}
            or event.get("comparison") != "strictly_greater_than"
            or not _is_plain_int(event.get("occurrence"))
        ):
            errors.append(f"internal_silence_events[{event_index}] is incomplete")
        else:
            try:
                recomputed_id = compute_internal_silence_event_id(
                    event["source"],
                    event.get("original_selection"),
                    event["gaps"],
                    event["occurrence"],
                )
                if recomputed_id != event_id:
                    errors.append(
                        f"internal_silence_events[{event_index}] event_id is not reproducible"
                    )
            except ValueError as exc:
                errors.append(
                    f"internal_silence_events[{event_index}] identity is invalid: {exc}"
                )
        events_by_id[event_id] = event

    for event_id, indexed_lineages in lineages_by_event.items():
        report_event = events_by_id.get(event_id)
        if report_event is None:
            errors.append(f"internal-silence lineage event {event_id} is absent from report")
            continue
        lineages = [lineage for _, lineage in indexed_lineages]
        segment_counts = {lineage.get("segment_count") for lineage in lineages}
        segment_indices = {lineage.get("segment_index") for lineage in lineages}
        split_gaps = [
            gap
            for gap in report_event.get("gaps", [])
            if isinstance(gap, dict) and gap.get("preserve_override") is not True
        ]
        expected_segment_count = len(split_gaps) + 1
        expected_action = (
            "split" if split_gaps else "preserved_by_explicit_overrides"
        )
        if (
            len(segment_counts) != 1
            or next(iter(segment_counts)) != expected_segment_count
            or len(lineages) != expected_segment_count
            or segment_indices != set(range(expected_segment_count))
            or report_event.get("action") != expected_action
        ):
            errors.append(f"internal-silence lineage event {event_id} has incomplete segments")
        ordered_by_edl = sorted(indexed_lineages, key=lambda item: item[0])
        edl_positions = [range_index for range_index, _ in ordered_by_edl]
        edl_segment_indices = [
            lineage.get("segment_index") for _, lineage in ordered_by_edl
        ]
        if (
            edl_positions
            and (
                edl_positions != list(
                    range(edl_positions[0], edl_positions[0] + len(edl_positions))
                )
                or edl_segment_indices != list(range(expected_segment_count))
            )
        ):
            errors.append(
                f"internal-silence lineage event {event_id} segments are reordered in the EDL"
            )
        for range_index, lineage in indexed_lineages:
            audit_event = lineage.get("audit_event")
            if audit_event != report_event:
                errors.append(
                    f"internal-silence lineage event {event_id} audit snapshot is stale"
                )

        preserved_gaps = [
            gap
            for gap in report_event.get("gaps", [])
            if isinstance(gap, dict) and gap.get("preserve_override") is True
        ]
        for range_index, lineage in indexed_lineages:
            edl_range = edl_ranges[range_index]
            anchors = edl_range.get("lexical_anchors")
            first_anchor = anchors.get("first") if isinstance(anchors, dict) else None
            last_anchor = anchors.get("last") if isinstance(anchors, dict) else None
            if not isinstance(first_anchor, dict) or not isinstance(last_anchor, dict):
                if preserved_gaps:
                    errors.append(
                        f"internal-silence lineage event {event_id} lacks lexical anchors"
                    )
                continue
            first_word_index = first_anchor.get("word_index")
            last_word_index = last_anchor.get("word_index")
            if not _is_plain_int(first_word_index) or not _is_plain_int(last_word_index):
                errors.append(
                    f"internal-silence lineage event {event_id} anchor indices are invalid"
                )
                continue
            expected_overrides = [
                {
                    "left_word_index": gap["left_word_index"],
                    "left_word": gap["left_word"],
                    "right_word_index": gap["right_word_index"],
                    "right_word": gap["right_word"],
                    "reason": gap["override_reason"],
                }
                for gap in preserved_gaps
                if first_word_index <= gap.get("left_word_index", -1)
                and gap.get("right_word_index", -1) <= last_word_index
            ]
            constraints = edl_range.get("boundary_constraints") or {}
            reported_overrides = (
                constraints.get("preserve_internal_silences", [])
                if isinstance(constraints, dict)
                else None
            )
            if reported_overrides != expected_overrides:
                errors.append(
                    f"internal-silence lineage event {event_id} preserved overrides are stale"
                )

        if report_event.get("action") == "preserved_by_explicit_overrides":
            range_index = indexed_lineages[0][0]
            edl_range = edl_ranges[range_index]
            selection = report_event.get("original_selection", {})
            try:
                if (
                    abs(float(edl_range.get("original_start", edl_range["start"])) - float(selection["start"])) > 1e-6
                    or abs(float(edl_range.get("original_end", edl_range["end"])) - float(selection["end"])) > 1e-6
                ):
                    errors.append(
                        f"preserved internal-silence event {event_id} selection is stale"
                    )
            except (KeyError, TypeError, ValueError):
                errors.append(
                    f"preserved internal-silence event {event_id} selection is invalid"
                )
        elif len(indexed_lineages) == expected_segment_count:
            ordered = sorted(
                indexed_lineages, key=lambda item: item[1]["segment_index"]
            )
            for gap_index, gap in enumerate(split_gaps):
                left_range = edl_ranges[ordered[gap_index][0]]
                right_range = edl_ranges[ordered[gap_index + 1][0]]
                left_anchors = left_range.get("lexical_anchors")
                right_anchors = right_range.get("lexical_anchors")
                left_anchor = (
                    left_anchors.get("last") if isinstance(left_anchors, dict) else None
                )
                right_anchor = (
                    right_anchors.get("first") if isinstance(right_anchors, dict) else None
                )
                try:
                    linked = (
                        isinstance(left_anchor, dict)
                        and isinstance(right_anchor, dict)
                        and left_anchor.get("word_index") == gap.get("left_word_index")
                        and right_anchor.get("word_index") == gap.get("right_word_index")
                        and normalize_text(str(left_anchor.get("text") or ""))
                        == normalize_text(str(gap.get("left_word") or ""))
                        and normalize_text(str(right_anchor.get("text") or ""))
                        == normalize_text(str(gap.get("right_word") or ""))
                        and abs(float(left_anchor["end"]) - float(gap["left_word_end"])) <= 1e-6
                        and abs(float(right_anchor["start"]) - float(gap["right_word_start"])) <= 1e-6
                    )
                except (KeyError, TypeError, ValueError):
                    linked = False
                if not linked:
                    errors.append(
                        f"internal-silence event {event_id} gap {gap_index} is not linked to EDL anchors"
                    )

    for event_id, event in events_by_id.items():
        if event_id not in lineages_by_event:
            errors.append(f"internal-silence event {event_id} has no EDL lineage")

    policy = boundary_data.get("internal_silence_policy")
    if events_by_id and policy is None:
        errors.append("internal_silence_policy is required when events exist")
    if policy is not None:
        if not isinstance(policy, dict):
            errors.append("internal_silence_policy is invalid")
        else:
            detected_count = sum(
                len(event.get("gaps", []))
                for event in events_by_id.values()
                if isinstance(event.get("gaps"), list)
            )
            split_count = sum(
                sum(gap.get("preserve_override") is not True for gap in event.get("gaps", []))
                for event in events_by_id.values()
                if isinstance(event.get("gaps"), list)
            )
            preserved_count = detected_count - split_count
            if (
                policy.get("policy") not in {INTERNAL_SILENCE_SPLIT_POLICY, "lexical_gap_strictly_gt_300ms_v1", "lexical_gap_strictly_gt_350ms_v1"}
                or policy.get("threshold_ms") not in {300.0, 350.0, 500.0}
                or policy.get("comparison") != "strictly_greater_than"
                or policy.get("detected_gap_count") != detected_count
                or policy.get("split_gap_count") != split_count
                or policy.get("preserved_gap_count") != preserved_count
            ):
                errors.append("internal_silence_policy summary contradicts event evidence")

    status = boundary_data.get("status")
    if status not in KNOWN_STATUSES:
        errors.append("status is missing or unknown")
    else:
        minimum_status = "review" if observed_counts["low"] else "pass"
        if STATUS_SEVERITY[status] < STATUS_SEVERITY[minimum_status]:
            errors.append(
                f"status {status!r} is less severe than confidence evidence requires ({minimum_status})"
            )
    return errors


def validate_audio_report(
    audio_data: object,
    map_ranges: list[dict[str, object]] | None = None,
    fps_value: object | None = None,
) -> list[str]:
    """Return structural/consistency errors for mandatory preview audio gates."""
    errors: list[str] = []
    if not isinstance(audio_data, dict):
        return ["report is not a JSON object"]

    required_bool_fields = (
        "wav_format_ok",
        "sample_count_parity_ok",
        "two_frame_tail_ok",
        "two_frame_tail_coverage_ok",
        "speech_clipping_ok",
        "boundary_clipping_detected",
    )
    for key in required_bool_fields:
        if not isinstance(audio_data.get(key), bool):
            errors.append(f"{key} is missing or not boolean")

    for key in (
        "total_samples",
        "expected_samples",
        "sample_rate",
        "channels",
        "sample_width",
        "two_frame_tail_required_samples",
        "tail_requirement_frames",
        "clipping_events_count",
        "range_review_count",
        "severe_pops_count",
        "warning_pops_count",
    ):
        value = audio_data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{key} is missing or not a non-negative integer")

    expected_wav_format_ok = (
        audio_data.get("sample_rate") == 48000
        and audio_data.get("channels") == 2
        and audio_data.get("sample_width") == 16
    )
    if audio_data.get("wav_format_ok") is not expected_wav_format_ok:
        errors.append("wav_format_ok contradicts sample_rate/channels/sample_width evidence")

    parsed_fps = None
    if map_ranges is not None:
        try:
            parsed_fps = parse_fps_fraction(fps_value)
        except Exception as exc:
            errors.append(f"cannot derive mapped audio tails without valid FPS: {exc}")

    total_samples = audio_data.get("total_samples")
    expected_samples = audio_data.get("expected_samples")
    mapped_total_samples = None
    if map_ranges is not None:
        if map_ranges:
            final_interval = map_ranges[-1].get("output_cumulative_sample_interval")
            if (
                isinstance(final_interval, list)
                and len(final_interval) == 2
                and all(_is_plain_int(value) for value in final_interval)
            ):
                mapped_total_samples = final_interval[1]
            else:
                errors.append("final timeline map cumulative interval is invalid")
        else:
            mapped_total_samples = 0
        if expected_samples != mapped_total_samples:
            errors.append("expected_samples does not match the timeline map endpoint")

    joins = audio_data.get("joins")
    if not isinstance(joins, list):
        errors.append("joins is missing or not a list")
        joins = []
    else:
        if map_ranges is not None and len(joins) != max(0, len(map_ranges) - 1):
            errors.append("join count does not match timeline map")
        severe_count = 0
        warning_count = 0
        for index, join in enumerate(joins):
            if not isinstance(join, dict):
                errors.append(f"joins[{index}] is not an object")
                continue
            join_status = join.get("status")
            if join_status == "severe":
                severe_count += 1
            elif join_status == "warning":
                warning_count += 1
            elif join_status != "pass":
                errors.append(f"joins[{index}] has unknown status")
            if map_ranges is not None and index < len(map_ranges) - 1:
                expected_sample = map_ranges[index].get("output_cumulative_sample_interval")
                expected_sample = expected_sample[1] if isinstance(expected_sample, list) and len(expected_sample) == 2 else None
                if join.get("join_index") != index or join.get("sample_index") != expected_sample:
                    errors.append(f"joins[{index}] identity does not match timeline map")
        if audio_data.get("severe_pops_count") != severe_count:
            errors.append("severe_pops_count does not match join evidence")
        if audio_data.get("warning_pops_count") != warning_count:
            errors.append("warning_pops_count does not match join evidence")

    range_entries = audio_data.get("range_entries")
    if not isinstance(range_entries, list):
        errors.append("range_entries is missing or not a list")
        range_entries = []
    else:
        if map_ranges is not None and len(range_entries) != len(map_ranges):
            errors.append("range_entries count does not match timeline map")
        observed_range_reviews = 0
        seen_range_indices: set[int] = set()
        for position, entry in enumerate(range_entries):
            if not isinstance(entry, dict):
                errors.append(f"range_entries[{position}] is not an object")
                continue
            range_index = entry.get("range_index")
            if (
                not isinstance(range_index, int)
                or isinstance(range_index, bool)
                or range_index < 0
                or range_index in seen_range_indices
            ):
                errors.append(f"range_entries[{position}] has invalid/duplicate range_index")
            else:
                seen_range_indices.add(range_index)
                if map_ranges is not None and position < len(map_ranges):
                    expected_range = map_ranges[position]
                    if range_index != position or entry.get("source") != expected_range.get("source"):
                        errors.append(f"range_entries[{position}] identity does not match timeline map")
            entry_status = entry.get("status")
            flags = entry.get("blocking_flags")
            if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                errors.append(f"range_entries[{position}] blocking_flags is invalid")
                flags = []
            expected_entry_status = "review" if flags else "pass"
            if entry_status != expected_entry_status:
                errors.append(f"range_entries[{position}] status contradicts blocking_flags")
            if entry_status == "review":
                observed_range_reviews += 1

            mapped_expectation = None
            if (
                map_ranges is not None
                and position < len(map_ranges)
                and parsed_fps is not None
                and _is_plain_int(total_samples)
            ):
                expected_range = map_ranges[position]
                try:
                    mapped_expectation = _mapped_tail_expectation(
                        expected_range, parsed_fps, total_samples
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(
                        f"range_entries[{position}] mapped tail cannot be derived: {exc}"
                    )
                expected_interval = expected_range.get(
                    "output_cumulative_sample_interval"
                )
                if entry.get("output_sample_interval") != expected_interval:
                    errors.append(
                        f"range_entries[{position}] output interval does not match timeline map"
                    )

            if mapped_expectation is not None:
                if entry.get("tail_requirement_frames") != mapped_expectation["frames"]:
                    errors.append(
                        f"range_entries[{position}] tail frame requirement does not match mapped constraints"
                    )
                if (
                    entry.get("two_frame_tail_required_samples")
                    != mapped_expectation["samples"]
                ):
                    errors.append(
                        f"range_entries[{position}] tail sample requirement does not match mapped FPS"
                    )
                if (
                    entry.get("two_frame_tail_coverage_ok")
                    is not mapped_expectation["coverage"]
                ):
                    errors.append(
                        f"range_entries[{position}] tail coverage contradicts mapped evidence"
                    )

                entry_rms = entry.get("two_frame_tail_rms_db")
                entry_rms_by_channel = entry.get("two_frame_tail_rms_db_by_channel")
                entry_threshold = entry.get("two_frame_tail_threshold_dbfs")
                activity_threshold = entry.get("activity_threshold_dbfs")
                activity_threshold_valid = (
                    isinstance(activity_threshold, (int, float))
                    and not isinstance(activity_threshold, bool)
                    and math.isfinite(float(activity_threshold))
                )
                expected_entry_threshold = (
                    max(-60.0, min(-50.0, float(activity_threshold)))
                    if activity_threshold_valid
                    else None
                )
                if (
                    expected_entry_threshold is None
                    or entry_threshold != expected_entry_threshold
                ):
                    errors.append(
                        f"range_entries[{position}] tail threshold contradicts activity evidence"
                    )
                entry_rms_valid = (
                    isinstance(entry_rms, (int, float))
                    and not isinstance(entry_rms, bool)
                    and math.isfinite(float(entry_rms))
                    and isinstance(entry_rms_by_channel, list)
                    and len(entry_rms_by_channel) == audio_data.get("channels")
                    and len(entry_rms_by_channel) > 0
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                        for value in entry_rms_by_channel
                    )
                    and isinstance(entry_threshold, (int, float))
                    and not isinstance(entry_threshold, bool)
                    and math.isfinite(float(entry_threshold))
                    and -60.0 <= float(entry_threshold) <= -50.0
                )
                if not entry_rms_valid:
                    errors.append(f"range_entries[{position}] tail RMS evidence is invalid")
                else:
                    worst_entry_rms = max(float(value) for value in entry_rms_by_channel)
                    if abs(float(entry_rms) - worst_entry_rms) > 1e-6:
                        errors.append(
                            f"range_entries[{position}] tail RMS does not equal its worst channel"
                        )
                    expected_left_tail_ok = bool(
                        mapped_expectation["coverage"]
                        and expected_entry_threshold is not None
                        and worst_entry_rms < expected_entry_threshold
                    )
                    if entry.get("left_tail_ok") is not expected_left_tail_ok:
                        errors.append(
                            f"range_entries[{position}] left_tail_ok contradicts mapped/RMS evidence"
                        )
                    has_tail_flag = "active_or_uncovered_two_frame_tail" in flags
                    if has_tail_flag is expected_left_tail_ok:
                        errors.append(
                            f"range_entries[{position}] tail blocker contradicts mapped/RMS evidence"
                        )
        if audio_data.get("range_review_count") != observed_range_reviews:
            errors.append("range_review_count does not match range entry evidence")

    if (
        isinstance(total_samples, int)
        and not isinstance(total_samples, bool)
        and isinstance(expected_samples, int)
        and not isinstance(expected_samples, bool)
    ):
        allowed_discrepancy = max(1, len(joins))
        expected_parity_ok = abs(total_samples - expected_samples) <= allowed_discrepancy
        if audio_data.get("sample_count_parity_ok") is not expected_parity_ok:
            errors.append("sample_count_parity_ok contradicts total/expected sample evidence")

    mapped_final_tail = None
    if (
        map_ranges
        and parsed_fps is not None
        and _is_plain_int(total_samples)
    ):
        try:
            mapped_final_tail = _mapped_tail_expectation(
                map_ranges[-1], parsed_fps, total_samples
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"global mapped tail cannot be derived: {exc}")

    tail_window_valid = False
    if mapped_final_tail is not None:
        if audio_data.get("tail_requirement_frames") != mapped_final_tail["frames"]:
            errors.append("tail_requirement_frames does not match mapped constraints")
        if (
            audio_data.get("two_frame_tail_required_samples")
            != mapped_final_tail["samples"]
        ):
            errors.append("two_frame_tail_required_samples does not match mapped FPS")
        if audio_data.get("two_frame_tail_window") != mapped_final_tail["window"]:
            errors.append("two_frame_tail_window does not match mapped endpoint")
        tail_window_valid = bool(mapped_final_tail["coverage"])
    if audio_data.get("two_frame_tail_coverage_ok") is not tail_window_valid:
        errors.append("two_frame_tail_coverage_ok contradicts mapped tail window evidence")

    tail_rms = audio_data.get("tail_rms_db")
    tail_rms_by_channel = audio_data.get("tail_rms_db_by_channel")
    tail_rms_evidence_valid = (
        isinstance(tail_rms, (int, float))
        and not isinstance(tail_rms, bool)
        and math.isfinite(float(tail_rms))
        and isinstance(tail_rms_by_channel, list)
        and len(tail_rms_by_channel) > 0
        and len(tail_rms_by_channel) == audio_data.get("channels")
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in tail_rms_by_channel
        )
    )
    if not tail_rms_evidence_valid:
        errors.append("tail RMS evidence is missing or invalid")
        expected_tail_ok = False
    else:
        worst_channel_rms = max(float(value) for value in tail_rms_by_channel)
        if abs(float(tail_rms) - worst_channel_rms) > 1e-6:
            errors.append("tail_rms_db does not equal the worst per-channel tail RMS")
        expected_tail_ok = tail_window_valid and worst_channel_rms < -60.0
    if audio_data.get("two_frame_tail_ok") is not expected_tail_ok:
        errors.append("two_frame_tail_ok contradicts coverage/RMS evidence")

    expected_speech_clipping_ok = audio_data.get("boundary_clipping_detected") is False
    if audio_data.get("speech_clipping_ok") is not expected_speech_clipping_ok:
        errors.append("speech_clipping_ok contradicts boundary_clipping_detected")

    structural_failure = (
        audio_data.get("wav_format_ok") is False
        or audio_data.get("sample_count_parity_ok") is False
        or audio_data.get("two_frame_tail_coverage_ok") is False
    )
    review_failure = (
        audio_data.get("two_frame_tail_ok") is False
        or audio_data.get("speech_clipping_ok") is False
        or audio_data.get("boundary_clipping_detected") is True
        or (isinstance(audio_data.get("severe_pops_count"), int) and audio_data.get("severe_pops_count", 0) > 0)
        or (isinstance(audio_data.get("range_review_count"), int) and audio_data.get("range_review_count", 0) > 0)
    )
    warning_present = (
        isinstance(audio_data.get("warning_pops_count"), int)
        and audio_data.get("warning_pops_count", 0) > 0
    )
    minimum_status = "fail" if structural_failure else (
        "review" if review_failure else ("warning" if warning_present else "pass")
    )
    status = audio_data.get("status")
    if status not in KNOWN_STATUSES:
        errors.append("status is missing or unknown")
    elif STATUS_SEVERITY[status] < STATUS_SEVERITY[minimum_status]:
        errors.append(
            f"status {status!r} is less severe than mandatory checks require ({minimum_status})"
        )
    return errors


def _canonical_source_words(
    transcript: dict[str, object], source_id: str
) -> list[dict[str, object]]:
    raw_words = transcript.get("words")
    if not isinstance(raw_words, list):
        raise ValueError(f"source transcript {source_id!r} words are invalid")
    words = [
        dict(word)
        for word in raw_words
        if isinstance(word, dict) and word.get("type") == "word"
    ]
    try:
        words.sort(key=lambda word: (float(word["start"]), float(word["end"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"source transcript {source_id!r} has invalid word timing") from exc
    if not words:
        raise ValueError(f"source transcript {source_id!r} has no canonical words")
    return words


def _validate_lexical_anchors(
    range_index: int,
    edl_range: dict[str, object],
    map_range: dict[str, object],
    source_words: list[dict[str, object]],
) -> tuple[int, int]:
    edl_anchors = edl_range.get("lexical_anchors")
    map_anchors = map_range.get("lexical_anchors")
    if not isinstance(edl_anchors, dict):
        raise ValueError("EDL lexical_anchors are missing or invalid")
    if map_anchors != edl_anchors:
        raise ValueError("timeline map lexical_anchors do not match the EDL")
    first_anchor = edl_anchors.get("first")
    last_anchor = edl_anchors.get("last")
    if not isinstance(first_anchor, dict) or not isinstance(last_anchor, dict):
        raise ValueError("EDL lexical anchor endpoints are invalid")
    first_index = first_anchor.get("word_index")
    last_index = last_anchor.get("word_index")
    if (
        not _is_plain_int(first_index)
        or not _is_plain_int(last_index)
        or first_index < 0
        or last_index < first_index
        or last_index >= len(source_words)
    ):
        raise ValueError("EDL lexical anchor indices are invalid")
    for label, anchor, canonical in (
        ("first", first_anchor, source_words[first_index]),
        ("last", last_anchor, source_words[last_index]),
    ):
        if normalize_text(str(anchor.get("text") or "")) != normalize_text(
            str(canonical.get("text") or "")
        ):
            raise ValueError(f"{label} anchor text is stale")
        for timestamp in ("start", "end"):
            try:
                anchor_value = float(anchor[timestamp])
                canonical_value = float(canonical[timestamp])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{label} anchor {timestamp} is invalid") from exc
            if not math.isfinite(anchor_value) or abs(anchor_value - canonical_value) > 1e-3:
                raise ValueError(f"{label} anchor {timestamp} is stale")
    return first_index, last_index


def _project_expected_words(
    source_words: list[dict[str, object]],
    first_index: int,
    last_index: int,
    map_range: dict[str, object],
) -> list[dict[str, object]]:
    source_interval = map_range.get("source_sample_interval")
    output_interval = map_range.get("output_cumulative_sample_interval")
    if (
        not isinstance(source_interval, list)
        or len(source_interval) != 2
        or not all(_is_plain_int(value) for value in source_interval)
        or not isinstance(output_interval, list)
        or len(output_interval) != 2
        or not all(_is_plain_int(value) for value in output_interval)
    ):
        raise ValueError("timeline map sample intervals are invalid")
    expected_records: list[dict[str, object]] = []
    for word_index in range(first_index, last_index + 1):
        source_word = source_words[word_index]
        projected_start = (
            output_interval[0]
            + int(round(float(source_word["start"]) * 48000))
            - source_interval[0]
        )
        projected_end = (
            output_interval[0]
            + int(round(float(source_word["end"]) * 48000))
            - source_interval[0]
        )
        for token_index, normalized_token in enumerate(
            tokenize(str(source_word.get("text") or ""))
        ):
            expected_records.append({
                "text": source_word.get("text"),
                "normalized": normalized_token,
                "source_word_index": word_index,
                "token_index": token_index,
                "source_start": float(source_word["start"]),
                "source_end": float(source_word["end"]),
                "timeline_start_sample": projected_start,
                "timeline_end_sample": projected_end,
            })
    return expected_records


def _assign_preview_words(
    preview_transcript: dict[str, object],
    map_ranges: list[dict[str, object]],
    fps_value: object | None = None,
) -> dict[str, object]:
    raw_words = preview_transcript.get("words")
    if not isinstance(raw_words, list):
        raise ValueError("preview transcript words are invalid")
    preview_words = [
        item
        for item in raw_words
        if isinstance(item, dict) and item.get("type") == "word"
    ]
    actual_by_range: list[list[dict[str, object]]] = [[] for _ in map_ranges]
    untimed_words: list[dict[str, object]] = []
    invalid_words: list[dict[str, object]] = []
    out_of_bounds_words: list[dict[str, object]] = []
    spanning_by_join: list[list[dict[str, object]]] = [
        [] for _ in range(max(0, len(map_ranges) - 1))
    ]
    try:
        resolved_fps = parse_fps_fraction(fps_value)
    except Exception:
        resolved_fps = None
        for map_range in map_ranges:
            frames = map_range.get("source_frames")
            samples = map_range.get("source_sample_interval")
            if (
                isinstance(frames, list)
                and len(frames) == 2
                and all(_is_plain_int(value) for value in frames)
                and isinstance(samples, list)
                and len(samples) == 2
                and all(_is_plain_int(value) for value in samples)
                and frames[1] > frames[0]
                and samples[1] > samples[0]
            ):
                resolved_fps = Fraction(
                    (frames[1] - frames[0]) * 48000,
                    samples[1] - samples[0],
                ).limit_denominator(1001)
                break
        if resolved_fps is None:
            raise ValueError("preview join FPS cannot be derived")
    tolerance_samples = frame_to_sample(2, resolved_fps)

    for preview_word_index, item in enumerate(preview_words):
        start = item.get("start")
        end = item.get("end")
        if start is None or end is None:
            untimed_words.append({
                "word_index": preview_word_index,
                "text": item.get("text"),
            })
            continue
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0.0
            or float(end) <= float(start)
        ):
            invalid_words.append({
                "word_index": preview_word_index,
                "text": item.get("text"),
                "start": start,
                "end": end,
            })
            continue
        start_sample = int(round(float(start) * 48000))
        end_sample = int(round(float(end) * 48000))
        midpoint = (start_sample + end_sample) // 2
        assigned_range = None
        for range_index, map_range in enumerate(map_ranges):
            interval = map_range.get("output_cumulative_sample_interval")
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or not all(_is_plain_int(value) for value in interval)
            ):
                raise ValueError(f"timeline map range {range_index} interval is invalid")
            if interval[0] <= midpoint < interval[1] or (
                range_index == len(map_ranges) - 1 and midpoint == interval[1]
            ):
                assigned_range = range_index
                break
        if assigned_range is None:
            out_of_bounds_words.append({
                "word_index": preview_word_index,
                "text": item.get("text"),
                "start": start,
                "end": end,
            })
            continue
        for token_index, normalized_token in enumerate(
            tokenize(str(item.get("text") or ""))
        ):
            actual_by_range[assigned_range].append({
                "text": item.get("text"),
                "normalized": normalized_token,
                "preview_word_index": preview_word_index,
                "token_index": token_index,
                "start": float(start),
                "end": float(end),
                "start_sample": start_sample,
                "end_sample": end_sample,
                "assigned_range": assigned_range,
            })

        for join_index in range(len(map_ranges) - 1):
            interval = map_ranges[join_index].get("output_cumulative_sample_interval")
            join_sample = interval[1]
            if (
                start_sample < join_sample - tolerance_samples
                and end_sample > join_sample + tolerance_samples
            ):
                spanning_by_join[join_index].append({
                    "word_index": preview_word_index,
                    "text": item.get("text"),
                    "start": float(start),
                    "end": float(end),
                })

    return {
        "actual_by_range": actual_by_range,
        "timing_validation": {
            "untimed_words": untimed_words,
            "invalid_words": invalid_words,
            "out_of_bounds_words": out_of_bounds_words,
        },
        "spanning_by_join": spanning_by_join,
        "fps": resolved_fps,
    }


def validate_transcript_report(
    transcript_data: object,
    edl_ranges: list[dict[str, object]],
    map_ranges: list[dict[str, object]],
    source_transcripts: dict[str, dict[str, object]],
    preview_transcript: dict[str, object] | None = None,
    fps_value: object | None = None,
) -> list[str]:
    """Recompute the mandatory range/join status from report evidence."""
    errors: list[str] = []
    if not isinstance(transcript_data, dict):
        return ["report is not a JSON object"]
    if transcript_data.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    if transcript_data.get("mode") != "range_aware":
        errors.append("mode must be range_aware")

    ranges = transcript_data.get("ranges")
    joins = transcript_data.get("joins")
    internal_silence_checks = transcript_data.get("internal_silence_checks")
    residual_checks = transcript_data.get("interword_residual_checks")
    summary = transcript_data.get("summary")
    timing = transcript_data.get("timing_validation")
    if not isinstance(ranges, list):
        errors.append("ranges is missing or not a list")
        ranges = []
    if not isinstance(joins, list):
        errors.append("joins is missing or not a list")
        joins = []
    if not isinstance(internal_silence_checks, list):
        errors.append("internal_silence_checks is missing or not a list")
        internal_silence_checks = []
    if not isinstance(residual_checks, list):
        errors.append("interword_residual_checks is missing or not a list")
        residual_checks = []
    if not isinstance(summary, dict):
        errors.append("summary is missing or not an object")
        summary = {}
    if not isinstance(timing, dict):
        errors.append("timing_validation is missing or not an object")
        timing = {}

    if len(ranges) != len(edl_ranges):
        errors.append("range count does not match EDL")
    expected_join_count = max(0, len(edl_ranges) - 1)
    if len(joins) != expected_join_count:
        errors.append("join count does not equal range count minus one")

    range_review_count = 0
    join_review_count = 0
    internal_silence_review_count = 0
    residual_review_count = 0
    evidence_flags: list[str] = []

    if preview_transcript is not None:
        raw_preview_words = preview_transcript.get("words")
        if not isinstance(raw_preview_words, list):
            errors.append("preview sidecar words are invalid")
        else:
            canonical_words_evidence = [
                {
                    "text": word.get("text"),
                    "type": word.get("type"),
                    "start": word.get("start"),
                    "end": word.get("end"),
                }
                for word in raw_preview_words
                if isinstance(word, dict)
            ]
            if transcript_data.get("words_evidence") != canonical_words_evidence:
                errors.append("words_evidence does not match the bound preview sidecar")
    if preview_transcript is None:
        words_evidence = transcript_data.get("words_evidence")
        preview_transcript = {
            "words": words_evidence if isinstance(words_evidence, list) else []
        }
    preview_evidence: dict[str, object] = {}
    try:
        preview_evidence = _assign_preview_words(
            preview_transcript, map_ranges, fps_value
        )
        actual_by_range = preview_evidence["actual_by_range"]
        recomputed_timing = preview_evidence["timing_validation"]
        spanning_by_join = preview_evidence["spanning_by_join"]
        if timing != recomputed_timing:
            errors.append("timing_validation does not match the bound preview sidecar")
        timing = recomputed_timing
    except ValueError as exc:
        errors.append(f"cannot recompute preview range words: {exc}")
        actual_by_range = [[] for _ in map_ranges]
        spanning_by_join = [[] for _ in range(max(0, len(map_ranges) - 1))]

    source_words_by_id: dict[str, list[dict[str, object]]] = {}
    for source_id, source_transcript in source_transcripts.items():
        try:
            source_words_by_id[source_id] = _canonical_source_words(
                source_transcript, source_id
            )
        except ValueError as exc:
            errors.append(str(exc))

    settings = transcript_data.get("settings")
    cue_terms = settings.get("cue_terms") if isinstance(settings, dict) else None
    if (
        not isinstance(cue_terms, list)
        or not cue_terms
        or not all(isinstance(term, str) and term.strip() for term in cue_terms)
    ):
        errors.append("settings.cue_terms is missing or invalid")
        declared_cue_terms: list[str] = []
    else:
        declared_cue_terms = cue_terms
    if not set(DEFAULT_CUE_TERMS).issubset(declared_cue_terms):
        errors.append("settings.cue_terms omits mandatory recording cues")
    cue_terms = list(dict.fromkeys(list(DEFAULT_CUE_TERMS) + declared_cue_terms))
    resolved_preview_fps = preview_evidence.get("fps")
    try:
        canonical_report = build_report(
            preview_transcript,
            None,
            None,
            cue_terms,
            edl_data={"ranges": edl_ranges},
            timeline_map={
                "output_format": {
                    "sample_rate": 48000,
                    "sequence_fps": str(resolved_preview_fps),
                },
                "ranges": map_ranges,
            },
            source_transcripts=source_transcripts,
        )
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"cannot recompute canonical preview report: {exc}")
        canonical_report = None
    if canonical_report is not None:
        for key in (
            "settings",
            "summary",
            "words_evidence",
            "text",
            "adjacent_repeats",
            "repeated_ngrams",
            "cue_hits",
            "expected_diff",
            "timing_validation",
            "ranges",
            "joins",
            "internal_silence_checks",
            "interword_residual_checks",
            "status",
        ):
            if transcript_data.get(key) != canonical_report.get(key):
                errors.append(
                    f"{key} does not match canonical EDL/map/sidecar analysis"
                )

    range_contexts: list[
        tuple[list[dict[str, object]], int, int, list[dict[str, object]]] | None
    ] = []
    for position, (edl_range, map_range) in enumerate(zip(edl_ranges, map_ranges)):
        source_id = edl_range.get("source")
        if (
            not isinstance(source_id, str)
            or map_range.get("source") != source_id
            or source_id not in source_words_by_id
        ):
            errors.append(f"range {position} source is invalid")
            range_contexts.append(None)
            continue
        edl_constraints = edl_range.get("boundary_constraints") or {}
        map_constraints = map_range.get("boundary_constraints") or {}
        if (
            not isinstance(edl_constraints, dict)
            or not isinstance(map_constraints, dict)
            or edl_constraints != map_constraints
        ):
            errors.append(f"range {position} timeline map boundary_constraints are stale")
        source_words = source_words_by_id[source_id]
        try:
            first_index, last_index = _validate_lexical_anchors(
                position, edl_range, map_range, source_words
            )
            expected_records = _project_expected_words(
                source_words, first_index, last_index, map_range
            )
        except ValueError as exc:
            errors.append(f"range {position} lexical contract is invalid: {exc}")
            range_contexts.append(None)
            continue
        range_contexts.append(
            (source_words, first_index, last_index, expected_records)
        )

    for position, result in enumerate(ranges):
        if not isinstance(result, dict):
            errors.append(f"ranges[{position}] is not an object")
            continue
        reported_flags = result.get("blocking_flags")
        if not isinstance(reported_flags, list) or not all(
            isinstance(flag, str) for flag in reported_flags
        ):
            errors.append(f"ranges[{position}] blocking_flags is invalid")
            reported_flags = []
        if position < len(edl_ranges) and position < len(map_ranges):
            if (
                result.get("range_index") != position
                or result.get("source") != edl_ranges[position].get("source")
                or result.get("source") != map_ranges[position].get("source")
            ):
                errors.append(f"ranges[{position}] identity does not match EDL/timeline map")

        canonical_flags = reported_flags
        if position < len(range_contexts) and range_contexts[position] is not None:
            expected_records = range_contexts[position][3]
            actual_records = (
                actual_by_range[position] if position < len(actual_by_range) else []
            )
            alignment = align_token_records(expected_records, actual_records)
            matched = sum(
                operation["op"] in {"equal", "fuzzy"} for operation in alignment
            )
            phrase_similarity = round(
                token_ratio(
                    " ".join(str(record["normalized"]) for record in expected_records),
                    " ".join(str(record["normalized"]) for record in actual_records),
                ),
                3,
            )
            recall = round(
                matched / len(expected_records) if expected_records else 1.0, 3
            )
            duplicate_insertions = duplicate_insertion_spans(alignment)
            token_excess = duplicate_token_excess(expected_records, actual_records)
            canonical_flags = []
            if phrase_similarity < 0.85:
                canonical_flags.append("range_phrase_mismatch")
            if recall < 0.90:
                canonical_flags.append("range_token_recall_low")
            if duplicate_insertions or token_excess:
                canonical_flags.append("range_duplicate_content")

            canonical_fields = {
                "expected_words": expected_records,
                "actual_words": actual_records,
                "alignment": alignment,
                "duplicate_insertions": duplicate_insertions,
                "duplicate_token_excess": token_excess,
                "phrase_similarity": phrase_similarity,
                "token_recall": recall,
                "blocking_flags": canonical_flags,
                "status": "review" if canonical_flags else "pass",
            }
            for key, expected_value in canonical_fields.items():
                if result.get(key) != expected_value:
                    errors.append(
                        f"ranges[{position}] {key} does not match canonical sidecar evidence"
                    )

        expected_status = "review" if canonical_flags else "pass"
        if result.get("status") != expected_status:
            errors.append(f"ranges[{position}] status contradicts canonical evidence")
        if expected_status == "review":
            range_review_count += 1
        evidence_flags.extend(canonical_flags)

    for position, result in enumerate(joins):
        if not isinstance(result, dict):
            errors.append(f"joins[{position}] is not an object")
            continue
        flags = result.get("blocking_flags")
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            errors.append(f"joins[{position}] blocking_flags is invalid")
            flags = []
        expected_status = "review" if flags else "pass"
        if result.get("status") != expected_status:
            errors.append(f"joins[{position}] status contradicts blocking_flags")
        if expected_status == "review":
            join_review_count += 1
        evidence_flags.extend(flags)
        if position < expected_join_count:
            interval = map_ranges[position].get("output_cumulative_sample_interval")
            expected_sample = interval[1] if isinstance(interval, list) and len(interval) == 2 else None
            if (
                result.get("join_index") != position
                or result.get("left_range_index") != position
                or result.get("right_range_index") != position + 1
                or result.get("timeline_sample") != expected_sample
            ):
                errors.append(f"joins[{position}] identity does not match timeline map")
            expected_spanning = spanning_by_join[position]
            actual_evidence = result.get("actual")
            reported_spanning = (
                actual_evidence.get("spanning_words")
                if isinstance(actual_evidence, dict)
                else None
            )
            if reported_spanning != expected_spanning:
                errors.append(
                    f"joins[{position}] spanning words do not match bound preview sidecar"
                )
            has_spanning_flag = "preview_word_spans_join" in flags
            if has_spanning_flag is not bool(expected_spanning):
                errors.append(
                    f"joins[{position}] spanning-word blocker contradicts sidecar evidence"
                )

    def silence_identity(result: object) -> tuple[object, ...] | None:
        if not isinstance(result, dict):
            return None
        try:
            range_index = result["range_index"]
            left_index = result["left_word_index"]
            right_index = result["right_word_index"]
            if not all(
                _is_plain_int(value)
                for value in (range_index, left_index, right_index)
            ):
                return None
            return (
                range_index,
                str(result["source"]),
                left_index,
                str(result.get("left_word") or ""),
                round(float(result["left_word_end"]), 6),
                right_index,
                str(result.get("right_word") or ""),
                round(float(result["right_word_start"]), 6),
                round(float(result["gap_ms"]), 6),
                result.get("preserve_override"),
                result.get("override_reason"),
                tuple(result.get("blocking_flags", [])),
                result.get("status"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    for position, result in enumerate(internal_silence_checks):
        if not isinstance(result, dict):
            errors.append(f"internal_silence_checks[{position}] is not an object")
            continue
        flags = result.get("blocking_flags")
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            errors.append(f"internal_silence_checks[{position}] blocking_flags is invalid")
            flags = []
        preserve_override = result.get("preserve_override")
        expected_flags = [] if preserve_override is True else ["uncut_internal_silence"]
        if flags != expected_flags:
            errors.append(
                f"internal_silence_checks[{position}] blockers contradict preservation evidence"
            )
        expected_status = "review" if flags else "pass"
        if result.get("status") != expected_status:
            errors.append(
                f"internal_silence_checks[{position}] status contradicts blocking_flags"
            )
        if result.get("policy") not in {INTERNAL_SILENCE_SPLIT_POLICY, "lexical_gap_strictly_gt_300ms_v1", "lexical_gap_strictly_gt_350ms_v1"}:
            errors.append(f"internal_silence_checks[{position}] policy is invalid")
        if result.get("threshold_ms") not in {300.0, 350.0, 500.0}:
            errors.append(f"internal_silence_checks[{position}] threshold is invalid")
        if result.get("comparison") != "strictly_greater_than":
            errors.append(f"internal_silence_checks[{position}] comparison is invalid")
        if expected_status == "review":
            internal_silence_review_count += 1
        evidence_flags.extend(flags)

    expected_internal_silence_checks: list[dict[str, object]] = []
    for range_index, (edl_range, map_range) in enumerate(zip(edl_ranges, map_ranges)):
        source_id = edl_range.get("source")
        context = range_contexts[range_index] if range_index < len(range_contexts) else None
        if context is None:
            errors.append(f"cannot recompute internal silence contract for range {range_index}")
            continue
        source_words, first_index, last_index, _ = context
        try:
            evaluated = evaluate_internal_silence_contract(
                edl_range, source_words, first_index, last_index
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                f"range {range_index} internal silence contract is invalid: {exc}"
            )
            continue
        for gap in evaluated:
            flags = [] if gap["preserve_override"] else ["uncut_internal_silence"]
            expected_internal_silence_checks.append({
                "range_index": range_index,
                "source": source_id,
                **gap,
                "blocking_flags": flags,
                "status": "review" if flags else "pass",
            })

    observed_silence_identities = [
        silence_identity(result) for result in internal_silence_checks
    ]
    expected_silence_identities = [
        silence_identity(result) for result in expected_internal_silence_checks
    ]
    if None in observed_silence_identities:
        errors.append("internal silence check identity is invalid")
    if observed_silence_identities != expected_silence_identities:
        errors.append("internal silence checks do not match canonical source evidence")

    for position, result in enumerate(residual_checks):
        if not isinstance(result, dict):
            errors.append(f"interword_residual_checks[{position}] is not an object")
            continue
        flags = result.get("blocking_flags")
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            errors.append(f"interword_residual_checks[{position}] blocking_flags is invalid")
            flags = []
        selection_status = result.get("selection_status")
        if selection_status == "selected":
            range_index = result.get("range_index")
            checks = result.get("checks")
            if (
                not isinstance(range_index, int)
                or isinstance(range_index, bool)
                or range_index < 0
                or range_index >= len(map_ranges)
            ):
                errors.append(f"interword_residual_checks[{position}] range identity is invalid")
            if not isinstance(checks, dict) or not all(
                checks.get(key) is True
                for key in (
                    "neighbors_faithful",
                    "neighbors_consecutive",
                    "no_preview_word_overlap",
                )
            ):
                if "selected_interword_residual_unresolved" not in flags:
                    errors.append(
                        f"interword_residual_checks[{position}] omits unresolved evidence"
                    )
        elif selection_status == "outside_selection":
            if flags:
                errors.append(
                    f"interword_residual_checks[{position}] outside selection has blockers"
                )
        elif "selected_interword_residual_unresolved" not in flags:
            errors.append(
                f"interword_residual_checks[{position}] invalid evidence is not blocking"
            )
        expected_status = "review" if flags else "pass"
        if result.get("status") != expected_status:
            errors.append(
                f"interword_residual_checks[{position}] status contradicts blocking_flags"
            )
        if expected_status == "review":
            residual_review_count += 1
        evidence_flags.extend(flags)

    def residual_identity(
        source_id: object,
        residual_index: object,
        component: object,
        previous: object,
        following: object,
    ) -> tuple[str, int, int, float, float, int, int] | None:
        if (
            not isinstance(source_id, str)
            or not isinstance(residual_index, int)
            or isinstance(residual_index, bool)
            or not isinstance(component, dict)
            or not isinstance(previous, dict)
            or not isinstance(following, dict)
        ):
            return None
        try:
            return (
                source_id,
                residual_index,
                int(component["index"]),
                round(float(component["start"]), 6),
                round(float(component["end"]), 6),
                int(previous["index"]),
                int(following["index"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    expected_residual_identities = []
    for source_id, source_transcript in source_transcripts.items():
        metadata = source_transcript.get("_alano_cut")
        acoustic = metadata.get("acoustic_timing") if isinstance(metadata, dict) else None
        residuals = acoustic.get("nonblocking_outliers", []) if isinstance(acoustic, dict) else []
        if not isinstance(residuals, list):
            errors.append(f"source transcript {source_id!r} residual evidence is invalid")
            continue
        for residual_index, residual in enumerate(residuals):
            if not isinstance(residual, dict) or residual.get("type") != "nonblocking_interword_residual":
                continue
            identity = residual_identity(
                source_id,
                residual_index,
                residual.get("component"),
                residual.get("previous_word"),
                residual.get("following_word"),
            )
            if identity is None:
                errors.append(f"source transcript {source_id!r} residual identity is invalid")
            else:
                expected_residual_identities.append(identity)

    observed_residual_identities = []
    for position, result in enumerate(residual_checks):
        if not isinstance(result, dict):
            continue
        identity = residual_identity(
            result.get("source"),
            result.get("residual_index"),
            result.get("source_component"),
            result.get("previous_word"),
            result.get("following_word"),
        )
        if identity is None:
            errors.append(f"interword_residual_checks[{position}] identity is invalid")
        else:
            observed_residual_identities.append(identity)
    if sorted(observed_residual_identities) != sorted(expected_residual_identities):
        errors.append("interword residual checks do not match source transcript evidence")

    timing_flag_map = {
        "untimed_words": {"incomplete_word_timestamps", "missing_timed_words"},
        "invalid_words": {"invalid_preview_word_timestamps"},
        "out_of_bounds_words": {"preview_words_out_of_bounds"},
    }
    timing_required_flags: list[set[str]] = []
    for key, report_flags in timing_flag_map.items():
        if not isinstance(timing.get(key), list):
            errors.append(f"timing_validation.{key} is invalid")
        elif timing.get(key):
            timing_required_flags.append(report_flags)

    expected_summary = {
        "range_count": len(ranges),
        "range_pass_count": len(ranges) - range_review_count,
        "range_review_count": range_review_count,
        "join_count": len(joins),
        "join_pass_count": len(joins) - join_review_count,
        "join_review_count": join_review_count,
        "internal_silence_count": len(internal_silence_checks),
        "internal_silence_pass_count": (
            len(internal_silence_checks) - internal_silence_review_count
        ),
        "internal_silence_review_count": internal_silence_review_count,
        "interword_residual_count": len(residual_checks),
        "interword_residual_pass_count": len(residual_checks) - residual_review_count,
        "interword_residual_review_count": residual_review_count,
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            errors.append(f"summary.{key} does not match report evidence")

    summary_flags = summary.get("blocking_flags")
    if not isinstance(summary_flags, list) or not all(isinstance(flag, str) for flag in summary_flags):
        errors.append("summary.blocking_flags is invalid")
        summary_flags = []
    for allowed_flags in timing_required_flags:
        if not allowed_flags.intersection(summary_flags):
            errors.append("summary.blocking_flags omits timing evidence")
    missing_evidence_flags = set(evidence_flags) - set(summary_flags)
    if missing_evidence_flags:
        errors.append("summary.blocking_flags omits range/join/timing evidence")
    expected_status = "review" if summary_flags else "pass"
    if summary.get("status") != expected_status:
        errors.append("summary.status contradicts blocking_flags")
    if transcript_data.get("status") != expected_status:
        errors.append("top-level status contradicts summary evidence")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify if edit is ready for XML export")
    ap.add_argument("edl", type=Path, nargs="?", default=Path("edit/edl.json"), help="Path to edl.json")
    ap.add_argument("--transcripts", type=Path, default=None, help="Path to transcripts directory")
    ap.add_argument("--boundary-report", type=Path, default=None, help="Path to edl_boundary_qc.json")
    ap.add_argument("--audio-report", type=Path, default=None, help="Path to preview_audio_qc.json")
    ap.add_argument("--semantic-report", type=Path, default=None, help="Path to edl_semantic_qc.json")
    ap.add_argument("--transcript-report", type=Path, default=None, help="Path to preview_transcript_qc.json")
    ap.add_argument("--audio", type=Path, default=None, help="Path to preview.wav")
    ap.add_argument("--timeline-map", type=Path, default=None, help="Path to preview_timeline.json")

    args = ap.parse_args()

    edl_path = args.edl.resolve()
    edit_dir = edl_path.parent
    transcripts_dir = args.transcripts.resolve() if args.transcripts else edit_dir / "transcripts"
    boundary_path = args.boundary_report.resolve() if args.boundary_report else edit_dir / "edl_boundary_qc.json"
    audio_path = args.audio_report.resolve() if args.audio_report else edit_dir / "preview_audio_qc.json"
    semantic_path = args.semantic_report.resolve() if args.semantic_report else edit_dir / "edl_semantic_qc.json"
    transcript_path = args.transcript_report.resolve() if args.transcript_report else edit_dir / "preview_transcript_qc.json"
    wav_path = args.audio.resolve() if args.audio else edit_dir / "preview.wav"
    map_path = args.timeline_map.resolve() if args.timeline_map else edit_dir / "preview_timeline.json"

    print("=== Alano Cut Edit Readiness Gate ===")

    # 1. Verify existence of core edit files
    if not edl_path.exists():
        print(f"FATAL: EDL file not found at {edl_path}")
        sys.exit(1)

    if not wav_path.exists():
        print(f"FATAL: Preview WAV file not found at {wav_path}")
        sys.exit(1)

    if not map_path.exists():
        print(f"FATAL: Timeline map file not found at {map_path}")
        sys.exit(1)

    try:
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FATAL: Timeline map file at {map_path} is invalid: {e}")
        sys.exit(1)

    # Load EDL content early for validation
    try:
        edl_content = json.loads(edl_path.read_text(encoding="utf-8"))
        sources = edl_content.get("sources", {})
    except Exception as e:
        print(f"FATAL: Failed to load sources from EDL: {e}")
        sys.exit(1)

    # Compute current source hashes
    current_edl_hash = compute_sha256(edl_path)
    current_wav_hash = compute_sha256(wav_path)
    current_map_hash = compute_sha256(map_path)

    # Validate timeline map contract
    if not isinstance(map_data, dict):
        print("FATAL: Timeline map is not a JSON object.")
        sys.exit(1)
    for key in ["edl_hash", "output_format", "ranges"]:
        if key not in map_data:
            print(f"FATAL: Timeline map missing key '{key}'.")
            sys.exit(1)

    if map_data["edl_hash"] != current_edl_hash:
        print(f"FATAL: Timeline map edl_hash ({map_data['edl_hash']}) does not match current EDL SHA-256 ({current_edl_hash}).")
        sys.exit(1)

    out_fmt = map_data["output_format"]
    if not isinstance(out_fmt, dict):
        print("FATAL: Timeline map output_format is not a JSON object.")
        sys.exit(1)
    for key in ["sample_rate", "channels", "sequence_fps"]:
        if key not in out_fmt:
            print(f"FATAL: Timeline map output_format missing key '{key}'.")
            sys.exit(1)

    if out_fmt["sample_rate"] != 48000:
        print(f"FATAL: Timeline map sample_rate is {out_fmt['sample_rate']}, expected 48000.")
        sys.exit(1)
    if out_fmt["channels"] != 2:
        print(f"FATAL: Timeline map channels is {out_fmt['channels']}, expected 2.")
        sys.exit(1)

    edl_fps_str = edl_content.get("metadata", {}).get("sequence_fps")
    if not edl_fps_str:
        print("FATAL: EDL metadata missing 'sequence_fps'.")
        sys.exit(1)
    try:
        edl_fps = parse_fps_fraction(edl_fps_str)
    except Exception as e:
        print(f"FATAL: Failed to parse EDL sequence_fps '{edl_fps_str}': {e}")
        sys.exit(1)

    map_fps_val = out_fmt["sequence_fps"]
    try:
        map_fps = parse_fps_fraction(map_fps_val)
    except Exception as e:
        print(f"FATAL: Failed to parse timeline map sequence_fps '{map_fps_val}': {e}")
        sys.exit(1)

    if edl_fps != map_fps:
        print(f"FATAL: Timeline map sequence_fps ({float(map_fps)}) does not match EDL authority FPS ({float(edl_fps)}).")
        sys.exit(1)

    edl_ranges = edl_content.get("ranges", [])
    map_ranges = map_data.get("ranges", [])
    if not isinstance(map_ranges, list):
        print("FATAL: Timeline map ranges is not a list.")
        sys.exit(1)
    if len(map_ranges) != len(edl_ranges):
        print(f"FATAL: Timeline map has {len(map_ranges)} ranges, but EDL has {len(edl_ranges)} ranges.")
        sys.exit(1)

    cum_start = 0
    for i, r in enumerate(edl_ranges):
        mr = map_ranges[i]
        if not isinstance(mr, dict):
            print(f"FATAL: Timeline map range at index {i} is not an object.")
            sys.exit(1)
        if mr.get("source") != r.get("source"):
            print(f"FATAL: Timeline map range {i} source '{mr.get('source')}' does not match EDL range source '{r.get('source')}'.")
            sys.exit(1)
        edl_constraints = r.get("boundary_constraints") or {}
        map_constraints = mr.get("boundary_constraints") or {}
        if (
            not isinstance(edl_constraints, dict)
            or not isinstance(map_constraints, dict)
            or edl_constraints != map_constraints
        ):
            print(
                f"FATAL: Timeline map range {i} boundary_constraints do not match the EDL."
            )
            sys.exit(1)

        sf = mr.get("source_frames")
        if not isinstance(sf, list) or len(sf) != 2:
            print(f"FATAL: Timeline map range {i} missing valid 'source_frames'.")
            sys.exit(1)

        in_f = r.get("source_in_frame")
        out_f = r.get("source_out_frame")
        if in_f is None or out_f is None:
            print(f"FATAL: EDL range {i} has missing source frames.")
            sys.exit(1)

        if in_f < 0 or out_f < 0:
            print(f"FATAL: EDL range {i} contains negative source frame bounds: in={in_f}, out={out_f}.")
            sys.exit(1)
        if sf[0] < 0 or sf[1] < 0:
            print(f"FATAL: Timeline map range {i} contains negative source frames: in={sf[0]}, out={sf[1]}.")
            sys.exit(1)

        if sf[0] != in_f or sf[1] != out_f:
            print(f"FATAL: Timeline map range {i} source_frames {sf} do not match EDL source frames [{in_f}, {out_f}].")
            sys.exit(1)

        cum_interval = mr.get("output_cumulative_sample_interval")
        if not isinstance(cum_interval, list) or len(cum_interval) != 2:
            print(f"FATAL: Timeline map range {i} missing valid 'output_cumulative_sample_interval'.")
            sys.exit(1)

        c_in, c_out = cum_interval[0], cum_interval[1]
        if c_in < 0 or c_out < 0:
            print(f"FATAL: Timeline map range {i} has negative cumulative sample interval: [{c_in}, {c_out}].")
            sys.exit(1)

        if c_in != cum_start:
            print(f"FATAL: Timeline map range {i} cumulative start {c_in} is not consecutive (expected {cum_start}).")
            sys.exit(1)

        start_sample = frame_to_sample(in_f, edl_fps)
        end_sample = frame_to_sample(out_f, edl_fps)
        expected_duration_samples = end_sample - start_sample

        actual_duration_samples = c_out - c_in
        if actual_duration_samples != expected_duration_samples:
            print(f"FATAL: Timeline map range {i} duration in samples {actual_duration_samples} does not match expected {expected_duration_samples}.")
            sys.exit(1)

        src_interval = mr.get("source_sample_interval")
        if not isinstance(src_interval, list) or len(src_interval) != 2:
            print(f"FATAL: Timeline map range {i} missing valid 'source_sample_interval'.")
            sys.exit(1)
        if src_interval[0] != start_sample or src_interval[1] != end_sample:
            print(f"FATAL: Timeline map range {i} source_sample_interval {src_interval} is not consistent with frame_to_sample conversion [{start_sample}, {end_sample}].")
            sys.exit(1)

        cum_start = c_out

    edl_mtime = get_mtime(edl_path)
    wav_mtime = get_mtime(wav_path)

    # 2. Check existence of all four reports
    reports = {
        "Boundary QC": boundary_path,
        "Audio QC": audio_path,
        "Semantic QC": semantic_path,
        "Preview Transcript QC": transcript_path,
    }

    missing_reports = []
    for name, r_path in reports.items():
        if not r_path.exists():
            missing_reports.append(name)
            print(f"FATAL: Report missing: {name} ({r_path.name})")

    if missing_reports:
        print("\nFATAL: One or more required QC reports are missing. Run the QC tools first.")
        sys.exit(1)

    # Load all reports
    try:
        boundary_data = json.loads(boundary_path.read_text(encoding="utf-8"))
        audio_data = json.loads(audio_path.read_text(encoding="utf-8"))
        semantic_data = json.loads(semantic_path.read_text(encoding="utf-8"))
        transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FATAL: Failed to parse one of the JSON reports: {e}")
        sys.exit(1)

    # 3. Freshness / Hash checks
    stale = False

    # A. Boundary QC (Refiner Report)
    boundary_errors = validate_boundary_report(boundary_data, edl_ranges)
    if boundary_errors:
        for error in boundary_errors:
            print(f"FATAL: Boundary QC schema/consistency error: {error}.")
        stale = True
    if boundary_data.get("output_edl_hash") != current_edl_hash:
        print("STALE: Boundary QC report output EDL hash mismatch. Re-run boundary refinement.")
        stale = True
    if get_mtime(boundary_path) < edl_mtime - 1.0:
        print("STALE: Boundary QC report is older than EDL. Re-run boundary refinement.")
        stale = True

    # B. Audio QC
    if audio_data.get("edl_hash") != current_edl_hash:
        print(f"STALE: Audio QC report EDL hash mismatch. Re-run audio QC.")
        stale = True
    if map_path.exists() and audio_data.get("timeline_map_hash") != current_map_hash:
        print(f"STALE: Audio QC report timeline map hash mismatch. Re-run audio QC.")
        stale = True
    if audio_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Audio QC report preview WAV hash mismatch. Re-run audio QC.")
        stale = True
    audio_errors = validate_audio_report(audio_data, map_ranges, edl_fps)
    if audio_errors:
        for error in audio_errors:
            print(f"FATAL: Audio QC schema/consistency error: {error}.")
        stale = True

    # C. Semantic QC
    if semantic_data.get("edl_hash") != current_edl_hash:
        print(f"STALE: Semantic QC report EDL hash mismatch. Re-run semantic QC.")
        stale = True
    # Check each transcript hash in semantic report
    stored_transcript_hashes = semantic_data.get("transcript_hashes", {})
    if not isinstance(stored_transcript_hashes, dict):
        print("FATAL: Semantic QC report transcript_hashes is invalid format.")
        sys.exit(1)

    # Validate ranges exist
    ranges = edl_content.get("ranges", [])
    if not isinstance(ranges, list) or len(ranges) == 0:
        print("FATAL: EDL has no ranges or ranges format is invalid.")
        sys.exit(1)

    # Validate integer boundaries, positive durations, and review_required exactly False on the EDL
    for idx, r in enumerate(ranges):
        in_f = r.get("source_in_frame")
        out_f = r.get("source_out_frame")

        if in_f is None or not isinstance(in_f, int) or isinstance(in_f, bool) or in_f < 0:
            print(f"FATAL: Range {idx} is missing a valid non-negative integer 'source_in_frame'.")
            sys.exit(1)
        if out_f is None or not isinstance(out_f, int) or isinstance(out_f, bool) or out_f < 0:
            print(f"FATAL: Range {idx} is missing a valid non-negative integer 'source_out_frame'.")
            sys.exit(1)
        if out_f <= in_f:
            print(f"FATAL: Range {idx} has non-positive duration (in={in_f}, out={out_f}).")
            sys.exit(1)
        if r.get("review_required") is not False:
            print(f"FATAL: Range {idx} does not have 'review_required' set to exactly False.")
            sys.exit(1)

    source_transcripts_for_qc: dict[str, dict[str, object]] = {}
    source_provider_bindings: set[tuple[object, object]] = set()
    for source_id in sources:
        t_path = transcripts_dir / f"{source_id}.json"
        try:
            source_transcript = json.loads(t_path.read_text(encoding="utf-8"))
            source_transcripts_for_qc[source_id] = source_transcript
            source_binding = source_transcript.get("_alano_cut")
            if isinstance(source_binding, dict):
                source_provider_bindings.add(
                    (
                        source_binding.get("transcription_provider"),
                        source_binding.get("config_sha256"),
                    )
                )
            selected_intervals = [
                (
                    frame_to_sample(int(item["source_in_frame"]), edl_fps) / 48000.0,
                    frame_to_sample(int(item["source_out_frame"]), edl_fps) / 48000.0,
                )
                for item in ranges
                if isinstance(item, dict) and item.get("source") == source_id
            ]
            scoped_blockers = validate_normative_transcript_for_intervals(
                source_transcript,
                selected_intervals,
            )
            if scoped_blockers:
                print(
                    f"AUDIT: Source transcript {source_id} retains "
                    f"{len(scoped_blockers)} acoustic blocker(s), all outside selected audio."
                )
        except (OSError, json.JSONDecodeError, TranscriptContractError) as error:
            print(
                f"FATAL: Source transcript {source_id} is not a canonical "
                f"provider-bound transcript: {error}"
            )
            stale = True
        current_t_hash = compute_sha256(t_path)
        stored_t_hash = stored_transcript_hashes.get(source_id)
        if current_t_hash != stored_t_hash:
            print(f"STALE: Semantic QC report source transcript hash mismatch for {source_id}. Re-run semantic QC.")
            stale = True

    # D. Preview Transcript QC
    if len(source_provider_bindings) != 1:
        print(
            "FATAL: Source transcripts do not share one provider/configuration; "
            "retranscribe the workspace with its selected provider."
        )
        stale = True
    if transcript_data.get("edl_hash") != current_edl_hash:
        print("STALE: Preview transcript QC report EDL hash mismatch. Re-run preview transcript QC.")
        stale = True
    if transcript_data.get("timeline_map_hash") != current_map_hash:
        print("STALE: Preview transcript QC report timeline map hash mismatch. Re-run preview transcript QC.")
        stale = True
    if transcript_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Preview transcript QC report WAV hash mismatch. Re-run preview transcript QC.")
        stale = True
    preview_source_hashes = transcript_data.get("source_transcript_hashes")
    if not isinstance(preview_source_hashes, dict):
        print("FATAL: Preview transcript QC source_transcript_hashes is invalid.")
        stale = True
    else:
        for source_id in sources:
            current_source_hash = compute_sha256(transcripts_dir / f"{source_id}.json")
            if preview_source_hashes.get(source_id) != current_source_hash:
                print(f"STALE: Preview transcript QC source transcript hash mismatch for {source_id}.")
                stale = True

    # Recalculate word coverage to verify declared counts
    t_file_val = transcript_data.get("transcript")
    recalc_word_count = 0
    recalc_timed_count = 0
    recalc_coverage = 0.0
    preview_transcript_for_qc: dict[str, object] | None = None

    if t_file_val and t_file_val != "generated":
        # Exija/carregue um transcript sidecar real
        t_file_path = Path(t_file_val).resolve()
        if not t_file_path.exists():
            print(f"FATAL: Preview transcript sidecar file not found: {t_file_path}")
            sys.exit(1)
        # Vincule hash quando houver sidecar
        sidecar_hash = compute_sha256(t_file_path)
        if transcript_data.get("transcript_hash") != sidecar_hash:
            print("STALE: Preview transcript QC report transcript file hash mismatch. Re-run preview transcript QC.")
            stale = True
        try:
            sidecar_data = json.loads(t_file_path.read_text(encoding="utf-8"))
            preview_transcript_for_qc = sidecar_data
            binding = sidecar_data.get("_alano_cut")
            if not isinstance(binding, dict) or binding.get("preview_wav_sha256") != current_wav_hash:
                print("STALE: Preview transcript sidecar is not bound to the current WAV.")
                stale = True
            try:
                validate_normative_transcript(sidecar_data)
            except TranscriptContractError as error:
                print(
                    "FATAL: Preview sidecar is not a canonical provider-bound "
                    f"transcript: {error}"
                )
                stale = True
            if isinstance(binding, dict) and source_provider_bindings:
                preview_binding = (
                    binding.get("transcription_provider"),
                    binding.get("config_sha256"),
                )
                if preview_binding not in source_provider_bindings:
                    print(
                        "STALE: Preview transcript provider/configuration does not "
                        "match the source transcripts. Re-run preview QC."
                    )
                    stale = True
            if not isinstance(binding, dict) or binding.get("source_sha256") != current_wav_hash:
                print("STALE: Preview transcript source hash does not match current WAV.")
                stale = True
            words_list = [w for w in sidecar_data.get("words", []) if w.get("type") == "word"]
            recalc_word_count = len(words_list)

            last_start = -1.0
            for w in words_list:
                start = w.get("start")
                end = w.get("end")
                if start is not None or end is not None:
                    if start is None or end is None:
                        print("FATAL: Timed word has start or end missing.")
                        sys.exit(1)
                    if not isinstance(start, (int, float)) or isinstance(start, bool):
                        print("FATAL: Timed word start is not a numeric type.")
                        sys.exit(1)
                    if not isinstance(end, (int, float)) or isinstance(end, bool):
                        print("FATAL: Timed word end is not a numeric type.")
                        sys.exit(1)
                    if not math.isfinite(start) or not math.isfinite(end):
                        print("FATAL: Timed word start or end is not finite.")
                        sys.exit(1)
                    if start < 0.0 or start >= end:
                        print(f"FATAL: Timed word bounds are invalid: start={start}, end={end}")
                        sys.exit(1)
                    if start < last_start:
                        print(f"FATAL: Timed word start={start} violates monotonic order (previous={last_start})")
                        sys.exit(1)

                    last_start = start
                    recalc_timed_count += 1
            recalc_coverage = (recalc_timed_count / recalc_word_count) if recalc_word_count > 0 else 0.0
        except Exception as e:
            print(f"FATAL: Failed to parse preview transcript sidecar JSON: {e}")
            sys.exit(1)
    else:
        print(
            "FATAL: Preview transcript QC must reference a persisted provider-bound "
            "sidecar; transcript='generated' is not independently verifiable."
        )
        sys.exit(1)

    transcript_errors = validate_transcript_report(
        transcript_data,
        edl_ranges,
        map_ranges,
        source_transcripts_for_qc,
        preview_transcript_for_qc,
        edl_fps,
    )
    if transcript_errors:
        for error in transcript_errors:
            print(f"FATAL: Preview transcript QC schema/consistency error: {error}.")
        stale = True

    # Verify that the declared word count and coverage match the recalculated ones
    t_summary = transcript_data.get("summary", {})
    decl_word_count = t_summary.get("word_count")
    decl_timed_count = t_summary.get("timed_word_count")
    decl_coverage = t_summary.get("timing_coverage")

    if (decl_word_count != recalc_word_count or
        decl_timed_count != recalc_timed_count or
        abs((decl_coverage or 0.0) - recalc_coverage) > 1e-4):
        print(f"FATAL: Declared transcript metrics (word_count={decl_word_count}, timed_word_count={decl_timed_count}, timing_coverage={decl_coverage}) "
              f"do not match recalculated metrics (word_count={recalc_word_count}, timed_word_count={recalc_timed_count}, timing_coverage={recalc_coverage:.4f}).")
        sys.exit(1)

    # Check if report is older than preview WAV (with 1s grace period)
    if get_mtime(transcript_path) < wav_mtime - 1.0:
        print("STALE: Preview transcript QC report is older than preview WAV. Re-run preview transcript QC.")
        stale = True

    # Reject missing or zero timed-word coverage
    if recalc_timed_count == 0 or recalc_coverage == 0.0:
        print("FATAL: Preview transcript QC report has missing or zero timed-word coverage.")
        sys.exit(1)

    if stale:
        print("\nFATAL: One or more QC reports are stale. Re-run the respective QC tools first.")
        sys.exit(1)

    # 4. Status mapping
    # We evaluate the statuses of all reports.
    # Statuses must belong to the known enum.
    if validate_boundary_report(boundary_data, edl_ranges):
        boundary_status = "fail"
    else:
        boundary_status = boundary_data.get("status")
        if boundary_status not in KNOWN_STATUSES:
            boundary_status = "fail"

    audio_status = audio_data.get("status")
    if audio_status not in KNOWN_STATUSES:
        audio_status = "fail"

    semantic_status = semantic_data.get("status")
    if semantic_status not in KNOWN_STATUSES:
        semantic_status = "fail"

    transcript_status = None
    if "summary" in transcript_data and isinstance(transcript_data["summary"], dict):
        transcript_status = transcript_data["summary"].get("status")
    if transcript_status is None:
        transcript_status = transcript_data.get("status")
    if transcript_status not in KNOWN_STATUSES:
        transcript_status = "fail"

    print(f"\nReport Statuses:")
    print(f"- Boundary QC:          {boundary_status.upper()}")
    print(f"- Audio QC:             {audio_status.upper()}")
    print(f"- Semantic QC:          {semantic_status.upper()}")
    print(f"- Preview Transcript QC: {transcript_status.upper()}")

    # Determine final output status and exit code
    all_statuses = [boundary_status, audio_status, semantic_status, transcript_status]

    if "fail" in all_statuses or "error" in all_statuses:
        print("\n=== GATE STATUS: FATAL ERROR ===")
        print("Edit has critical errors. Fix them before proceeding.")
        sys.exit(1)
    elif "review" in all_statuses or "warning" in all_statuses:
        print("\n=== GATE STATUS: REVIEW NEEDED ===")
        print("Edit has warnings or items that require manual review.")
        sys.exit(2)
    else:
        print("\n=== GATE STATUS: READY ===")
        print("All quality gates passed successfully. Ready for XML export.")
        sys.exit(0)


if __name__ == "__main__":
    main()
