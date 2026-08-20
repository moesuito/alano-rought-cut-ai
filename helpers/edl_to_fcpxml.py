"""Convert an EDL JSON timeline to Final Cut Pro 7 XML (XMEML) format.

This allows importing the edited timeline directly into Adobe Premiere Pro
with the original raw clips placed on the video and audio tracks.

Usage:
    python helpers/edl_to_fcpxml.py <edl_path>
    python helpers/edl_to_fcpxml.py raw_video/edit/edl.json -o raw_video/edit/timeline.xml
    python helpers/edl_to_fcpxml.py raw_video/edit/edl.json --timeline-name "reels 35_cadastro_alano-cut"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Support running directly as a script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fractions import Fraction
from helpers.timing import parse_fps_fraction, time_to_frame



FALLBACK_SUFFIX = "alano-cut"


def get_video_metadata(file_path: Path) -> dict:
    """Probe the video file using ffprobe to retrieve dimensions, frame rate, and duration."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "json", str(file_path)
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            raise ValueError("No video streams found")
        stream = streams[0]

        # Parse frame rate as exact Fraction
        r_frame_rate = stream.get("r_frame_rate", "24/1")
        try:
            fps = Fraction(r_frame_rate)
        except Exception:
            if "/" in r_frame_rate:
                num, den = map(int, r_frame_rate.split("/"))
                fps = Fraction(num, den) if den != 0 else Fraction(24, 1)
            else:
                fps = Fraction(float(r_frame_rate))

        duration = float(stream.get("duration", 0.0))
        # If duration is missing, try format container duration
        if duration == 0.0:
            cmd_fmt = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", str(file_path)
            ]
            out_fmt = subprocess.check_output(cmd_fmt, text=True)
            data_fmt = json.loads(out_fmt)
            duration = float(data_fmt.get("format", {}).get("duration", 0.0))

        return {
            "width": int(stream.get("width", 1920)),
            "height": int(stream.get("height", 1080)),
            "fps": fps,
            "duration": duration
        }
    except Exception as e:
        print(f"Warning: could not probe metadata for {file_path.name}: {e}", file=sys.stderr)
        # Default fallback values for standard formats
        return {
            "width": 1920,
            "height": 1080,
            "fps": Fraction(24, 1),
            "duration": 3600.0  # fallback 1 hour
        }


def get_timebase_and_ntsc(fps: Fraction) -> tuple[int, str]:
    """Map FPS Fraction to FCP 7 XML timebase and NTSC standards."""
    if fps == Fraction(24000, 1001):
        return 24, "TRUE"
    elif fps == Fraction(30000, 1001):
        return 30, "TRUE"
    elif fps == Fraction(60000, 1001):
        return 60, "TRUE"
    elif fps.denominator == 1:
        return int(fps.numerator), "FALSE"
    else:
        # Non-canonical fractional rates must be NTSC FALSE
        return int(round(float(fps))), "FALSE"


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def slug_part(value: object, allow_spaces: bool = False) -> str:
    text = strip_accents(str(value)).lower().strip()
    replacement = " " if allow_spaces else "_"
    text = re.sub(r"[^a-z0-9]+", replacement, text)
    if allow_spaces:
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = re.sub(r"_+", "_", text).strip("_")
    return text


def sanitize_timeline_name(value: object) -> str:
    """Keep a Premiere-friendly display name while removing path-like chars."""
    text = str(value).strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"_+", "_", text)
    return text.strip(" _")


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def infer_content_label(edl_path: Path) -> str:
    """Infer a content label from the workspace when the EDL has no metadata."""
    edit_dir = edl_path.parent
    videos_dir = edit_dir.parent if edit_dir.name.lower() == "edit" else edit_dir
    generic_video_dirs = {"raw_video", "raw-videos", "raw videos", "videos", "video"}
    project_dir = videos_dir.parent if videos_dir.name.lower() in generic_video_dirs else videos_dir
    return project_dir.name or "rough_cut"


