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
import hashlib
import json
import os
import subprocess
from fractions import Fraction
from typing import Any

import numpy as np

from helpers.timing import parse_fps_fraction, time_to_frame, frame_to_time
from helpers.audio_analysis import (
    EXPECTED_MODEL_HASH,
    get_ffmpeg_version,
    is_ffmpeg_arnndn_available,
    verify_model_hash,
    check_vfr,
    get_source_fingerprint,
    extract_analysis_audio,
    get_combined_activity,
    DEFAULT_VAD_PARAMS,
    run_vad_hysteresis,
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


def calculate_sha256_of_string(content: str) -> str:
    """Return the SHA-256 hash of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_atomic(dest_path: Path, content: str) -> None:
    """Write content to a file atomically via temp file, flush, fsync, and os.replace."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # Replace replaces target on Windows if it exists
        os.replace(str(temp_path), str(dest_path))
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e


def get_refined_bound_on_signal(
    activity_sig: np.ndarray,
    w_start: float,
    w_end: float
) -> tuple[float, float, bool]:
    """Trace VAD activity forward and backward from overlapping word region."""
    w_start_f = int(round(w_start * 200))
    w_end_f = int(round(w_end * 200))

    if w_start_f > w_end_f:
        w_start_f, w_end_f = w_end_f, w_start_f

    w_start_f = max(0, min(len(activity_sig) - 1, w_start_f))
    w_end_f = max(0, min(len(activity_sig) - 1, w_end_f))

    subset = activity_sig[w_start_f : w_end_f + 1]
    active_offsets = np.where(subset)[0]

    if active_offsets.size == 0:
        return w_start, w_end, False

    first_active = w_start_f + active_offsets[0]

    # Trace backward to start of component
    i = first_active
    while i >= 0 and activity_sig[i]:
        i -= 1
    comp_start = (i + 1) * 0.005

    last_active = w_start_f + active_offsets[-1]

    # Trace forward to end of component
    i = last_active
    while i < len(activity_sig) and activity_sig[i]:
        i += 1
    comp_end = i * 0.005

    return comp_start, comp_end, True


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
        input_raw_content = edl_path.read_text(encoding="utf-8")
        input_hash = calculate_sha256_of_string(input_raw_content)
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
    ranges = edl.get("ranges", [])
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

        # Find refined start bound (from first word)
        t_onset_raw, _, has_start_raw = get_refined_bound_on_signal(activity_raw, float(first_word["start"]), float(first_word["end"]))
        # Find refined end bound (from last word)
        _, t_offset_raw, has_end_raw = get_refined_bound_on_signal(activity_raw, float(last_word["start"]), float(last_word["end"]))

        # RNNoise boundaries for agreement check
        t_onset_rnn, _, has_start_rnn = get_refined_bound_on_signal(activity_rnn, float(first_word["start"]), float(first_word["end"]))
        _, t_offset_rnn, has_end_rnn = get_refined_bound_on_signal(activity_rnn, float(last_word["start"]), float(last_word["end"]))

        # Apply Clamping and checks
        t_onset = t_onset_raw
        t_offset = t_offset_raw
        is_clamped_start = False
        is_clamped_end = False

        # Start boundary clamp (cannot cross prev word end)
        if prev_word is not None:
            prev_end = float(prev_word["end"])
            if t_onset < prev_end:
                t_onset = prev_end
                is_clamped_start = True

        # End boundary clamp (cannot cross next word start)
        if next_word is not None:
            next_start = float(next_word["start"])
            if t_offset > next_start:
                t_offset = next_start
                is_clamped_end = True

        # Exact in=floor onset fps, out=ceil offset fps+2
        F_in = time_to_frame(t_onset, fps, "floor")
        F_out_ideal = time_to_frame(t_offset, fps, "ceil") + 2
        F_out = F_out_ideal

        # Apply frame limit constraints
        if prev_word is not None:
            F_prev_limit = time_to_frame(float(prev_word["end"]), fps, "ceil")
            if F_in < F_prev_limit:
                F_in = F_prev_limit
                is_clamped_start = True

        if next_word is not None:
            F_next_limit = time_to_frame(float(next_word["start"]), fps, "floor")
            if F_out > F_next_limit:
                F_out = F_next_limit
                is_clamped_end = True

        # Verify tail padding for the out-point
        tail_frames = F_out - time_to_frame(t_offset, fps, "ceil")
        has_insufficient_tail = (tail_frames < 2)

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

        # Threshold sweep for agreement and stability
        sweep_deltas = [(-1.0, -0.5), (0.0, 0.0), (1.0, 0.5)]
        gap_frames = int(DEFAULT_VAD_PARAMS["gap_fill_ms"] / DEFAULT_VAD_PARAMS["hop_ms"])
        trans_frames = int(DEFAULT_VAD_PARAMS["transient_protection_ms"] / DEFAULT_VAD_PARAMS["hop_ms"])

        F_in_raw_vals = []
        F_in_rnn_vals = []
        F_out_raw_vals = []
        F_out_rnn_vals = []

        sweep_ok = True
        for delta_high, delta_low in sweep_deltas:
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

            t_onset_raw_s, _, has_start_raw_s = get_refined_bound_on_signal(act_raw_s, float(first_word["start"]), float(first_word["end"]))
            _, t_offset_raw_s, has_end_raw_s = get_refined_bound_on_signal(act_raw_s, float(last_word["start"]), float(last_word["end"]))
            t_onset_rnn_s, _, has_start_rnn_s = get_refined_bound_on_signal(act_rnn_s, float(first_word["start"]), float(first_word["end"]))
            _, t_offset_rnn_s, has_end_rnn_s = get_refined_bound_on_signal(act_rnn_s, float(last_word["start"]), float(last_word["end"]))

            if not (has_start_raw_s and has_start_rnn_s and has_end_raw_s and has_end_rnn_s):
                sweep_ok = False
                break

            F_in_raw_s = time_to_frame(t_onset_raw_s, fps, "floor")
            F_in_rnn_s = time_to_frame(t_onset_rnn_s, fps, "floor")
            F_out_raw_s = time_to_frame(t_offset_raw_s, fps, "ceil") + 2
            F_out_rnn_s = time_to_frame(t_offset_rnn_s, fps, "ceil") + 2

            F_in_raw_vals.append(F_in_raw_s)
            F_in_rnn_vals.append(F_in_rnn_s)
            F_out_raw_vals.append(F_out_raw_s)
            F_out_rnn_vals.append(F_out_rnn_s)

        if sweep_ok:
            spread_in = max(F_in_raw_vals + F_in_rnn_vals) - min(F_in_raw_vals + F_in_rnn_vals)
            spread_out = max(F_out_raw_vals + F_out_rnn_vals) - min(F_out_raw_vals + F_out_rnn_vals)
        else:
            spread_in = 9999
            spread_out = 9999

        # Confidence Classification
        start_conf = "low"
        if (
            snr_val is not None
            and corr_val is not None
            and snr_val >= 8.0
            and corr_val >= 0.8
            and has_start_raw
            and has_start_rnn
            and not is_clamped_start
            and sweep_ok
        ):
            if spread_in <= 1:
                start_conf = "high"
            elif spread_in <= 2:
                start_conf = "medium"

        end_conf = "low"
        if (
            snr_val is not None
            and corr_val is not None
            and snr_val >= 8.0
            and corr_val >= 0.8
            and has_end_raw
            and has_end_rnn
            and not is_clamped_end
            and not has_insufficient_tail
            and sweep_ok
        ):
            if spread_out <= 1:
                end_conf = "high"
            elif spread_out <= 2:
                end_conf = "medium"

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

        review_required = (start_conf == "low" or end_conf == "low")
        review_status = "approved" if not review_required else "low"
        if review_required:
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
            "review_status": review_status,
            "review_required": review_required
        })
        updated_ranges.append(r_new)

        # Record boundary evidence
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
            "vad_refined": {
                "onset_raw": t_onset_raw,
                "offset_raw": t_offset_raw,
                "onset_rnn": t_onset_rnn,
                "offset_rnn": t_offset_rnn
            },
            "clamped": {"start": is_clamped_start, "end": is_clamped_end},
            "tail_frames": tail_frames,
            "snr_db": snr_val,
            "correlation": corr_val,
            "confidence": {"start": start_conf, "end": end_conf},
            "spread": {"start": int(spread_in) if sweep_ok else None, "end": int(spread_out) if sweep_ok else None},
            "final_frames": {"in": final_in_frame, "out": final_out_frame},
            "final_times": {"start": final_start_s, "end": final_end_s}
        })

    # Prepare updated EDL dict
    new_total_duration = round(sum(r["end"] - r["start"] for r in updated_ranges), 6)
    edl_new = dict(edl)
    edl_new["ranges"] = updated_ranges
    edl_new["total_duration_s"] = new_total_duration
    if "metadata" not in edl_new:
        edl_new["metadata"] = {}
    edl_new["metadata"]["sequence_fps"] = float(fps)
    edl_new["metadata"]["refined_by"] = "alano-cut-snapper-f1.1"

    # Write Backup BEFORE modifying the input EDL file
    backups_dir = edit_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backups_dir / f"edl.{input_hash}.json"

    # Immutable create-exclusive dedup backup
    if not backup_path.exists():
        try:
            with open(backup_path, "x", encoding="utf-8") as f:
                f.write(input_raw_content)
        except FileExistsError:
            pass

    # Serialize output files
    edl_output_str = json.dumps(edl_new, indent=2, ensure_ascii=False)
    output_hash = calculate_sha256_of_string(edl_output_str)

    # Compile report
    report = {
        "status": "review" if has_low_confidence else "pass",
        "settings": DEFAULT_VAD_PARAMS,
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
        with open(temp_report, "w", encoding="utf-8") as f:
            f.write(report_output_str)
            f.flush()
            os.fsync(f.fileno())

        temp_edl.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_edl, "w", encoding="utf-8") as f:
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
