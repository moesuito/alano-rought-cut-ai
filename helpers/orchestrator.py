"""Autonomous Rough Cut Orchestrator (Alano Rough Cut AI v0.5.0).

Executes the entire end-to-end rough cut pipeline in a single automated flow:
1. Inventory & Inspection
2. Denoised 4-in-1 GPU Transcription (Whisper Vulkan + Wav2Vec2 + Pyannote)
3. Transcript Packing (takes_packed.md)
4. LLM Editorial Intelligence (via OpenAI-compatible endpoint / NVIDIA NIM)
5. Acoustic Boundary Refinement (VAD + 66ms pre/post-roll padding + dynamic gaps)
6. Audio Preview Render & Quality Control (Audio QC)
7. Premiere FCP7 XML Timeline Export (timeline.xml)
8. Run State & Memory Persistence
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.llm_client import generate_editorial_plan, get_llm_config, load_env_file
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
    llm_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the full autonomous rough cut pipeline."""
    load_env_file()
    raw_dir = raw_dir.resolve()
    if edit_dir is None:
        edit_dir = raw_dir / "edit"
    edit_dir = edit_dir.resolve()

    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "="*70)
    print("🎬 ALANO ROUGH CUT AI — MOTOR AUTÔNOMO v0.5.0")
    print("="*70)
    print(f"📁 Diretório de Vídeos:   {raw_dir}")
    print(f"📁 Diretório de Edição:   {edit_dir}")
    print(f"🎯 Tipo de Conteúdo:      {video_type}")
    print(f"📝 Briefing:              {brief or '(padrão: melhor take por beat)'}")
    print("="*70 + "\n")

    # -------------------------------------------------------------
    # Step 01: Inventory
    # -------------------------------------------------------------
    print("[1/8] 🔍 Inspecionando inventário de mídia...")
    inventory = scan_inventory(raw_dir)
    print(f"  -> Encontrados {len(inventory)} arquivos de vídeo brutos:")
    for item in inventory:
        dur = item["meta"]["duration"]
        fps = item["meta"]["fps"]
        print(f"     • {item['filename']} ({dur:.1f}s, {fps} fps)")

    primary_fps = inventory[0]["meta"]["fps"]
    sources_map = {item["source_id"]: item["path"] for item in inventory}

    # -------------------------------------------------------------
    # Step 02: 4-in-1 GPU Transcription (Whisper + Wav2Vec2 + Pyannote)
    # -------------------------------------------------------------
    print("\n[2/8] 🎙️ Verificando e executando transcrições com alinhamento fonético...")
    transcribe_needed = force_transcribe
    for item in inventory:
        t_file = transcripts_dir / f"{item['source_id']}.json"
        if not t_file.exists():
            transcribe_needed = True
            break

    if transcribe_needed:
        print("  -> Executando transcrição em lote (DeepFilterNet 3 + Whisper + Wav2Vec2 + Pyannote)...")
        batch_script = PROJECT_ROOT / "helpers" / "transcribe_batch.py"
        cmd_transcribe = [
            sys.executable, str(batch_script),
            str(raw_dir),
            "--edit-dir", str(edit_dir),
            "--provider", provider,
            "--diarization", "community-1",
            "--language", language,
        ]
        if force_transcribe:
            cmd_transcribe.append("--force")

        subprocess.check_call(cmd_transcribe)
        print("  -> Transcrição concluída com sucesso.")
    else:
        print("  -> Transcrições existentes reutilizadas.")

    # -------------------------------------------------------------
    # Step 03: Pack Transcripts (takes_packed.md)
    # -------------------------------------------------------------
    print("\n[3/8] 📦 Agrupando transcrições em frases e pausas (takes_packed.md)...")
    pack_script = PROJECT_ROOT / "helpers" / "pack_transcripts.py"
    cmd_pack = [
        sys.executable, str(pack_script),
        "--edit-dir", str(edit_dir),
    ]
    subprocess.check_call(cmd_pack)
    takes_packed_file = edit_dir / "takes_packed.md"
    if not takes_packed_file.exists():
        raise FileNotFoundError(f"takes_packed.md was not generated at {takes_packed_file}")
    takes_packed_content = takes_packed_file.read_text(encoding="utf-8")
    print(f"  -> takes_packed.md gerado com sucesso ({len(takes_packed_content)} bytes).")

    # -------------------------------------------------------------
    # Step 04 & 05 & 06: Cognitive LLM Editorial Decision
    # -------------------------------------------------------------
    print("\n[4/8] 🧠 Consultando Inteligência Editorial (OpenAI-Compatible / NVIDIA NIM)...")
    if not brief.strip():
        brief = f"Corte bruto para {video_type}. Selecione os melhores takes, remova falsos inícios, hesitações e falas de direção."

    cut_plan = generate_editorial_plan(
        brief=brief,
        takes_packed_content=takes_packed_content,
        video_type=video_type,
        config=llm_config,
    )
    print(f"  -> A LLM selecionou {len(cut_plan)} takes principais:")
    for i, cut in enumerate(cut_plan, 1):
        print(f"     [{i:02d}] {cut['source']} ({cut['start']:.2f}s - {cut['end']:.2f}s) | Beat: {cut['beat']} | List: {cut['is_list']}")

    # Formulate base EDL JSON
    if not timeline_name:
        sanitized_slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in video_type).lower()
        timeline_name = f"{sanitized_slug}_rough_cut_alano"

    edl_path = edit_dir / "edl.json"
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
    print(f"  -> edl.json inicial gravada em {edl_path}.")

    # -------------------------------------------------------------
    # Step 07: Acoustic Boundary Refinement & Snapper
    # -------------------------------------------------------------
    print("\n[5/8] 🎛️ Refinando bordas acústicas, VAD e aplicando padding de respiração...")
    refine_script = PROJECT_ROOT / "helpers" / "refine_edl_boundaries.py"
    refine_report_path = edit_dir / "refine_report.json"
    cmd_refine = [
        sys.executable, str(refine_script),
        str(edl_path),
        "--transcripts", str(transcripts_dir),
        "--report", str(refine_report_path),
    ]
    res_refine = subprocess.run(cmd_refine)
    if res_refine.returncode not in {0, 2}:
        raise RuntimeError(f"refine_edl_boundaries failed with exit code {res_refine.returncode}")
    print(f"  -> Bordas acústicas refinadas e salvas em {edl_path}.")

    # -------------------------------------------------------------
    # Step 08: Audio Render & Quality Control (Audio QC)
    # -------------------------------------------------------------
    print("\n[6/8] 🔊 Renderizando áudio do preview e validando Controle de Qualidade (QC)...")
    render_script = PROJECT_ROOT / "helpers" / "render.py"
    preview_wav_path = edit_dir / "preview.wav"
    timeline_map_path = edit_dir / "preview_timeline.json"
    cmd_render = [
        sys.executable, str(render_script),
        str(edl_path),
        "-o", str(preview_wav_path),
        "--timeline-map", str(timeline_map_path),
    ]
    subprocess.check_call(cmd_render)

    audio_qc_script = PROJECT_ROOT / "helpers" / "preview_audio_qc.py"
    qc_report_path = edit_dir / "preview_audio_qc.json"
    cmd_qc = [
        sys.executable, str(audio_qc_script),
        str(preview_wav_path),
        "--edl", str(edl_path),
        "--timeline-map", str(timeline_map_path),
        "-o", str(qc_report_path),
    ]
    res_qc = subprocess.run(cmd_qc)
    try:
        qc_data = json.loads(qc_report_path.read_text(encoding="utf-8"))
        print(f"  -> Audio QC Status: {qc_data.get('status', 'pass').upper()}")
    except Exception:
        print(f"  -> Audio QC concluído (exit code: {res_qc.returncode}).")

    # -------------------------------------------------------------
    # Step 09: Final FCP7 XML Export
    # -------------------------------------------------------------
    print("\n[7/8] 🎞️ Exportando timeline Final Cut Pro 7 XML para Premiere Pro...")
    xml_script = PROJECT_ROOT / "helpers" / "edl_to_fcpxml.py"
    timeline_xml_path = edit_dir / "timeline.xml"
    cmd_xml = [
        sys.executable, str(xml_script),
        str(edl_path),
        "-o", str(timeline_xml_path),
    ]
    subprocess.check_call(cmd_xml)
    print(f"  -> XML Final gerado com sucesso: {timeline_xml_path}")

    # -------------------------------------------------------------
    # Step 10: Persist Run State & Memory
    # -------------------------------------------------------------
    print("\n[8/8] 💾 Salvando estado de execução e memória do projeto...")
    run_state_path = edit_dir / "run_state.md"
    project_memory_path = edit_dir / "project.md"

    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_state_content = f"""# Run State: {timeline_name}

- **Data de Execução**: {now_iso}
- **Modo**: Modo 1 (Orquestrador Autônomo v0.5.0)
- **Tipo de Conteúdo**: {video_type}
- **Briefing**: {brief}
- **Fontes Analisadas**: {len(inventory)} arquivos
- **Takes Selecionados pela LLM**: {len(cut_plan)}
- **Arquivo XML Entregável**: `{timeline_xml_path}`
- **Preview de Áudio**: `{preview_wav_path}`
"""
    run_state_path.write_text(run_state_content, encoding="utf-8")

    project_memory = f"""# Project Memory: {timeline_name}

- **Última Atualização**: {now_iso}
- **Vídeo Final**: {timeline_name} ({video_type})
- **Entregável**: `{timeline_xml_path}`
"""
    project_memory_path.write_text(project_memory, encoding="utf-8")

    print("\n" + "="*70)
    print("🎉 CORTE BRUTO CONCLUÍDO COM SUCESSO!")
    print(f"📄 Timeline XML: {timeline_xml_path}")
    print("="*70 + "\n")

    return {
        "status": "success",
        "timeline_xml": str(timeline_xml_path),
        "preview_wav": str(preview_wav_path),
        "edl_json": str(edl_path),
        "takes_count": len(cut_plan),
        "timeline_name": timeline_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Alano Rough Cut AI Autonomous Orchestrator (Mode 1)")
    parser.add_argument("raw_dir", nargs="?", default="raw_video", help="Directory containing raw video files")
    parser.add_argument("--edit-dir", default=None, help="Directory to output edit files (default: <raw_dir>/edit)")
    parser.add_argument("--brief", "-b", default="", help="User editorial brief / instructions")
    parser.add_argument("--video-type", "-t", default="aula", help="Content type (aula, reels, tiktok, tutorial, etc.)")
    parser.add_argument("--provider", default="whisper-vulkan", help="ASR Provider (whisper-vulkan, elevenlabs, assemblyai)")
    parser.add_argument("--language", "-l", default="pt", help="Language code (pt, en, etc.)")
    parser.add_argument("--force-transcribe", action="store_true", help="Force re-transcription of all audio")
    parser.add_argument("--timeline-name", default=None, help="Custom name for the output sequence")

    args = parser.parse_args()
    raw_path = Path(args.raw_dir)
    edit_path = Path(args.edit_dir) if args.edit_dir else None

    try:
        run_autonomous_rough_cut(
            raw_dir=raw_path,
            edit_dir=edit_path,
            brief=args.brief,
            video_type=args.video_type,
            provider=args.provider,
            language=args.language,
            force_transcribe=args.force_transcribe,
            timeline_name=args.timeline_name,
        )
    except Exception as e:
        print(f"\n❌ Erro na execução do orquestrador: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
