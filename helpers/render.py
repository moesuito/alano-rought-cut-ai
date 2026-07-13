"""Render a WAV preview from an EDL.

Implements the dry, deterministic audio-only preview pipeline:
  1. Per-segment extract as PCM16 48kHz stereo WAV from EDL boundaries.
  2. Lossless concatenation into final WAV.
  3. Generation of a timeline map JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

# Support running directly as a script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.timing import frame_to_sample, parse_fps_fraction, time_to_frame


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def probe_channels(source_path: Path) -> int:
    """Probe the number of audio channels in a source file."""
    cmd_channels = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=channels",
        "-of", "json", str(source_path)
    ]
    try:
        out_c = subprocess.check_output(cmd_channels, text=True)
        data_c = json.loads(out_c)
        streams = data_c.get("streams", [])
        if not streams:
            return 1
        return int(streams[0].get("channels", 1))
    except Exception:
        return 1


def extract_audio_segment(
    source_path: Path,
    start_sample: int,
    end_sample: int,
    out_path: Path,
) -> None:
    """Extract a sample range as stereo PCM16 48kHz WAV using ffmpeg filter graphs.

    Resamples input to 48000 Hz and channel layout to stereo first,
    then trims to exact start and end samples, resetting PTS.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # We use aformat to enforce 48000 Hz, stereo, and s16 format before trimming.
    filter_str = (
        f"aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo,"
        f"atrim=start_sample={start_sample}:end_sample={end_sample},"
        f"asetpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(source_path),
        "-vn",
        "-af", filter_str,
        "-c:a", "pcm_s16le",
        "-rf64", "auto",
        str(out_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def write_atomic(dest_path: Path, content: str) -> None:
    """Write string content atomically to a file using a temp file."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(temp_path), str(dest_path))
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a WAV preview from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output audio WAV path")
    ap.add_argument("--timeline-map", type=Path, required=True, help="Output timeline map JSON path")

    # Deprecated no-ops for v0.4 compatibility
    ap.add_argument("--preview", action="store_true", help="Deprecated no-op")
    ap.add_argument("--draft", action="store_true", help="Deprecated no-op")
    ap.add_argument("--build-subtitles", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-subtitles", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-loudnorm", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--allow-manual-fallback", action="store_true", help="Allow fallback to start/end seconds conversion when source frames are missing")

    args = ap.parse_args()

    # Reject .mp4 output files
    if args.output.suffix.lower() == ".mp4":
        print("Error: Rendering to .mp4 is rejected in v0.4. Audio-only WAV is required.", file=sys.stderr)
        sys.exit(1)

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        print(f"Error: EDL not found at {edl_path}", file=sys.stderr)
        sys.exit(1)

    # Read EDL and compute hash
    edl_bytes = edl_path.read_bytes()
    edl_hash = hashlib.sha256(edl_bytes).hexdigest()

    try:
        edl = json.loads(edl_bytes.decode("utf-8"))
    except Exception as e:
        print(f"Error: Failed to parse EDL JSON: {e}", file=sys.stderr)
        sys.exit(1)

    edit_dir = edl_path.parent
    sources = edl.get("sources", {})
    ranges = edl.get("ranges", [])

    # Determine FPS from sources or metadata: metadata.sequence_fps is authority first.
    seq_fps_val = edl.get("metadata", {}).get("sequence_fps")
    authority_fps = None
    if seq_fps_val:
        try:
            authority_fps = parse_fps_fraction(seq_fps_val)
        except Exception as e:
            print(f"Error: Invalid sequence_fps metadata: {seq_fps_val} ({e})", file=sys.stderr)
            sys.exit(1)

    probed_video_fps = None
    for source_id, rel_path in sources.items():
        src_path = resolve_path(rel_path, edit_dir)
        if src_path.suffix.lower() == ".wav":
            continue  # WAV does not dictate FPS

        cmd_fps = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "json", str(src_path)
        ]
        try:
            out = subprocess.check_output(cmd_fps, text=True)
            data = json.loads(out)
        except Exception as e:
            print(f"Error: ffprobe failed for non-WAV source '{source_id}' ({src_path.name}): {e}", file=sys.stderr)
            sys.exit(1)

        streams = data.get("streams", [])
        if not streams:
            print(f"Error: Video stream 'v:0' not found in non-WAV source '{source_id}' ({src_path.name}).", file=sys.stderr)
            sys.exit(1)

        r_fps = streams[0].get("r_frame_rate")
        if not r_fps or r_fps == "0/0":
            print(f"Error: Invalid or missing frame rate 'r_frame_rate' in video stream for source '{source_id}'.", file=sys.stderr)
            sys.exit(1)

        try:
            probed_fps = parse_fps_fraction(r_fps)
        except Exception as e:
            print(f"Error parsing probed frame rate '{r_fps}' for source '{source_id}': {e}", file=sys.stderr)
            sys.exit(1)

        if authority_fps is not None:
            if probed_fps != authority_fps:
                print(f"Error: Probed video FPS {probed_fps} for source '{source_id}' does not match sequence authority FPS {authority_fps}", file=sys.stderr)
                sys.exit(1)
        else:
            if probed_video_fps is None:
                probed_video_fps = probed_fps
            elif probed_fps != probed_video_fps:
                print(f"Error: Multiple video source frame rates detected and no sequence authority FPS defined in metadata.", file=sys.stderr)
                sys.exit(1)

    if authority_fps is not None:
        fps = authority_fps
    elif probed_video_fps is not None:
        fps = probed_video_fps
    else:
        print("Error: No frame rate found in sources or EDL metadata.", file=sys.stderr)
        sys.exit(1)

    # Process ranges
    ranges_map = []
    cum_start = 0

    # We will perform all rendering inside a temp directory to keep the workspace clean
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        segment_files = []

        try:
            for i, r in enumerate(ranges):
                source_id = r["source"]
                src_path = resolve_path(sources[source_id], edit_dir)
                if not src_path.exists():
                    print(f"Error: Source file not found: {src_path}", file=sys.stderr)
                    sys.exit(1)

                # Resolve frame boundaries
                F_in = r.get("source_in_frame")
                F_out = r.get("source_out_frame")
                if F_in is None or F_out is None:
                    if not args.allow_manual_fallback:
                        print("Error: Missing exact source frame boundaries ('source_in_frame' or 'source_out_frame') in EDL range. "
                              "Exact source frames are required in the automated renderer path.", file=sys.stderr)
                        sys.exit(1)
                    else:
                        print("Warning: Missing source frame boundaries. Using noisy manual fallback conversion from seconds.", file=sys.stderr)
                        if F_in is None:
                            F_in = time_to_frame(r["start"], fps, "round")
                        if F_out is None:
                            F_out = time_to_frame(r["end"], fps, "round")

                # Validate source_in_frame/source_out_frame as int non-bool, non-negative, and out > in
                if (not isinstance(F_in, int) or isinstance(F_in, bool) or
                    not isinstance(F_out, int) or isinstance(F_out, bool)):
                    print(f"Error: Resolved frames {F_in} and {F_out} must be integers and not booleans.", file=sys.stderr)
                    sys.exit(1)

                if F_in < 0 or F_out < 0:
                    print(f"Error: Resolved frames {F_in} and {F_out} must be non-negative.", file=sys.stderr)
                    sys.exit(1)

                if F_out <= F_in:
                    print(f"Error: Resolved out frame {F_out} must be strictly greater than in frame {F_in}.", file=sys.stderr)
                    sys.exit(1)

                start_sample = frame_to_sample(F_in, fps)
                end_sample = frame_to_sample(F_out, fps)
                duration_samples = end_sample - start_sample

                channels = probe_channels(src_path)
                channel_policy = "mono_to_stereo" if channels == 1 else "stereo_preserve" if channels == 2 else "multichannel_downmix"

                temp_wav = temp_dir_path / f"seg_{i:04d}.wav"

                extract_audio_segment(
                    source_path=src_path,
                    start_sample=start_sample,
                    end_sample=end_sample,
                    out_path=temp_wav
                )

                segment_files.append(temp_wav)

                ranges_map.append({
                    "source": source_id,
                    "source_frames": [int(F_in), int(F_out)],
                    "source_sample_interval": [start_sample, end_sample],
                    "output_cumulative_sample_interval": [cum_start, cum_start + duration_samples],
                    "seconds": round(float(Fraction(F_out - F_in) / fps), 6),
                    "source_channels": channels,
                    "channel_policy": channel_policy
                })

                cum_start += duration_samples

            # Concatenate the segments using the concat demuxer
            concat_list_path = temp_dir_path / "concat_list.txt"
            concat_content = "".join(f"file '{p.name}'\n" for p in segment_files)
            concat_list_path.write_text(concat_content, encoding="utf-8")

            temp_output_wav = temp_dir_path / "preview.wav"
            concat_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list_path),
                "-c", "copy",
                "-rf64", "auto",
                str(temp_output_wav)
            ]
            # Execute concat in the temp directory so relative filenames resolve correctly
            subprocess.run(concat_cmd, check=True, cwd=temp_dir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            # Formulate the global channel policy
            channel_policies = [rm["channel_policy"] for rm in ranges_map]
            if not channel_policies:
                global_channel_policy = "empty"
            elif all(cp == "mono_to_stereo" for cp in channel_policies):
                global_channel_policy = "mono_to_stereo"
            elif all(cp == "stereo_preserve" for cp in channel_policies):
                global_channel_policy = "stereo_preserve"
            elif all(cp == "multichannel_downmix" for cp in channel_policies):
                global_channel_policy = "multichannel_downmix"
            else:
                global_channel_policy = "mixed"

            timeline_map = {
                "edl_hash": edl_hash,
                "output_format": {
                    "format": "PCM16",
                    "sample_rate": 48000,
                    "channels": 2,
                    "channel_policy": global_channel_policy,
                    "sequence_fps": float(fps)
                },
                "ranges": ranges_map
            }

            # Atomic replacement of both outputs
            out_wav_path = args.output.resolve()
            out_map_path = args.timeline_map.resolve()

            # Write map JSON atomically
            map_content = json.dumps(timeline_map, indent=2)
            write_atomic(out_map_path, map_content)

            # Copy temp WAV to final output path atomically
            temp_final_wav = out_wav_path.with_suffix(out_wav_path.suffix + f".{os.getpid()}.tmp")
            try:
                import shutil
                shutil.copy2(temp_output_wav, temp_final_wav)
                os.replace(str(temp_final_wav), str(out_wav_path))
            except Exception as e:
                if temp_final_wav.exists():
                    temp_final_wav.unlink()
                raise e

            print(f"Render completed: {out_wav_path} ({cum_start} samples)")

        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
            print(f"Error during audio processing: {err_msg}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
