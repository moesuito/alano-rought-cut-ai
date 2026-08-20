"""Autonomous Rough Cut Orchestrator (Alano Rough Cut AI v0.5.0).

Executes the entire end-to-end rough cut pipeline in a single automated flow:
1. Inventory & Inspection
2. Denoised 4-in-1 GPU Transcription (Whisper Vulkan + Wav2Vec2 + Pyannote)
3. Transcript Packing (takes_packed.md)
4. LLM Editorial Intelligence (via OpenAI-compatible endpoint / NVIDIA NIM)
5. Acoustic Boundary Refinement (VAD + 66ms pre/post-roll padding + dynamic gaps)
6. Audio Preview Render & Quality Control (Audio QC)
7. Premiere FCP7 XML Timeline Export (timeline.xml)
8. Run State & Memory Persistence in AppData Session Cache
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.llm_client import generate_editorial_plan, get_llm_config, load_env_file
from helpers.session_manager import (
    SessionContext,
    create_new_session,
    export_deliverable,
    get_global_transcripts_cache_dir,
)
from helpers.timing import format_fps_fraction, parse_fps_fraction


SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4", ".mkv", ".m4v", ".webm", ".avi"}


def probe_video_file(file_path: Path) -> dict[str, Any]:
    """Inspect video file metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,width,height,duration:format=duration",
        "-of", "json",
        str(file_path)
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            raise ValueError("No video stream found")
        v = streams[0]
        fps_str = v.get("r_frame_rate", "30000/1001")
        width = int(v.get("width", 1920))
        height = int(v.get("height", 1080))
        dur_str = v.get("duration") or data.get("format", {}).get("duration", "0")
        duration = float(dur_str)
        return {
            "fps": fps_str,
            "width": width,
            "height": height,
            "duration": duration,
        }
    except Exception as e:
        return {
            "fps": "30000/1001",
            "width": 1920,
            "height": 1080,
            "duration": 0.0,
            "probe_error": str(e),
        }


