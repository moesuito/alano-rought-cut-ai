"""Convert an EDL JSON timeline to Final Cut Pro 7 XML (XMEML) format.

This allows importing the edited timeline directly into Adobe Premiere Pro
with the original raw clips placed on the video and audio tracks.

Usage:
    python helpers/edl_to_fcpxml.py <edl_path>
    python helpers/edl_to_fcpxml.py raw_video/edit/edl.json -o raw_video/edit/timeline.xml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


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
        
        # Parse frame rate (can be a fraction like "24/1" or "30000/1001")
        r_frame_rate = stream.get("r_frame_rate", "24/1")
        if "/" in r_frame_rate:
            num, den = map(int, r_frame_rate.split("/"))
            fps = num / den if den != 0 else 24.0
        else:
            fps = float(r_frame_rate)
            
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
            "fps": 24.0,
            "duration": 3600.0  # fallback 1 hour
        }


def get_timebase_and_ntsc(fps: float) -> tuple[int, str]:
    """Map FPS to FCP 7 XML timebase and NTSC standards."""
    if abs(fps - 23.976) < 0.1:
        return 24, "TRUE"
    elif abs(fps - 29.97) < 0.1:
        return 30, "TRUE"
    elif abs(fps - 59.94) < 0.1:
        return 60, "TRUE"
    else:
        return int(round(fps)), "FALSE"


def convert_edl_to_xml(edl_path: Path, output_path: Path) -> None:
    # Load EDL JSON
    if not edl_path.exists():
        sys.exit(f"Error: EDL file not found at {edl_path}")
        
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    sources = edl.get("sources", {})
    ranges = edl.get("ranges", [])
    
    if not ranges:
        sys.exit("Error: No cut ranges found in the EDL")

    # Probe first source to establish sequence parameters
    first_src_name = ranges[0]["source"]
    first_src_path = Path(sources[first_src_name])
    if not first_src_path.is_absolute():
        first_src_path = (edl_path.parent / first_src_path).resolve()
        
    first_metadata = get_video_metadata(first_src_path)
    seq_fps = first_metadata["fps"]
    seq_timebase, seq_ntsc = get_timebase_and_ntsc(seq_fps)
    seq_width = first_metadata["width"]
    seq_height = first_metadata["height"]

    # --- Build XML Tree ---
    # Root element
    root = ET.Element("xmeml", version="5")

    # Project structure
    project = ET.SubElement(root, "project")
    ET.SubElement(project, "name").text = "Ticto Edited Project"
    children = ET.SubElement(project, "children")

    # Sequence structure
    sequence = ET.SubElement(children, "sequence")
    ET.SubElement(sequence, "name").text = "Ticto Edited Timeline"
    
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
        clip_fps = metadata["fps"]
        clip_timebase, clip_ntsc = get_timebase_and_ntsc(clip_fps)
        
        # Calculate frame ranges
        in_frame = int(round(start_sec * clip_fps))
        out_frame = int(round(end_sec * clip_fps))
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
            
            file_dur_frames = int(round(metadata["duration"] * clip_fps))
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
    print(f"  -> Total frames: {start_timeline_frame} (~{start_timeline_frame / seq_fps:.2f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EDL JSON to Premiere-compatible FCP XML")
    parser.add_argument("edl", type=Path, nargs="?", default=Path("raw_video/edit/edl.json"),
                        help="Path to edl.json (default: raw_video/edit/edl.json)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output XML path (default: same directory as edl.json, named timeline.xml)")
    args = parser.parse_args()

    edl_path = args.edl.resolve()
    output_path = args.output
    if output_path is None:
        output_path = edl_path.parent / "timeline.xml"
        
    convert_edl_to_xml(edl_path, output_path)


if __name__ == "__main__":
    main()
