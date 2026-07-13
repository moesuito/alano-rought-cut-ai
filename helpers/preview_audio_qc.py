"""Verify audio quality of the preview WAV.

Checks:
- WAV format correctness (PCM16, 48kHz, stereo)
- Parity between timeline map cumulative samples and WAV frames
- Leading silence duration
- Two-frame tail silence
- Speech clipping (saturated samples) near boundaries
- Join discontinuities (pops / severe pops / warnings)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import wave
from pathlib import Path
import numpy as np

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.timing import frame_to_sample, parse_fps_fraction
from helpers.audio_analysis import compute_rms_db


def two_frame_sample_count(
    fps_value: object,
    sample_rate: int = 48000,
    *,
    end_frame: int = 2,
) -> int:
    """Return the exact PCM span of two frames ending at ``end_frame``."""
    if not isinstance(end_frame, int) or isinstance(end_frame, bool) or end_frame < 2:
        raise ValueError("end_frame must be an integer greater than or equal to 2")
    fps = parse_fps_fraction(fps_value)
    return (
        frame_to_sample(end_frame, fps, sample_rate)
        - frame_to_sample(end_frame - 2, fps, sample_rate)
    )


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def rms_db(samples: np.ndarray) -> float:
    """Compute the RMS value of a signal in dB FS (relative to 32768)."""
    if len(samples) == 0:
        return -100.0
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms == 0:
        return -100.0
    # Relative to PCM16 Full Scale (32768)
    return float(20.0 * np.log10(rms / 32768.0))


def activity_profile(samples: np.ndarray, sample_rate: int = 48000) -> dict:
    """Return phase-safe adaptive activity evidence for a multichannel span."""
    if samples.ndim == 1:
        samples = samples.reshape((-1, 1))
    window_samples = int(sample_rate * 0.010)
    hop_samples = int(sample_rate * 0.005)
    if len(samples) < window_samples or samples.shape[1] == 0:
        return {
            "threshold_dbfs": None,
            "noise_floor_dbfs": None,
            "first_activity_ms": None,
            "activity_ms": 0.0,
            "activity": np.zeros(0, dtype=bool),
            "max_rms_dbfs": np.zeros(0, dtype=np.float64),
            "hop_ms": 5.0,
        }

    rms_channels = np.vstack([
        compute_rms_db(samples[:, channel], window_samples, hop_samples)
        for channel in range(samples.shape[1])
    ])
    max_rms = np.max(rms_channels, axis=0)
    noise_floor = float(np.percentile(max_rms, 10))
    threshold = float(np.clip(noise_floor + 8.0, -65.0, -30.0))
    exit_threshold = threshold - 3.0

    activity = np.zeros(len(max_rms), dtype=bool)
    active = False
    for index, value in enumerate(max_rms):
        if active:
            active = value >= exit_threshold
        else:
            active = value >= threshold
        activity[index] = active

    # Fill short internal holes (<= 40 ms).
    max_gap_bins = 8
    index = 0
    while index < len(activity):
        if activity[index]:
            index += 1
            continue
        gap_start = index
        while index < len(activity) and not activity[index]:
            index += 1
        if gap_start > 0 and index < len(activity) and index - gap_start <= max_gap_bins:
            activity[gap_start:index] = True

    # Ignore isolated noise: entry activity must remain connected for 15 ms.
    minimum_run_bins = 3
    first_activity_bin = None
    index = 0
    while index < len(activity):
        if not activity[index]:
            index += 1
            continue
        run_start = index
        while index < len(activity) and activity[index]:
            index += 1
        if index - run_start >= minimum_run_bins:
            if first_activity_bin is None:
                first_activity_bin = run_start
        else:
            activity[run_start:index] = False

    return {
        "threshold_dbfs": threshold,
        "noise_floor_dbfs": noise_floor,
        "first_activity_ms": (
            float(first_activity_bin * 5.0) if first_activity_bin is not None else None
        ),
        "activity_ms": float(np.count_nonzero(activity) * 5.0),
        "activity": activity,
        "max_rms_dbfs": max_rms,
        "hop_ms": 5.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Preview Audio QC Tool")
    ap.add_argument("wav", type=Path, nargs="?", default=Path("edit/preview.wav"), help="Path to preview.wav")
    ap.add_argument("-m", "--timeline-map", type=Path, default=Path("edit/preview_timeline.json"), help="Path to preview_timeline.json")
    ap.add_argument("-o", "--output", type=Path, default=Path("edit/preview_audio_qc.json"), help="Path to output preview_audio_qc.json")
    ap.add_argument("-e", "--edl", type=Path, default=Path("edit/edl.json"), help="Path to edl.json")

    args = ap.parse_args()

    wav_path = args.wav.resolve()
    map_path = args.timeline_map.resolve()
    out_path = args.output.resolve()
    edl_path = args.edl.resolve()

    if not wav_path.exists():
        print(f"Error: WAV file not found at {wav_path}")
        import sys
        sys.exit(1)

    if not map_path.exists():
        print(f"Error: Timeline map file not found at {map_path}")
        import sys
        sys.exit(1)

    # Read the timeline map
    try:
        timeline_map = json.loads(map_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error: Failed to parse timeline map JSON: {e}")
        import sys
        sys.exit(1)

    # 1. Verify WAV Format
    wav_format_ok = True
    try:
        with wave.open(str(wav_path), "rb") as w:
            nchannels = w.getnchannels()
            sampwidth = w.getsampwidth()
            framerate = w.getframerate()
            nframes = w.getnframes()

            # Read all frames
            raw_frames = w.readframes(nframes)
            samples = np.frombuffer(raw_frames, dtype=np.int16)
            if nchannels > 0:
                samples = samples.reshape((-1, nchannels))
            else:
                samples = np.array([])
    except Exception as e:
        print(f"Error: Failed to read WAV file: {e}")
        import sys
        sys.exit(1)

    if nchannels != 2 or sampwidth != 2 or framerate != 48000:
        wav_format_ok = False

    # Preserve channels for every detector. Averaging can cancel anti-phase
    # speech, clipping, or discontinuities into apparent silence.
    if samples.ndim == 2:
        channel_samples = samples.astype(np.float64, copy=False)
    else:
        channel_samples = np.asarray(samples, dtype=np.float64).reshape((-1, 1))

    # 2. Parity between timeline map and WAV frames
    ranges = timeline_map.get("ranges", [])
    expected_samples = ranges[-1]["output_cumulative_sample_interval"][1] if ranges else 0
    actual_samples = int(channel_samples.shape[0])

    discrepancy = abs(actual_samples - expected_samples)
    # Allowed discrepancy: within 1 sample per join
    num_joins = len(ranges) - 1 if len(ranges) > 0 else 0
    # Parity is ok if discrepancy <= max(1, num_joins)
    sample_count_parity_ok = discrepancy <= max(1, num_joins)

    # 3. Leading silence duration
    # Threshold for -60 dB FS: 32768 * 10^(-60/20) = 32.768
    silence_threshold = 32768.0 * (10.0 ** (-60.0 / 20.0))
    frame_peak = np.max(np.abs(channel_samples), axis=1) if actual_samples else np.array([])
    first_active_indices = np.where(frame_peak >= silence_threshold)[0]
    if len(first_active_indices) > 0:
        leading_silence_seconds = float(first_active_indices[0] / 48000.0)
    else:
        leading_silence_seconds = float(actual_samples / 48000.0)

    # 4. Two-frame tail silence
    # Extract sequence FPS
    fps_val = timeline_map.get("output_format", {}).get("sequence_fps")
    if not fps_val:
        # Fallback to EDL
        if edl_path.exists():
            try:
                edl_data = json.loads(edl_path.read_text(encoding="utf-8"))
                fps_val = edl_data.get("metadata", {}).get("sequence_fps", 30.0)
            except Exception:
                fps_val = 30.0
        else:
            fps_val = 30.0
    fps = parse_fps_fraction(fps_val)

    # Keep the generic two-frame size for the existing clipping-neighborhood
    # check. Tail coverage itself is phase-aware at the final source endpoint.
    boundary_window_size = two_frame_sample_count(fps_val)
    tail_size = 0
    tail_window_start = None
    tail_window_end = None
    tail_coverage_ok = False
    tail_rms_by_channel = [-100.0] * channel_samples.shape[1]

    if ranges:
        last_range = ranges[-1]
        source_frames = last_range.get("source_frames")
        output_interval = last_range.get("output_cumulative_sample_interval")
        if (
            isinstance(source_frames, list)
            and len(source_frames) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in source_frames)
            and isinstance(output_interval, list)
            and len(output_interval) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in output_interval)
        ):
            source_in_frame, source_out_frame = source_frames
            output_start, output_end = output_interval
            if source_out_frame - source_in_frame >= 2:
                tail_size = two_frame_sample_count(
                    fps_val,
                    end_frame=source_out_frame,
                )
                tail_window_start = output_end - tail_size
                tail_window_end = output_end
                tail_coverage_ok = (
                    tail_size > 0
                    and output_start <= tail_window_start < tail_window_end
                    and tail_window_end <= actual_samples
                )

    if tail_coverage_ok:
        tail_samples = channel_samples[tail_window_start:tail_window_end]
        tail_rms_by_channel = [
            rms_db(tail_samples[:, channel_index])
            for channel_index in range(channel_samples.shape[1])
        ]

    # A tail passes only when the complete two-frame interval is available and
    # every preserved output channel is below the silence threshold.
    tail_rms = max(tail_rms_by_channel, default=-100.0)
    two_frame_tail_ok = tail_coverage_ok and tail_rms < -60.0

    # 5. Per-range entry/tail evidence. This makes every join auditable from
    # both sides instead of checking only the beginning/end of the whole WAV.
    range_entries = []
    range_reviews_count = 0
    frame_ms = 1000.0 / float(fps)
    allowed_acoustic_preroll_ms = frame_ms + 25.0
    allowed_entry_inactivity_ms = 150.0

    for position, range_map in enumerate(ranges):
        flags = []
        warnings = []
        output_interval = range_map.get("output_cumulative_sample_interval")
        source_frames = range_map.get("source_frames")
        source_interval = range_map.get("source_sample_interval")
        valid_intervals = (
            isinstance(output_interval, list)
            and len(output_interval) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in output_interval)
            and isinstance(source_frames, list)
            and len(source_frames) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in source_frames)
        )
        if not valid_intervals:
            range_entries.append({
                "range_index": range_map.get("range_index", position),
                "source": range_map.get("source"),
                "status": "review",
                "blocking_flags": ["invalid_range_map"],
            })
            range_reviews_count += 1
            continue

        output_start, output_end = output_interval
        source_in_frame, source_out_frame = source_frames
        span_covered = 0 <= output_start < output_end <= actual_samples
        if not span_covered:
            flags.append("range_audio_not_fully_covered")
        segment = channel_samples[
            max(0, min(actual_samples, output_start)):
            max(0, min(actual_samples, output_end))
        ]
        profile = activity_profile(segment, framerate)
        first_activity_ms = profile["first_activity_ms"]
        if first_activity_ms is None:
            flags.append("missing_connected_activity")
        entry_activity_is_late = (
            first_activity_ms is not None
            and first_activity_ms > allowed_entry_inactivity_ms
        )

        if (
            not isinstance(source_interval, list)
            or len(source_interval) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in source_interval)
        ):
            source_interval = [
                frame_to_sample(source_in_frame, fps),
                frame_to_sample(source_out_frame, fps),
            ]

        lexical_anchors = range_map.get("lexical_anchors")
        first_anchor = lexical_anchors.get("first") if isinstance(lexical_anchors, dict) else None
        acoustic_onset = None
        if isinstance(first_anchor, dict):
            acoustic_onset = first_anchor.get("acoustic_onset", first_anchor.get("start"))
        if not isinstance(acoustic_onset, (int, float)) or isinstance(acoustic_onset, bool):
            expected_guard_ms = None
            expected_onset_output_sample = None
            residual_pre_anchor_activity_ms = None
            flags.append("missing_lexical_anchor")
        else:
            expected_onset_source_sample = int(round(float(acoustic_onset) * framerate))
            expected_onset_offset = expected_onset_source_sample - source_interval[0]
            expected_onset_output_sample = output_start + expected_onset_offset
            expected_guard_ms = max(0.0, expected_onset_offset * 1000.0 / framerate)
            if expected_guard_ms > allowed_acoustic_preroll_ms:
                flags.append("excessive_acoustic_preroll")

            # Activity more than one source frame before the selected acoustic
            # onset indicates a rejected cue/word leaking into the range.
            residual_cutoff_ms = max(0.0, expected_guard_ms - frame_ms)
            residual_bins = int(residual_cutoff_ms / profile["hop_ms"])
            residual_pre_anchor_activity_ms = float(
                np.count_nonzero(profile["activity"][:residual_bins]) * profile["hop_ms"]
            )
            if residual_pre_anchor_activity_ms > 15.0:
                flags.append("residual_pre_anchor_activity")

        if entry_activity_is_late:
            if (
                expected_guard_ms is not None
                and expected_guard_ms <= allowed_acoustic_preroll_ms
                and residual_pre_anchor_activity_ms is not None
                and residual_pre_anchor_activity_ms <= 15.0
            ):
                # A quiet first word may sit below this lightweight amplitude
                # detector. The mandatory timed preview transcript remains the
                # lexical gate, so record the ambiguity without rejecting an
                # otherwise tight and cue-free mapped entry.
                warnings.append("quiet_entry_requires_transcript_confirmation")
            else:
                flags.append("excessive_entry_inactivity")

        peak_dbfs_by_channel = []
        rms_dbfs_by_channel = []
        for channel in range(channel_samples.shape[1]):
            channel_segment = segment[:, channel] if len(segment) else np.array([])
            rms_dbfs_by_channel.append(rms_db(channel_segment))
            peak = float(np.max(np.abs(channel_segment))) if len(channel_segment) else 0.0
            peak_dbfs_by_channel.append(
                float(20.0 * np.log10(peak / 32768.0)) if peak > 0 else -100.0
            )

        range_tail_size = (
            two_frame_sample_count(fps, end_frame=source_out_frame)
            if source_out_frame >= 2
            else 0
        )
        range_tail_coverage_ok = range_tail_size > 0 and len(segment) >= range_tail_size
        if range_tail_coverage_ok:
            range_tail = segment[-range_tail_size:]
            range_tail_rms_by_channel = [
                rms_db(range_tail[:, channel]) for channel in range(range_tail.shape[1])
            ]
        else:
            range_tail_rms_by_channel = [-100.0] * channel_samples.shape[1]
        range_tail_rms = max(range_tail_rms_by_channel, default=-100.0)
        activity_threshold = profile.get("threshold_dbfs")
        adaptive_tail_threshold = -60.0
        if isinstance(activity_threshold, (int, float)):
            adaptive_tail_threshold = max(-60.0, min(-50.0, float(activity_threshold)))
        left_tail_ok = (
            range_tail_coverage_ok
            and range_tail_rms < adaptive_tail_threshold
        )
        if not left_tail_ok:
            flags.append("active_or_uncovered_two_frame_tail")

        boundary_window = min(boundary_window_size, len(segment))
        boundary_clipping = False
        if boundary_window > 0:
            boundary_samples = np.concatenate(
                [segment[:boundary_window], segment[-boundary_window:]], axis=0
            )
            boundary_clipping = bool(np.any(np.abs(boundary_samples) >= 32760))
        if boundary_clipping:
            flags.append("range_boundary_clipping")

        range_status = "review" if flags else "pass"
        if flags:
            range_reviews_count += 1
        range_entries.append({
            "range_index": range_map.get("range_index", position),
            "source": range_map.get("source"),
            "beat_id": range_map.get("beat_id"),
            "output_sample_interval": output_interval,
            "first_activity_ms": first_activity_ms,
            "allowed_entry_inactivity_ms": allowed_entry_inactivity_ms,
            "activity_threshold_dbfs": profile["threshold_dbfs"],
            "activity_noise_floor_dbfs": profile["noise_floor_dbfs"],
            "activity_ms": profile["activity_ms"],
            "expected_acoustic_onset_output_sample": expected_onset_output_sample,
            "expected_acoustic_preroll_ms": expected_guard_ms,
            "allowed_acoustic_preroll_ms": allowed_acoustic_preroll_ms,
            "residual_pre_anchor_activity_ms": residual_pre_anchor_activity_ms,
            "peak_dbfs_by_channel": peak_dbfs_by_channel,
            "rms_dbfs_by_channel": rms_dbfs_by_channel,
            "two_frame_tail_required_samples": range_tail_size,
            "two_frame_tail_coverage_ok": range_tail_coverage_ok,
            "two_frame_tail_rms_db": range_tail_rms,
            "two_frame_tail_rms_db_by_channel": range_tail_rms_by_channel,
            "two_frame_tail_threshold_dbfs": adaptive_tail_threshold,
            "left_tail_ok": left_tail_ok,
            "boundary_clipping_detected": boundary_clipping,
            "status": range_status,
            "blocking_flags": flags,
            "warnings": warnings,
        })

    # 6. Speech clipping (saturated samples) near boundaries
    # Saturated sample is where abs(val) >= 32760
    saturated_indices = np.where(frame_peak >= 32760)[0]
    clipping_events_count = len(saturated_indices)

    # Check if there is clipping near boundaries (within 2 frames of any join)
    boundary_clipping_detected = False
    boundary_clipping_indices = []

    join_points = []
    for r in ranges[:-1]:
        join_points.append(r["output_cumulative_sample_interval"][1])

    for idx in saturated_indices:
        for jp in join_points:
            if abs(idx - jp) <= boundary_window_size:
                boundary_clipping_detected = True
                boundary_clipping_indices.append(int(idx))
                break

    speech_clipping_ok = not boundary_clipping_detected

    # 7. Join discontinuities
    severe_pops_count = 0
    warning_pops_count = 0
    joins_analysis = []

    # Window size: 100ms -> 4800 samples
    W = 4800

    for i in range(len(ranges) - 1):
        r_left = ranges[i]
        r_right = ranges[i + 1]

        S_left, E_left = r_left["output_cumulative_sample_interval"]
        S_right, E_right = r_right["output_cumulative_sample_interval"]

        # The join is at sample E_left
        J = E_left

        if J <= 0 or J >= actual_samples:
            continue

        # Local window excluding the join transition
        left_start = max(S_left, J - W)
        right_end = min(E_right, J + W)

        channel_analyses = []
        for channel_index in range(channel_samples.shape[1]):
            signal = channel_samples[:, channel_index]
            delta = float(abs(signal[J] - signal[J - 1]))

            left_deltas = (
                np.abs(np.diff(signal[left_start:J]))
                if J > left_start
                else np.array([])
            )
            right_deltas = (
                np.abs(np.diff(signal[J:right_end]))
                if right_end > J
                else np.array([])
            )
            local_deltas = np.concatenate([left_deltas, right_deltas])
            local_p95_delta = (
                float(np.percentile(local_deltas, 95))
                if len(local_deltas) > 0
                else 0.0
            )

            severe_threshold = max(0.10 * 32768.0, 8.0 * local_p95_delta)
            warning_threshold = max(0.05 * 32768.0, 4.0 * local_p95_delta)

            if delta >= severe_threshold:
                channel_status = "severe"
            elif delta >= warning_threshold:
                channel_status = "warning"
            else:
                channel_status = "pass"

            channel_analyses.append({
                "channel_index": channel_index,
                "delta": delta,
                "local_p95_delta": local_p95_delta,
                "warning_threshold": warning_threshold,
                "severe_threshold": severe_threshold,
                "status": channel_status,
            })

        status_rank = {"pass": 0, "warning": 1, "severe": 2}
        worst_channel = max(
            channel_analyses,
            key=lambda item: (status_rank[item["status"]], item["delta"]),
        )
        status = worst_channel["status"]
        if status == "severe":
            severe_pops_count += 1
        elif status == "warning":
            warning_pops_count += 1

        joins_analysis.append({
            "join_index": i,
            "sample_index": J,
            "left_source": r_left["source"],
            "right_source": r_right["source"],
            "worst_channel_index": worst_channel["channel_index"],
            "delta": worst_channel["delta"],
            "local_p95_delta": worst_channel["local_p95_delta"],
            "warning_threshold": worst_channel["warning_threshold"],
            "severe_threshold": worst_channel["severe_threshold"],
            "status": status,
            "channels": channel_analyses,
        })

    global_status = "pass"
    if not wav_format_ok or not sample_count_parity_ok or not tail_coverage_ok:
        global_status = "fail"
    elif (
        not two_frame_tail_ok
        or not speech_clipping_ok
        or severe_pops_count > 0
        or range_reviews_count > 0
    ):
        global_status = "review"
    elif warning_pops_count > 0:
        global_status = "warning"

    qc_report = {
        "edl_hash": compute_sha256(edl_path),
        "timeline_map_hash": compute_sha256(map_path),
        "preview_wav_hash": compute_sha256(wav_path),
        "wav_format_ok": wav_format_ok,
        "sample_rate": framerate,
        "channels": nchannels,
        "sample_width": sampwidth * 8,
        "total_samples": actual_samples,
        "expected_samples": expected_samples,
        "sample_count_parity_ok": sample_count_parity_ok,
        "leading_silence_seconds": leading_silence_seconds,
        "two_frame_tail_ok": two_frame_tail_ok,
        "two_frame_tail_coverage_ok": tail_coverage_ok,
        "two_frame_tail_required_samples": tail_size,
        "two_frame_tail_window": [tail_window_start, tail_window_end],
        "tail_rms_db": tail_rms,
        "tail_rms_db_by_channel": tail_rms_by_channel,
        "speech_clipping_ok": speech_clipping_ok,
        "clipping_events_count": clipping_events_count,
        "boundary_clipping_detected": boundary_clipping_detected,
        "severe_pops_count": severe_pops_count,
        "warning_pops_count": warning_pops_count,
        "range_review_count": range_reviews_count,
        "range_entries": range_entries,
        "joins": joins_analysis,
        "status": global_status
    }

    # Save the QC report
    write_atomic_json(out_path, qc_report)
    print(f"QC Report written to {out_path} with status: {global_status}")


if __name__ == "__main__":
    main()