def format_timeline_name(
    video_type: object | None,
    content_number: object | None,
    content_label: object | None,
) -> str:
    """Build names like 'reels 35_cadastro_alano-cut'."""
    type_part = slug_part(video_type, allow_spaces=True) if video_type else ""
    number_part = slug_part(content_number) if content_number else ""
    content_part = slug_part(content_label) if content_label else "rough_cut"

    if type_part and number_part:
        prefix = f"{type_part} {number_part}"
    elif type_part:
        prefix = type_part
    elif number_part:
        prefix = number_part
    else:
        prefix = ""

    if prefix:
        return f"{prefix}_{content_part}_{FALLBACK_SUFFIX}"
    return f"{content_part}_{FALLBACK_SUFFIX}"


def resolve_timeline_name(edl: dict, edl_path: Path, override: str | None = None) -> str:
    """Resolve the Premiere project/sequence name from CLI, EDL metadata, or folder context."""
    if override:
        cleaned = sanitize_timeline_name(override)
        if cleaned:
            return cleaned

    metadata = edl.get("metadata") if isinstance(edl.get("metadata"), dict) else {}
    explicit = first_value(
        edl.get("timeline_name"),
        metadata.get("timeline_name"),
        metadata.get("sequence_name"),
        metadata.get("xml_timeline_name"),
    )
    if explicit:
        cleaned = sanitize_timeline_name(explicit)
        if cleaned:
            return cleaned

    video_type = first_value(
        metadata.get("timeline_video_type"),
        metadata.get("video_type"),
        metadata.get("inferred_type"),
        metadata.get("content_type"),
        edl.get("video_type"),
    )
    content_number = first_value(
        metadata.get("content_number"),
        metadata.get("video_number"),
        metadata.get("episode_number"),
        metadata.get("lesson_number"),
        edl.get("content_number"),
    )
    content_label = first_value(
        metadata.get("content_slug"),
        metadata.get("content"),
        metadata.get("topic"),
        metadata.get("title"),
        edl.get("content_slug"),
        infer_content_label(edl_path),
    )
    return sanitize_timeline_name(format_timeline_name(video_type, content_number, content_label))


