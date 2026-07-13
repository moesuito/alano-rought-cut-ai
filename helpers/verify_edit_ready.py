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
from typing import Any


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
from fractions import Fraction

def parse_fps_fraction(fps_val: Any) -> Fraction:
    """Parse FPS value into a Fraction. Supports int, float, Fraction, and string format."""
    if isinstance(fps_val, Fraction):
        return fps_val
    if isinstance(fps_val, (int, float)):
        return Fraction(fps_val)
    fps_str = str(fps_val).strip()
    if "/" in fps_str:
        num, den = map(int, fps_str.split("/"))
        return Fraction(num, den)
    return Fraction(float(fps_str))

def frame_to_sample(frame: int, fps: Fraction, sample_rate: int = 48000) -> int:
    """Convert frame number to audio sample index with precise rational timing."""
    return int(frame * sample_rate / fps)


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify if edit is ready for XML export")
    ap.add_argument("edl", type=Path, nargs="?", default=Path("edit/edl.json"), help="Path to edl.json")
    ap.add_argument("--transcripts", type=Path, default=Path("edit/transcripts"), help="Path to transcripts directory")
    ap.add_argument("--boundary-report", type=Path, default=Path("edit/edl_boundary_qc.json"), help="Path to edl_boundary_qc.json")
    ap.add_argument("--audio-report", type=Path, default=Path("edit/preview_audio_qc.json"), help="Path to preview_audio_qc.json")
    ap.add_argument("--semantic-report", type=Path, default=Path("edit/edl_semantic_qc.json"), help="Path to edl_semantic_qc.json")
    ap.add_argument("--transcript-report", type=Path, default=Path("edit/preview_transcript_qc.json"), help="Path to preview_transcript_qc.json")
    ap.add_argument("--audio", type=Path, default=Path("edit/preview.wav"), help="Path to preview.wav")
    ap.add_argument("--timeline-map", type=Path, default=Path("edit/preview_timeline.json"), help="Path to preview_timeline.json")

    args = ap.parse_args()

    edl_path = args.edl.resolve()
    transcripts_dir = args.transcripts.resolve()
    boundary_path = args.boundary_report.resolve()
    audio_path = args.audio_report.resolve()
    semantic_path = args.semantic_report.resolve()
    transcript_path = args.transcript_report.resolve()
    wav_path = args.audio.resolve()
    map_path = args.timeline_map.resolve()

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
    if "boundary_evidence" not in boundary_data or "confidence_summary" not in boundary_data:
        print("FATAL: Boundary QC report is not in the refiner report schema format.")
        stale = True
    else:
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
        current_t_hash = compute_sha256(t_path)
        stored_t_hash = stored_transcript_hashes.get(source_id)
        if current_t_hash != stored_t_hash:
            print(f"STALE: Semantic QC report source transcript hash mismatch for {source_id}. Re-run semantic QC.")
            stale = True

    # D. Preview Transcript QC
    if transcript_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Preview transcript QC report WAV hash mismatch. Re-run preview transcript QC.")
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
    # Statuses must belong to KNOWN_STATUSES enum known statuses
    KNOWN_STATUSES = {"pass", "warning", "review", "fail", "error"}

    if "boundary_evidence" not in boundary_data or "confidence_summary" not in boundary_data:
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
