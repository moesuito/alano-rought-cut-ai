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
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.timing import frame_to_sample, parse_fps_fraction
from helpers.transcription_contract import (
    TranscriptContractError,
    validate_normative_transcript,
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


import math


KNOWN_STATUSES = {"pass", "warning", "review", "fail", "error"}
STATUS_SEVERITY = {"pass": 0, "warning": 1, "review": 2, "fail": 3, "error": 3}
BOUNDARY_CONFIDENCES = {"high", "medium", "low"}


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
        if audio_data.get("range_review_count") != observed_range_reviews:
            errors.append("range_review_count does not match range entry evidence")

    total_samples = audio_data.get("total_samples")
    expected_samples = audio_data.get("expected_samples")
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

    tail_window = audio_data.get("two_frame_tail_window")
    tail_required = audio_data.get("two_frame_tail_required_samples")
    tail_window_valid = (
        isinstance(tail_window, list)
        and len(tail_window) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in tail_window)
        and tail_window[0] >= 0
        and tail_window[1] > tail_window[0]
        and isinstance(tail_required, int)
        and not isinstance(tail_required, bool)
        and tail_required > 0
        and tail_window[1] - tail_window[0] == tail_required
        and isinstance(total_samples, int)
        and not isinstance(total_samples, bool)
        and isinstance(expected_samples, int)
        and not isinstance(expected_samples, bool)
        and tail_window[1] == expected_samples
        and tail_window[1] <= total_samples
    )
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


def validate_transcript_report(
    transcript_data: object,
    edl_ranges: list[dict[str, object]],
    map_ranges: list[dict[str, object]],
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
    summary = transcript_data.get("summary")
    timing = transcript_data.get("timing_validation")
    if not isinstance(ranges, list):
        errors.append("ranges is missing or not a list")
        ranges = []
    if not isinstance(joins, list):
        errors.append("joins is missing or not a list")
        joins = []
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
    evidence_flags: list[str] = []
    for position, result in enumerate(ranges):
        if not isinstance(result, dict):
            errors.append(f"ranges[{position}] is not an object")
            continue
        flags = result.get("blocking_flags")
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            errors.append(f"ranges[{position}] blocking_flags is invalid")
            flags = []
        expected_status = "review" if flags else "pass"
        if result.get("status") != expected_status:
            errors.append(f"ranges[{position}] status contradicts blocking_flags")
        if expected_status == "review":
            range_review_count += 1
        evidence_flags.extend(flags)
        if position < len(edl_ranges):
            if (
                result.get("range_index") != position
                or result.get("source") != edl_ranges[position].get("source")
                or result.get("source") != map_ranges[position].get("source")
            ):
                errors.append(f"ranges[{position}] identity does not match EDL/timeline map")
        for key in ("expected_words", "actual_words", "alignment"):
            if not isinstance(result.get(key), list):
                errors.append(f"ranges[{position}] {key} is invalid")

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

    if abs(float(edl_fps) - float(map_fps)) > 1e-6:
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
    audio_errors = validate_audio_report(audio_data, map_ranges)
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

    for source_id in sources:
        t_path = transcripts_dir / f"{source_id}.json"
        try:
            source_transcript = json.loads(t_path.read_text(encoding="utf-8"))
            validate_normative_transcript(source_transcript)
        except (OSError, json.JSONDecodeError, TranscriptContractError) as error:
            print(
                f"FATAL: Source transcript {source_id} is not a canonical, "
                f"forced-aligned, diarized WhisperX transcript: {error}"
            )
            stale = True
        current_t_hash = compute_sha256(t_path)
        stored_t_hash = stored_transcript_hashes.get(source_id)
        if current_t_hash != stored_t_hash:
            print(f"STALE: Semantic QC report source transcript hash mismatch for {source_id}. Re-run semantic QC.")
            stale = True

    # D. Preview Transcript QC
    if transcript_data.get("edl_hash") != current_edl_hash:
        print("STALE: Preview transcript QC report EDL hash mismatch. Re-run preview transcript QC.")
        stale = True
    if transcript_data.get("timeline_map_hash") != current_map_hash:
        print("STALE: Preview transcript QC report timeline map hash mismatch. Re-run preview transcript QC.")
        stale = True
    if transcript_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Preview transcript QC report WAV hash mismatch. Re-run preview transcript QC.")
        stale = True
    transcript_errors = validate_transcript_report(transcript_data, edl_ranges, map_ranges)
    if transcript_errors:
        for error in transcript_errors:
            print(f"FATAL: Preview transcript QC schema/consistency error: {error}.")
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
            binding = sidecar_data.get("_alano_cut")
            if not isinstance(binding, dict) or binding.get("preview_wav_sha256") != current_wav_hash:
                print("STALE: Preview transcript sidecar is not bound to the current WAV.")
                stale = True
            try:
                validate_normative_transcript(sidecar_data)
            except TranscriptContractError as error:
                print(
                    "FATAL: Preview sidecar is not a canonical, forced-aligned, "
                    f"diarized WhisperX transcript: {error}"
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
        # Relatório com transcript=generated sem evidência verificável deve falhar
        if "words_evidence" not in transcript_data or not isinstance(transcript_data["words_evidence"], list):
            print("FATAL: Preview transcript QC report is 'generated' but missing verifiable words_evidence.")
            sys.exit(1)
        # Recalcule cobertura
        words_evidence = transcript_data["words_evidence"]
        words_list = [w for w in words_evidence if w.get("type") == "word"]
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