def convert_edl_to_xml(
    edl_path: Path,
    output_path: Path,
    timeline_name: str | None = None,
    project_name: str | None = None,
    crossfade_frames: int = 4,
) -> None:
    # Load EDL JSON
    if not edl_path.exists():
        sys.exit(f"Error: EDL file not found at {edl_path}")

    # Warn but do not block if QC check fails or is stale
    verify_script = Path(__file__).parent / "verify_edit_ready.py"
    if verify_script.exists():
        edit_dir = edl_path.parent
        cmd = [
            sys.executable, str(verify_script), str(edl_path),
            "--transcripts", str(edit_dir / "transcripts"),
            "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
            "--audio-report", str(edit_dir / "preview_audio_qc.json"),
            "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
            "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
            "--audio", str(edit_dir / "preview.wav"),
            "--timeline-map", str(edit_dir / "preview_timeline.json")
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"Warning: QC verification failed or reports are stale (exit code {res.returncode}). Proceeding with XML generation.", file=sys.stderr)
        except Exception as e:
            print(f"Warning: could not run verification check: {e}", file=sys.stderr)

    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    sources = edl.get("sources", {})
    ranges = edl.get("ranges", [])
    resolved_timeline_name = resolve_timeline_name(edl, edl_path, timeline_name)
    resolved_project_name = sanitize_timeline_name(project_name) if project_name else resolved_timeline_name

    if not ranges:
        sys.exit("Error: No cut ranges found in the EDL")

    # Probe first source to establish sequence parameters
    first_src_name = ranges[0]["source"]
    first_src_path = Path(sources[first_src_name])
    if not first_src_path.is_absolute():
        first_src_path = (edl_path.parent / first_src_path).resolve()

    first_metadata = get_video_metadata(first_src_path)
    seq_width = first_metadata["width"]
    seq_height = first_metadata["height"]

    # Determine timeline sequence fps authority
    seq_fps = None
    metadata_fps = edl.get("metadata", {}).get("sequence_fps")
    if metadata_fps is not None:
        try:
            seq_fps = parse_fps_fraction(metadata_fps)
        except Exception as e:
            print(f"Warning: Invalid sequence_fps in metadata: {metadata_fps} ({e})", file=sys.stderr)

    if seq_fps is None:
        # Fallback to probing sources, but if a source is WAV, it is WAV-only and doesn't dictate fps.
        # Let's check non-WAV sources first.
        fps_set = set()
        for source_name, source_rel_path in sources.items():
            source_path = Path(source_rel_path)
            if not source_path.is_absolute():
                source_path = (edl_path.parent / source_path).resolve()
            if source_path.suffix.lower() != ".wav":
                try:
                    source_meta = get_video_metadata(source_path)
                    fps_set.add(parse_fps_fraction(source_meta["fps"]))
                except Exception:
                    pass
        if len(fps_set) == 1:
            seq_fps = list(fps_set)[0]
        elif len(fps_set) > 1:
            seq_fps = list(fps_set)[0]
        else:
            try:
                seq_fps = parse_fps_fraction(first_metadata["fps"])
            except Exception:
                seq_fps = Fraction(24, 1)

    seq_timebase, seq_ntsc = get_timebase_and_ntsc(seq_fps)

    # --- Build XML Tree ---
    # Root element
    root = ET.Element("xmeml", version="5")

    # Project structure
    project = ET.SubElement(root, "project")
    ET.SubElement(project, "name").text = resolved_project_name
    children = ET.SubElement(project, "children")

    # Sequence structure
    sequence = ET.SubElement(children, "sequence")
    ET.SubElement(sequence, "name").text = resolved_timeline_name

    # We will compute and fill sequence duration after the loop
    seq_duration_el = ET.SubElement(sequence, "duration")

    seq_rate = ET.SubElement(sequence, "rate")
    ET.SubElement(seq_rate, "timebase").text = str(seq_timebase)
    ET.SubElement(seq_rate, "ntsc").text = seq_ntsc

    # Media tracks setup
    media = ET.SubElement(sequence, "media")

    # Video setup
    video = ET.SubElement(media, "video")
    v_format = ET.SubElement(video, "format")
    v_sc = ET.SubElement(v_format, "samplecharacteristics")
    ET.SubElement(v_sc, "width").text = str(seq_width)
    ET.SubElement(v_sc, "height").text = str(seq_height)

    video_track = ET.SubElement(video, "track")

    # Audio setup (Single Stereo track layout)
    audio = ET.SubElement(media, "audio")
    audio_track1 = ET.SubElement(audio, "track")

    start_timeline_frame = 0
    defined_files = set()

    print(f"Converting {len(ranges)} cuts to FCP XML...")

    for idx, r in enumerate(ranges, start=1):
        source_name = r["source"]
        source_path_raw = Path(sources[source_name])

        # Resolve path
        if source_path_raw.is_absolute():
            source_path = source_path_raw
        else:
            source_path = (edl_path.parent / source_path_raw).resolve()

        start_sec = float(r["start"])
        end_sec = float(r["end"])

        metadata = get_video_metadata(source_path)
        is_wav = source_path.suffix.lower() == ".wav"
        if is_wav:
            clip_fps_frac = seq_fps
        else:
            clip_fps_frac = metadata["fps"]

        clip_timebase, clip_ntsc = get_timebase_and_ntsc(clip_fps_frac)

        # Calculate frame ranges
        in_frame = r.get("source_in_frame")
        out_frame = r.get("source_out_frame")
        if in_frame is None:
            in_frame = time_to_frame(start_sec, clip_fps_frac, "round")
        if out_frame is None:
            out_frame = time_to_frame(end_sec, clip_fps_frac, "round")

        in_frame = int(in_frame)
        out_frame = int(out_frame)
        duration_frames = out_frame - in_frame

        end_timeline_frame = start_timeline_frame + duration_frames

        file_id = f"file-{source_name}"

        # Unique IDs for each track item
        clip_v_id = f"clipitem-v-{idx}"
        clip_a1_id = f"clipitem-a1-{idx}"

        # Print status of segment
        note = r.get("beat") or r.get("note") or f"segment_{idx}"
        print(f"  [{idx:02d}] {source_name} ({start_sec:.2f}s - {end_sec:.2f}s) -> frames {start_timeline_frame} to {end_timeline_frame} [{note}]")

        # ------------------ VIDEO CLIPITEM ------------------
        clipitem_v = ET.SubElement(video_track, "clipitem", id=clip_v_id)
        ET.SubElement(clipitem_v, "name").text = source_path.name
        ET.SubElement(clipitem_v, "duration").text = str(duration_frames)

        rate = ET.SubElement(clipitem_v, "rate")
        ET.SubElement(rate, "timebase").text = str(clip_timebase)
        ET.SubElement(rate, "ntsc").text = clip_ntsc

        ET.SubElement(clipitem_v, "in").text = str(in_frame)
        ET.SubElement(clipitem_v, "out").text = str(out_frame)
        ET.SubElement(clipitem_v, "start").text = str(start_timeline_frame)
        ET.SubElement(clipitem_v, "end").text = str(end_timeline_frame)

        ET.SubElement(clipitem_v, "pixelaspect").text = "Square"
        ET.SubElement(clipitem_v, "anamorphic").text = "FALSE"

        file_el_v = ET.SubElement(clipitem_v, "file", id=file_id)
        if file_id not in defined_files:
            defined_files.add(file_id)
            ET.SubElement(file_el_v, "name").text = source_path.name
            ET.SubElement(file_el_v, "pathurl").text = source_path.as_uri()

            f_rate = ET.SubElement(file_el_v, "rate")
            ET.SubElement(f_rate, "timebase").text = str(clip_timebase)
            ET.SubElement(f_rate, "ntsc").text = clip_ntsc

            file_dur_frames = int(round(float(Fraction(metadata["duration"]) * clip_fps_frac)))
            ET.SubElement(file_el_v, "duration").text = str(file_dur_frames)

            # Master media description
            m_desc = ET.SubElement(file_el_v, "media")

            v_desc = ET.SubElement(m_desc, "video")
            v_sc = ET.SubElement(v_desc, "samplecharacteristics")
            ET.SubElement(v_sc, "width").text = str(metadata["width"])
            ET.SubElement(v_sc, "height").text = str(metadata["height"])

            a_desc = ET.SubElement(m_desc, "audio")
            ET.SubElement(a_desc, "channelcount").text = "2"

        # ------------------ AUDIO TRACK 1 CLIPITEM ------------------
        if crossfade_frames > 0 and idx > 1:
            half_dur = crossfade_frames // 2
            trans_start = max(0, start_timeline_frame - half_dur)
            trans_end = start_timeline_frame + (crossfade_frames - half_dur)

            trans_item = ET.SubElement(audio_track1, "transitionitem")
            ET.SubElement(trans_item, "start").text = str(trans_start)
            ET.SubElement(trans_item, "end").text = str(trans_end)
            ET.SubElement(trans_item, "alignment").text = "center"

            t_rate = ET.SubElement(trans_item, "rate")
            ET.SubElement(t_rate, "timebase").text = str(seq_timebase)
            ET.SubElement(t_rate, "ntsc").text = seq_ntsc

            effect = ET.SubElement(trans_item, "effect")
            ET.SubElement(effect, "name").text = "Cross Fade (+3dB)"
            ET.SubElement(effect, "effectid").text = "CrossFade3dB"
            ET.SubElement(effect, "effecttype").text = "transition"
            ET.SubElement(effect, "mediatype").text = "audio"

        clipitem_a1 = ET.SubElement(audio_track1, "clipitem", id=clip_a1_id)
        ET.SubElement(clipitem_a1, "name").text = source_path.name
        ET.SubElement(clipitem_a1, "duration").text = str(duration_frames)

        rate = ET.SubElement(clipitem_a1, "rate")
        ET.SubElement(rate, "timebase").text = str(clip_timebase)
        ET.SubElement(rate, "ntsc").text = clip_ntsc

        ET.SubElement(clipitem_a1, "in").text = str(in_frame)
        ET.SubElement(clipitem_a1, "out").text = str(out_frame)
        ET.SubElement(clipitem_a1, "start").text = str(start_timeline_frame)
        ET.SubElement(clipitem_a1, "end").text = str(end_timeline_frame)

        ET.SubElement(clipitem_a1, "file", id=file_id)
        s_track1 = ET.SubElement(clipitem_a1, "sourcetrack")
        ET.SubElement(s_track1, "mediatype").text = "audio"
        ET.SubElement(s_track1, "trackindex").text = "1"
        # ------------------ LINK VIDEO & AUDIO TOGETHER ------------------
        for item in [clipitem_v, clipitem_a1]:
            l_v = ET.SubElement(item, "link")
            ET.SubElement(l_v, "linkclipref").text = clip_v_id
            ET.SubElement(l_v, "mediatype").text = "video"
            ET.SubElement(l_v, "trackindex").text = "1"
            ET.SubElement(l_v, "clipindex").text = str(idx)

            l_a1 = ET.SubElement(item, "link")
            ET.SubElement(l_a1, "linkclipref").text = clip_a1_id
            ET.SubElement(l_a1, "mediatype").text = "audio"
            ET.SubElement(l_a1, "trackindex").text = "1"
            ET.SubElement(l_a1, "clipindex").text = str(idx)

        start_timeline_frame = end_timeline_frame

    # Set final sequence duration in the XML
    seq_duration_el.text = str(start_timeline_frame)

    # Output XML bytes
    xml_bytes = ET.tostring(root, encoding="utf-8")

    # Save with custom header
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        f.write(xml_bytes)

    print(f"\nSuccessfully generated Premiere-compatible XML:")
    print(f"  -> {output_path.resolve()}")
    print(f"  -> Timeline name: {resolved_timeline_name}")
    print(f"  -> Total frames: {start_timeline_frame} (~{start_timeline_frame / seq_fps:.2f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EDL JSON to Premiere-compatible FCP XML")
    parser.add_argument("edl", type=Path, nargs="?", default=Path("raw_video/edit/edl.json"),
                        help="Path to edl.json (default: raw_video/edit/edl.json)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output XML path (default: same directory as edl.json, named timeline.xml)")
    parser.add_argument("--timeline-name", type=str, default=None,
                        help='Premiere sequence name, e.g. "reels 35_cadastro_alano-cut". '
                             "If omitted, reads EDL metadata.timeline_name or derives a fallback.")
    parser.add_argument("--project-name", type=str, default=None,
                        help="Optional FCP XML project name. Defaults to the timeline name.")
    parser.add_argument("--crossfade-frames", type=int, default=4,
                        help="Number of frames for audio crossfade transitions across cuts (default: 4, 0 to disable)")
    args = parser.parse_args()

    edl_path = args.edl.resolve()
    output_path = args.output
    if output_path is None:
        output_path = edl_path.parent / "timeline.xml"

    convert_edl_to_xml(
        edl_path,
        output_path,
        timeline_name=args.timeline_name,
        project_name=args.project_name,
        crossfade_frames=args.crossfade_frames,
    )


if __name__ == "__main__":
    main()
