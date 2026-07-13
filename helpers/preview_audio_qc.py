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
import sys
import wave
from pathlib import Path
import numpy as np

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.timing import frame_to_sample, parse_fps_fraction


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


def rms_db(samples: np.ndarray) -> float:
    """Compute the RMS value of a signal in dB FS (relative to 32768)."""
    if len(samples) == 0:
        return -100.0
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms == 0:
        return -100.0
    # Relative to PCM16 Full Scale (32768)
    return float(20.0 * np.log10(rms / 32768.0))


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

    # 5. Speech clipping (saturated samples) near boundaries
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

    # 6. Join discontinuities
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
    elif not two_frame_tail_ok or not speech_clipping_ok or severe_pops_count > 0:
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
        "joins": joins_analysis,
        "status": global_status
    }

    # Save the QC report
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(qc_report, indent=2), encoding="utf-8")
    print(f"QC Report written to {out_path} with status: {global_status}")


if __name__ == "__main__":
    main()
