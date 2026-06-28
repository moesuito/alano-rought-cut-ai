"""Convert Final Cut Pro 7 XML (XMEML) back to Alano EDL JSON.

This is a QA and correction-analysis helper. It reads the first non-empty
video track from a Premiere/FCP7 XML and writes ranges in the same shape
consumed by render.py and edl_to_fcpxml.py.

Usage:
    python helpers/fcpxml_to_edl.py raw_video/edit/timeline_fix.xml
    python helpers/fcpxml_to_edl.py raw_video/edit/timeline_fix.xml -o raw_video/edit/timeline_fix.edl.json --media-root raw_video
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path


def fps_from_rate(rate_el: ET.Element | None) -> float:
    """Return FPS from an XMEML <rate> element."""
    if rate_el is None:
        return 30.0
    timebase_text = rate_el.findtext("timebase") or "30"
    ntsc_text = (rate_el.findtext("ntsc") or "FALSE").strip().upper()
    timebase = float(timebase_text)
    if ntsc_text == "TRUE":
        return timebase * 1000.0 / 1001.0
    return timebase


def path_from_pathurl(pathurl: str | None) -> Path | None:
    """Decode an XMEML file:// pathurl into a local Path."""
    if not pathurl:
        return None

    value = pathurl.strip()
    if value.startswith("file://localhost/"):
        value = value[len("file://localhost/") :]
    elif value.startswith("file://"):
        value = value[len("file://") :]

    value = urllib.parse.unquote(value)

    # Windows pathurls often arrive as /C:/Users/... after stripping file://.
    if len(value) >= 4 and value[0] == "/" and value[2:4] == ":/":
        value = value[1:]

    return Path(value)


def find_media_by_name(media_root: Path, source_name: str) -> Path | None:
    """Resolve a media filename under media_root, with a case-insensitive fallback."""
    matches = sorted(p for p in media_root.rglob(source_name) if p.is_file())
    if matches:
        return matches[0].resolve()

    wanted = source_name.lower()
    for p in media_root.rglob("*"):
        if p.is_file() and p.name.lower() == wanted:
            return p.resolve()
    return None


def resolve_source_path(source_name: str, xml_pathurl: str | None, media_root: Path | None) -> Path:
    xml_path = path_from_pathurl(xml_pathurl)
    if media_root:
        found = find_media_by_name(media_root, source_name)
        if found:
            return found

    if xml_path and xml_path.exists():
        return xml_path.resolve()

    if xml_path:
        return xml_path
    return Path(source_name)


def file_pathurl_for_clip(clip: ET.Element, file_pathurls_by_id: dict[str, str]) -> str | None:
    """Read the pathurl from a clip file element, following later file id references."""
    file_el = clip.find("file")
    if file_el is None:
        return None

    pathurl = file_el.findtext("pathurl")
    file_id = file_el.get("id")
    if pathurl:
        if file_id:
            file_pathurls_by_id[file_id] = pathurl
        return pathurl

    if file_id:
        return file_pathurls_by_id.get(file_id)
    return None


def convert_xml_to_edl(xml_path: Path, output_path: Path, media_root: Path | None) -> dict:
    root = ET.parse(xml_path).getroot()
    if root.tag != "xmeml":
        sys.exit(f"expected xmeml root, got: {root.tag}")

    sequence = root.find(".//sequence")
    if sequence is None:
        sys.exit("no sequence found")

    seq_fps = fps_from_rate(sequence.find("rate"))
    video = sequence.find("./media/video")
    if video is None:
        sys.exit("no video media found")

    tracks = video.findall("./track")
    if not tracks:
        sys.exit("no video tracks found")

    video_clips: list[ET.Element] = []
    for track in tracks:
        video_clips = track.findall("./clipitem")
        if video_clips:
            break
    if not video_clips:
        sys.exit("no video clipitems found")

    sources: dict[str, str] = {}
    file_pathurls_by_id: dict[str, str] = {}
    ranges: list[dict[str, object]] = []

    for idx, clip in enumerate(video_clips, start=1):
        source_name = clip.findtext("name") or f"clip_{idx:03d}"
        source_id = Path(source_name).stem

        clip_fps = fps_from_rate(clip.find("rate")) or seq_fps
        start_frame = int(clip.findtext("start") or "0")
        end_frame = int(clip.findtext("end") or "0")
        in_frame = int(clip.findtext("in") or "0")
        out_frame = int(clip.findtext("out") or "0")
        if end_frame <= start_frame or out_frame <= in_frame:
            continue

        pathurl = file_pathurl_for_clip(clip, file_pathurls_by_id)
        source_path = resolve_source_path(source_name, pathurl, media_root)
        sources[source_id] = str(source_path)

        start_s = round(in_frame / clip_fps, 3)
        end_s = round(out_frame / clip_fps, 3)
        ranges.append(
            {
                "source": source_id,
                "start": start_s,
                "end": end_s,
                "beat": "FIXED_XML",
                "quote": "",
                "reason": "Imported from timeline XML video clipitem",
                "timeline_start": round(start_frame / seq_fps, 3),
                "timeline_end": round(end_frame / seq_fps, 3),
                "xml_clip_id": clip.get("id"),
            }
        )

    total = round(sum(float(r["end"]) - float(r["start"]) for r in ranges), 3)
    edl = {
        "version": 1,
        "sources": sources,
        "ranges": ranges,
        "total_duration_s": total,
        "metadata": {
            "source_xml": str(xml_path),
            "timeline_name": sequence.findtext("name"),
            "sequence_fps": round(seq_fps, 6),
            "range_count": len(ranges),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
    return edl


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert FCP7/XMEML timeline XML to Alano EDL JSON")
    ap.add_argument("xml", type=Path, help="Input FCP7/XMEML XML")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output EDL JSON")
    ap.add_argument("--media-root", type=Path, default=None, help="Root to resolve media by filename")
    args = ap.parse_args()

    xml_path = args.xml.resolve()
    if not xml_path.exists():
        sys.exit(f"xml not found: {xml_path}")

    media_root = args.media_root.resolve() if args.media_root else None
    output = args.output or xml_path.with_name(f"{xml_path.stem}_from_xml.edl.json")
    edl = convert_xml_to_edl(xml_path, output, media_root)

    print(f"wrote {len(edl['ranges'])} ranges -> {output}")
    print(f"total_duration_s={edl['total_duration_s']}")


if __name__ == "__main__":
    main()