def scan_inventory(raw_dir: Path) -> list[dict[str, Any]]:
    """Scan raw directory for supported video files and extract metadata."""
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw video directory does not exist: {raw_dir}")

    items = []
    for p in sorted(raw_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            meta = probe_video_file(p)
            items.append({
                "source_id": p.stem,
                "filename": p.name,
                "path": str(p.resolve()),
                "meta": meta,
            })

    if not items:
        raise ValueError(f"No video files ({', '.join(SUPPORTED_VIDEO_EXTENSIONS)}) found in {raw_dir}")

    return items


def run_autonomous_rough_cut(
    raw_dir: Path,
    edit_dir: Path | None = None,
    brief: str = "",
    video_type: str = "aula",
    provider: str = "whisper-vulkan",
    language: str = "pt",
    force_transcribe: bool = False,
    timeline_name: str | None = None,
    output_xml_filename: str = "timeline.xml",
    llm_config: dict[str, str] | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Execute the full autonomous rough cut pipeline with clean AppData session caching."""
    load_env_file()
    raw_dir = raw_dir.resolve()

    def update_progress(step: str, detail: str) -> None:
        if progress_callback:
            progress_callback(step, detail)

    # Initialize Session Context in AppData (Zero folder pollution)
    if edit_dir is None:
        session = create_new_session(working_dir=raw_dir, video_type=video_type, brief=brief)
        active_edit_dir = session.edit_dir
    else:
        active_edit_dir = Path(edit_dir).resolve()
        active_edit_dir.mkdir(parents=True, exist_ok=True)
        session = SessionContext(
            session_id=f"custom_{raw_dir.name}",
            created_at=datetime.datetime.now().isoformat(),
            working_dir=str(raw_dir),
            session_dir=str(active_edit_dir.parent),
            video_type=video_type,
            brief=brief,
        )

    transcripts_dir = active_edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------
    # Step 01: Inventory
    # -------------------------------------------------------------
    update_progress("1/7", "Inspecionando inventário de mídia...")
    inventory = scan_inventory(raw_dir)
    session.source_files = inventory
    session.save()

    sources_map = {item["source_id"]: item["path"] for item in inventory}

    # -------------------------------------------------------------
    # Step 02: 4-in-1 GPU Transcription (Whisper + Wav2Vec2 + Pyannote)
    # -------------------------------------------------------------
    update_progress("2/7", "Verificando e executando transcrições na GPU...")
    transcribe_needed = force_transcribe
    for item in inventory:
        t_file = transcripts_dir / f"{item['source_id']}.json"
        if not t_file.exists():
            transcribe_needed = True
            break

    if transcribe_needed:
        batch_script = PROJECT_ROOT / "helpers" / "transcribe_batch.py"
        cmd_transcribe = [
            sys.executable, str(batch_script),
            str(raw_dir),
            "--edit-dir", str(active_edit_dir),
            "--provider", provider,
            "--diarization", "community-1",
            "--language", language,
        ]
        if force_transcribe:
            cmd_transcribe.append("--force")

        subprocess.check_call(cmd_transcribe)

    # -------------------------------------------------------------
    # Step 03: Pack Transcripts (takes_packed.md)
    # -------------------------------------------------------------
    update_progress("3/7", "Agrupando transcrições em frases e pausas...")
    pack_script = PROJECT_ROOT / "helpers" / "pack_transcripts.py"
    cmd_pack = [
        sys.executable, str(pack_script),
        "--edit-dir", str(active_edit_dir),
    ]
    subprocess.check_call(cmd_pack)
    takes_packed_file = active_edit_dir / "takes_packed.md"
    if not takes_packed_file.exists():
        raise FileNotFoundError(f"takes_packed.md was not generated at {takes_packed_file}")
    takes_packed_content = takes_packed_file.read_text(encoding="utf-8")

    # -------------------------------------------------------------
    # Step 04 & 05 & 06: Cognitive LLM Editorial Decision
    # -------------------------------------------------------------
    update_progress("4/7", "Montando plano de corte inteligente com LLM...")
    editorial_brief = brief.strip()
    if not editorial_brief:
        editorial_brief = f"Corte autônomo para {video_type}. Identifique os melhores takes, elimine falsos inícios, hesitações e falas de direção."

    cut_plan = generate_editorial_plan(
        brief=editorial_brief,
        takes_packed_content=takes_packed_content,
        video_type=video_type,
        config=llm_config,
    )

    if not timeline_name:
        sanitized_slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in video_type).lower()
        timeline_name = f"{sanitized_slug}_rough_cut_alano"

    edl_path = active_edit_dir / "edl.json"
    edl_data = {
        "sequence_name": timeline_name,
        "fps": 29.97,
        "timebase": 30,
        "metadata": {
            "timeline_name": timeline_name,
            "video_type": video_type,
            "sequence_fps": "30000/1001",
        },
        "sources": sources_map,
        "ranges": [
            {
                "source": c["source"],
                "start": c["start"],
                "end": c["end"],
                "beat": c["beat"],
                "quote": c["quote"],
                "reason": c["reason"],
                "is_list": c["is_list"],
            }
            for c in cut_plan
        ],
    }
    edl_path.write_text(json.dumps(edl_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # -------------------------------------------------------------
    # Step 07: Acoustic Boundary Refinement & Snapper
    # -------------------------------------------------------------
    update_progress("5/7", "Refinando bordas acústicas, VAD e aplicando padding...")
    refine_script = PROJECT_ROOT / "helpers" / "refine_edl_boundaries.py"
    refine_report_path = active_edit_dir / "refine_report.json"
    cmd_refine = [
        sys.executable, str(refine_script),
        str(edl_path),
        "--transcripts", str(transcripts_dir),
        "--report", str(refine_report_path),
    ]
    res_refine = subprocess.run(cmd_refine)
    if res_refine.returncode not in {0, 2}:
        raise RuntimeError(f"refine_edl_boundaries failed with exit code {res_refine.returncode}")

    # -------------------------------------------------------------
    # Step 08: Audio Render & Quality Control (Audio QC)
    # -------------------------------------------------------------
    update_progress("6/7", "Renderizando áudio do preview e validando QC...")
    render_script = PROJECT_ROOT / "helpers" / "render.py"
    preview_wav_path = active_edit_dir / "preview.wav"
    timeline_map_path = active_edit_dir / "preview_timeline.json"
    cmd_render = [
        sys.executable, str(render_script),
        str(edl_path),
        "-o", str(preview_wav_path),
        "--timeline-map", str(timeline_map_path),
    ]
    subprocess.check_call(cmd_render)

    audio_qc_script = PROJECT_ROOT / "helpers" / "preview_audio_qc.py"
    qc_report_path = active_edit_dir / "preview_audio_qc.json"
    cmd_qc = [
        sys.executable, str(audio_qc_script),
        str(preview_wav_path),
        "--edl", str(edl_path),
        "--timeline-map", str(timeline_map_path),
        "-o", str(qc_report_path),
    ]
    subprocess.run(cmd_qc)

    # -------------------------------------------------------------
    # Step 09: Final FCP7 XML Export & Clean Delivery
    # -------------------------------------------------------------
    update_progress("7/7", "Exportando timeline XML para o Premiere...")
    xml_script = PROJECT_ROOT / "helpers" / "edl_to_fcpxml.py"
    session_timeline_xml = active_edit_dir / "timeline.xml"
    cmd_xml = [
        sys.executable, str(xml_script),
        str(edl_path),
        "-o", str(session_timeline_xml),
    ]
    subprocess.check_call(cmd_xml)

    # Export deliverable directly to user working folder (cleanly)
    final_user_xml = export_deliverable(session, raw_dir, output_filename=output_xml_filename)

    # Calculate final duration
    try:
        refined_edl = json.loads(edl_path.read_text(encoding="utf-8"))
        ranges = refined_edl.get("ranges", [])
        total_dur = sum(r["end"] - r["start"] for r in ranges)
        session.cuts_count = len(ranges)
        session.total_duration_s = total_dur
    except Exception:
        session.cuts_count = len(cut_plan)

    session.save()

    return {
        "status": "success",
        "timeline_xml": str(final_user_xml),
        "session_dir": str(session.dir),
        "preview_wav": str(preview_wav_path),
        "takes_count": session.cuts_count,
        "total_duration_s": session.total_duration_s,
        "timeline_name": timeline_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Alano Rough Cut AI Autonomous Orchestrator (Mode 1)")
    parser.add_argument("raw_dir", nargs="?", default=".", help="Directory containing raw video files (default: current directory)")
    parser.add_argument("--edit-dir", default=None, help="Custom directory for edit cache (default: AppData/AlanoCut)")
    parser.add_argument("--brief", "-b", default="", help="Optional user editorial brief / instructions")
    parser.add_argument("--video-type", "-t", default="aula", help="Content type (aula, reels, tiktok, tutorial, etc.)")
    parser.add_argument("--provider", default="whisper-vulkan", help="ASR Provider (whisper-vulkan, elevenlabs, assemblyai)")
    parser.add_argument("--language", "-l", default="pt", help="Language code (pt, en, etc.)")
    parser.add_argument("--force-transcribe", action="store_true", help="Force re-transcription of all audio")
    parser.add_argument("--timeline-name", default=None, help="Custom name for the output sequence")
    parser.add_argument("--output-xml", default="timeline.xml", help="Output filename in raw directory (default: timeline.xml)")

    args = parser.parse_args()
    raw_path = Path(args.raw_dir)
    edit_path = Path(args.edit_dir) if args.edit_dir else None

    try:
        res = run_autonomous_rough_cut(
            raw_dir=raw_path,
            edit_dir=edit_path,
            brief=args.brief,
            video_type=args.video_type,
            provider=args.provider,
            language=args.language,
            force_transcribe=args.force_transcribe,
            timeline_name=args.timeline_name,
            output_xml_filename=args.output_xml,
        )
        print(f"\n✅ Concluído! Timeline gerada em: {res['timeline_xml']}")
    except Exception as e:
        print(f"\n❌ Erro na execução do orquestrador: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
