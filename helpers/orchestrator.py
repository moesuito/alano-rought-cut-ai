"""Fail-closed orchestration for the public, argument-free AlanoCut flow."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.agent_artifacts import ArtifactStore, EvidenceCatalog
from helpers.agent_telemetry import JsonlUsageWriter, RunLedger
from helpers.artifact_agent import ArtifactDrivenEditor, StructuredCompletionProvider
from helpers.editorial_edl_bridge import EditorialEdlBridge
from helpers.knowledge_loader import load_artifact_schema_catalog
from helpers.llm_client import get_llm_config, load_env_file, send_chat_completion_structured
from helpers.session_manager import SessionContext, create_new_session, export_deliverable
from helpers.timing import format_fps_fraction, parse_fps_fraction

SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4", ".mkv", ".m4v", ".webm", ".avi"}
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t]*")


class PipelineFailure(RuntimeError):
    """Content-free pipeline error that is safe to show in the TUI."""

    def __init__(self, code: str, stage: str) -> None:
        super().__init__(f"{code}: stage {stage} failed")
        self.code = code
        self.stage = stage


def probe_video_file(file_path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=r_frame_rate,width,height,duration:format=duration", "-of", "json",
        str(file_path),
    ]
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL)
        data = json.loads(output)
        streams = data.get("streams", [])
        if not streams:
            raise ValueError("missing video stream")
        stream = streams[0]
        fps = format_fps_fraction(parse_fps_fraction(stream.get("r_frame_rate", "")))
        duration = float(stream.get("duration") or data.get("format", {}).get("duration", "0"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid media duration")
        return {
            "fps": fps,
            "width": int(stream.get("width", 0)),
            "height": int(stream.get("height", 0)),
            "duration": duration,
        }
    except Exception:
        return {
            "fps": "0/1", "width": 0, "height": 0, "duration": 0.0,
            "probe_error": "MEDIA_PROBE_FAILED",
        }


def scan_inventory(raw_dir: Path) -> list[dict[str, Any]]:
    """Freeze regular files directly under the operated folder using opaque IDs."""
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise FileNotFoundError("Raw video directory is unavailable")
    _reject_reparse(raw_dir, "Raw video directory")
    paths: list[Path] = []
    for candidate in sorted(raw_dir.iterdir(), key=lambda item: item.name.casefold()):
        if candidate.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            continue
        _reject_reparse(candidate, "Media source")
        if not candidate.is_file():
            raise ValueError("A media source is not a regular file")
        paths.append(candidate)
    if not paths:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise ValueError(f"No video files ({supported}) found in the operated directory")

    stems: set[str] = set()
    source_ids: set[str] = set()
    inventory: list[dict[str, Any]] = []
    for path in paths:
        stem_key = path.stem.casefold()
        if stem_key in stems:
            raise ValueError("Duplicate media stems are not allowed")
        stems.add(stem_key)
        source_id = _logical_source_id(path.name)
        if source_id in source_ids:
            raise ValueError("Logical source ID collision")
        source_ids.add(source_id)
        inventory.append(
            {
                "source_id": source_id,
                "original_source_id": path.stem,
                "filename": path.name,
                "path": str(path.absolute()),
                "meta": probe_video_file(path),
                "file_identity": _regular_identity(path),
            }
        )
    return inventory


def run_autonomous_rough_cut(
    raw_dir: Path,
    edit_dir: Path | None = None,
    brief: str = "",
    video_type: str = "videoaula",
    provider: str = "whisper-vulkan",
    language: str = "pt",
    force_transcribe: bool = False,
    timeline_name: str | None = None,
    output_xml_filename: str = "timeline.xml",
    mode: str = "agentic",
    llm_config: dict[str, str] | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Run the artifact editor and publish only after every deterministic gate."""
    if mode != "agentic":
        raise ValueError("The public runtime supports only the artifact-driven editor")
    if output_xml_filename != "timeline.xml":
        raise ValueError("The public deliverable name is fixed to timeline.xml")
    load_env_file()
    raw_dir = Path(raw_dir)
    _reject_reparse(raw_dir, "Raw video directory")
    raw_dir = raw_dir.resolve(strict=True)
    session = _create_session(raw_dir, edit_dir, video_type, brief)

    def progress(step: str, detail: str) -> None:
        if progress_callback is not None:
            progress_callback(step, detail)

    try:
        progress("1/8", "Congelando inventário de mídia...")
        inventory = scan_inventory(raw_dir)
        _require_valid_media_metadata(inventory)
        session.source_files = inventory
        session.status = "running"
        session.log(f"pipeline_started source_count={len(inventory)}")
        session.save()
        transcripts_dir = session.transcripts_dir
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        environment = _subprocess_environment()

        progress("2/8", "Executando transcrição local e alinhamento...")
        if force_transcribe or _transcription_required(inventory, transcripts_dir):
            _run_checked(
                [
                    sys.executable, str(PROJECT_ROOT / "helpers" / "transcribe_batch.py"),
                    str(raw_dir), "--edit-dir", str(session.edit_dir), "--provider", provider,
                    "--diarization", "community-1", "--language", language, "--force",
                ],
                environment,
                "transcription",
            )
        _assert_inventory_unchanged(inventory)
        _assert_exact_transcript_set(inventory, transcripts_dir)

        progress("3/8", "Preparando evidência editorial imutável...")
        _run_checked(
            [sys.executable, str(PROJECT_ROOT / "helpers" / "pack_transcripts.py"), "--edit-dir", str(session.edit_dir)],
            environment,
            "transcript_pack",
        )
        if not session.takes_packed_file.is_file():
            raise PipelineFailure("PACK_OUTPUT_MISSING", "transcript_pack")
        resolved_name = timeline_name or f"{_slug(video_type)}_rough_cut_alano"
        prepared = _prepare_agent_inputs(
            session=session,
            inventory=inventory,
            brief=_editorial_brief(brief, video_type),
            timeline_name=resolved_name,
        )

        progress("4/8", "Agente: diagnóstico, plano, montagem e crítica...")
        config = dict(llm_config) if llm_config is not None else get_llm_config()
        catalog = load_artifact_schema_catalog()
        store = ArtifactStore(
            session.agent_artifacts_dir,
            run_id=session.session_id,
            schemas=catalog,
            evidence=EvidenceCatalog.from_transcripts(prepared["template"], prepared["transcripts"]),
            edl_template=prepared["template"],
        )
        result = ArtifactDrivenEditor(
            run_id=session.session_id,
            model=config["model"],
            provider=StructuredCompletionProvider(
                send_chat_completion_structured, config=config, timeout_seconds=120
            ),
            store=store,
            input_root=session.agent_inputs_dir,
            input_files=prepared["input_files"],
            telemetry=JsonlUsageWriter(session.llm_usage_file),
            ledger=RunLedger(session.agent_run_file, run_id=session.session_id, model=config["model"]),
        ).run(content_type_hint=video_type, brief=prepared["brief"])
        session.agent_state = result.state
        session.last_error_code = result.error_code or ""
        _log_agent_summary(session, store, result.state, result.error_code)
        if result.state == "failed":
            raise PipelineFailure(result.error_code or "AGENT_FAILED", "agent")
        if result.state != "approved" or result.final_edl is None:
            return _finish_for_review(session, result.error_code or "AGENT_NOT_APPROVED")

        _assert_inventory_unchanged(inventory)
        _, edl_path = EditorialEdlBridge(
            store=store,
            edit_root=session.edit_dir,
            source_paths={item["source_id"]: item["path"] for item in inventory},
        ).publish_and_project()

        progress("5/8", "Refinando boundaries com evidência acústica...")
        boundary_report = session.edit_dir / "edl_boundary_qc.json"
        refine = _run_process(
            [
                sys.executable, str(PROJECT_ROOT / "helpers" / "refine_edl_boundaries.py"),
                str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(boundary_report),
            ],
            environment,
        )
        if refine.returncode == 2:
            return _finish_for_review(session, "BOUNDARY_REVIEW_REQUIRED")
        if refine.returncode != 0:
            raise PipelineFailure("BOUNDARY_REFINEMENT_FAILED", "boundary_refinement")

        progress("6/8", "Renderizando preview e executando QCs...")
        preview_wav = session.edit_dir / "preview.wav"
        timeline_map = session.edit_dir / "preview_timeline.json"
        audio_report = session.edit_dir / "preview_audio_qc.json"
        semantic_report = session.edit_dir / "edl_semantic_qc.json"
        transcript_report = session.edit_dir / "preview_transcript_qc.json"
        preview_transcript = transcripts_dir / "preview.json"
        commands = [
            ([sys.executable, str(PROJECT_ROOT / "helpers" / "render.py"), str(edl_path), "-o", str(preview_wav), "--timeline-map", str(timeline_map)], "preview_render"),
            ([sys.executable, str(PROJECT_ROOT / "helpers" / "preview_audio_qc.py"), str(preview_wav), "--edl", str(edl_path), "--timeline-map", str(timeline_map), "-o", str(audio_report)], "audio_qc"),
            ([sys.executable, str(PROJECT_ROOT / "helpers" / "semantic_qc.py"), str(edl_path), "--transcripts", str(transcripts_dir), "-o", str(semantic_report)], "semantic_qc"),
            ([sys.executable, str(PROJECT_ROOT / "helpers" / "preview_transcript_qc.py"), str(preview_wav), "--edl", str(edl_path), "--transcripts", str(transcripts_dir), "--timeline-map", str(timeline_map), "--transcript-output", str(preview_transcript), "--provider", provider, "-o", str(transcript_report)], "preview_transcript_qc"),
        ]
        for command, stage in commands:
            stage_result = _run_process(command, environment)
            if stage == "preview_transcript_qc" and stage_result.returncode == 2:
                return _finish_for_review(session, "TRANSCRIPT_QC_REVIEW_REQUIRED")
            if stage_result.returncode != 0:
                raise PipelineFailure("STAGE_FAILED", stage)

        progress("7/8", "Validando readiness de todos os relatórios...")
        readiness = _run_process(
            _readiness_command(edl_path, transcripts_dir, boundary_report, audio_report, semantic_report, transcript_report, preview_wav, timeline_map),
            environment,
        )
        if readiness.returncode == 2:
            return _finish_for_review(session, "QC_REVIEW_REQUIRED")
        if readiness.returncode != 0:
            raise PipelineFailure("READINESS_FAILED", "readiness")
        session.qc_status = "pass"

        progress("8/8", "Exportando timeline.xml pronta para o Premiere...")
        _run_checked(
            [sys.executable, str(PROJECT_ROOT / "helpers" / "edl_to_fcpxml.py"), str(edl_path), "-o", str(session.session_xml_file)],
            environment,
            "xml_export",
        )
        final_xml = export_deliverable(session, raw_dir, output_filename="timeline.xml")
        ranges = json.loads(edl_path.read_text(encoding="utf-8"))["ranges"]
        total_duration = sum(float(item["end"]) - float(item["start"]) for item in ranges)
        _write_safe_audit(session, inventory, ranges, total_duration)
        session.cuts_count = len(ranges)
        session.total_duration_s = total_duration
        session.timeline_xml_path = str(final_xml)
        session.status = "completed"
        session.last_error_code = ""
        session.log(f"pipeline_completed cuts={len(ranges)} duration_ms={round(total_duration * 1000)} qc=pass")
        session.save()
        return {
            "status": "success", "timeline_xml": str(final_xml),
            "session_dir": str(session.dir), "session_log": str(session.session_log_file),
            "audit_txt": str(session.audit_txt_file), "preview_wav": str(preview_wav),
            "takes_count": session.cuts_count, "total_duration_s": session.total_duration_s,
            "timeline_name": resolved_name, "mode": "artifact-driven",
            "qc_status": session.qc_status, "agent_state": session.agent_state,
        }
    except PipelineFailure as exc:
        session.status = "failed"
        session.last_error_code = exc.code
        session.log(f"pipeline_failed stage={exc.stage} code={exc.code}")
        session.save()
        raise
    except Exception as exc:
        session.status = "failed"
        session.last_error_code = "PIPELINE_FAILED"
        session.log("pipeline_failed stage=host code=PIPELINE_FAILED")
        session.save()
        raise PipelineFailure("PIPELINE_FAILED", "host") from exc


def _create_session(raw_dir: Path, edit_dir: Path | None, video_type: str, brief: str) -> SessionContext:
    if edit_dir is None:
        return create_new_session(raw_dir, video_type=video_type, brief=brief)
    active_edit_dir = Path(edit_dir).resolve()
    active_edit_dir.mkdir(parents=True, exist_ok=True)
    _reject_reparse(active_edit_dir, "Edit directory")
    session = SessionContext(
        session_id=f"custom_{hashlib.sha256(str(active_edit_dir).encode()).hexdigest()[:16]}",
        created_at=datetime.datetime.now().isoformat(), working_dir=str(raw_dir),
        session_dir=str(active_edit_dir.parent), video_type=video_type, brief=brief,
    )
    session.save()
    return session


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(PROJECT_ROOT), str(PROJECT_ROOT / "helpers"), environment.get("PYTHONPATH", "")))
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _run_process(command: Sequence[str], environment: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), env=dict(environment), capture_output=True, text=True)


def _run_checked(command: Sequence[str], environment: Mapping[str, str], stage: str) -> None:
    if _run_process(command, environment).returncode != 0:
        raise PipelineFailure("STAGE_FAILED", stage)


def _transcription_required(inventory: Sequence[Mapping[str, Any]], transcripts_dir: Path) -> bool:
    for item in inventory:
        transcript = transcripts_dir / f"{item['original_source_id']}.json"
        if not transcript.is_file():
            return True
        try:
            words = json.loads(transcript.read_text(encoding="utf-8")).get("words")
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if not isinstance(words, list) or not words:
            return True
        if not any(isinstance(word, dict) and word.get("timing_source") in {"forced_alignment", "provider_word_timestamp"} for word in words):
            return True
    return False


def _assert_exact_transcript_set(
    inventory: Sequence[Mapping[str, Any]], transcripts_dir: Path
) -> None:
    expected = {f"{item['original_source_id']}.json" for item in inventory}
    actual = {
        path.name
        for path in transcripts_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".json"
    }
    if actual != expected:
        raise PipelineFailure("TRANSCRIPT_SET_MISMATCH", "transcription")
    for filename in expected:
        _reject_reparse(transcripts_dir / filename, "Canonical transcript")


def _prepare_agent_inputs(
    *, session: SessionContext, inventory: Sequence[Mapping[str, Any]], brief: str,
    timeline_name: str,
) -> dict[str, Any]:
    inputs = session.agent_inputs_dir
    transcript_inputs = inputs / "transcripts"
    transcript_inputs.mkdir(parents=True, exist_ok=True)
    for path in (session.agent_dir, inputs, transcript_inputs):
        _reject_reparse(path, "Agent session directory")
    marker_map: dict[str, str] = {}
    sources: dict[str, str] = {}
    transcripts: dict[str, dict[str, Any]] = {}
    input_files = {"brief.md": "brief.md", "takes_packed.md": "takes_packed.md", "edl_template.json": "edl_template.json"}
    registry: list[dict[str, Any]] = []
    for item in inventory:
        source_id = str(item["source_id"])
        original_id = str(item["original_source_id"])
        marker_map.update({str(item["path"]): source_id, str(item["filename"]): source_id, original_id: source_id})
        sources[source_id] = f"source:{source_id}"
        source_transcript = session.transcripts_dir / f"{original_id}.json"
        _reject_reparse(source_transcript, "Canonical transcript")
        payload = json.loads(source_transcript.read_text(encoding="utf-8"))
        _rewrite_transcript_source_fields(payload, item, source_id)
        _assert_no_physical_markers(payload, item)
        transcripts[source_id] = payload
        transcript_bytes = _strict_json_bytes(payload)
        logical_name = f"transcripts/{source_id}.json"
        _write_new_file(inputs / logical_name, transcript_bytes)
        _write_new_file(session.transcripts_dir / f"{source_id}.json", transcript_bytes)
        input_files[logical_name] = logical_name
        registry.append({"source_id": source_id, "filename": item["filename"], "path": item["path"], "file_identity": item["file_identity"]})

    safe_brief = _replace_physical_markers(brief, marker_map)
    safe_takes = _rewrite_packed_sources(session.takes_packed_file.read_text(encoding="utf-8"), marker_map)
    _assert_text_has_no_source_markers(safe_brief, inventory)
    _assert_text_has_no_source_markers(safe_takes, inventory)
    template = {
        "version": 1,
        "metadata": {
            "timeline_name": timeline_name, "video_type": session.video_type,
            "content_number": None, "content_slug": _slug(session.video_type),
            "sequence_fps": _single_sequence_fps(inventory),
        },
        "sources": sources,
    }
    _write_new_file(inputs / "brief.md", safe_brief.encode("utf-8"))
    _write_new_file(inputs / "takes_packed.md", safe_takes.encode("utf-8"))
    _write_new_file(inputs / "edl_template.json", _strict_json_bytes(template))
    _write_new_file(session.source_registry_file, _strict_json_bytes({"version": 1, "run_id": session.session_id, "sources": registry}))
    return {"brief": safe_brief, "template": template, "transcripts": transcripts, "input_files": input_files}


def _rewrite_transcript_source_fields(payload: Any, item: Mapping[str, Any], source_id: str) -> None:
    if not isinstance(payload, dict):
        raise PipelineFailure("TRANSCRIPT_INVALID", "agent_inputs")
    markers = {str(item["path"]), str(item["filename"]), str(item["original_source_id"])}
    for key in ("source", "source_id", "source_name", "filename", "file", "path"):
        value = payload.get(key)
        if isinstance(value, str) and value in markers:
            payload[key] = source_id if key != "path" else f"source:{source_id}"


def _rewrite_packed_sources(content: str, marker_map: Mapping[str, str]) -> str:
    result = content
    for original, logical in sorted(marker_map.items(), key=lambda item: len(item[0]), reverse=True):
        if "/" in original or "\\" in original or "." in original:
            continue
        result = re.sub(rf"(?m)^(##\s+){re.escape(original)}(?=\s|$)", rf"\1{logical}", result)
    return result


def _replace_physical_markers(content: str, marker_map: Mapping[str, str]) -> str:
    result = content
    for marker, logical in sorted(marker_map.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(marker, logical)
    return _PATH_RE.sub("[local-path]", result)


def _assert_no_physical_markers(payload: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    if any(str(marker) in rendered for marker in (item["path"], item["filename"], item["original_source_id"])):
        raise PipelineFailure("TRANSCRIPT_PRIVACY_CONFLICT", "agent_inputs")


def _assert_text_has_no_source_markers(content: str, inventory: Sequence[Mapping[str, Any]]) -> None:
    for item in inventory:
        if any(str(marker) in content for marker in (item["path"], item["filename"], item["original_source_id"])):
            raise PipelineFailure("INPUT_PRIVACY_CONFLICT", "agent_inputs")


def _log_agent_summary(session: SessionContext, store: ArtifactStore, state: str, error_code: str | None) -> None:
    summary: dict[str, Any] = {"state": state, "error_code": error_code}
    for name in ("diagnosis.json", "cut_plan.json", "edl.json"):
        try:
            record = store.read(name)
        except Exception:
            continue
        summary[f"{name}_revision"] = record.revision
        summary[f"{name}_sha256"] = record.sha256
        if name == "diagnosis.json":
            summary["selected_archetype"] = record.content.get("selected_archetype")
        elif name == "cut_plan.json":
            summary["beat_count"] = len(record.content.get("beats", []))
        else:
            summary["range_count"] = len(record.content.get("ranges", []))
    session.log("agent_summary " + json.dumps(summary, sort_keys=True, separators=(",", ":")))


def _finish_for_review(session: SessionContext, error_code: str) -> dict[str, Any]:
    session.status = "needs_human_review"
    session.last_error_code = error_code
    qc_review_codes = {
        "BOUNDARY_REVIEW_REQUIRED",
        "QC_REVIEW_REQUIRED",
        "READINESS_FAILED",
        "TRANSCRIPT_QC_REVIEW_REQUIRED",
    }
    session.qc_status = "review" if error_code in qc_review_codes else "pending"
    session.log(f"pipeline_paused state=needs_human_review code={error_code}")
    session.save()
    return {
        "status": "needs_human_review", "error_code": error_code, "timeline_xml": None,
        "session_dir": str(session.dir), "session_log": str(session.session_log_file),
        "agent_state": session.agent_state, "qc_status": session.qc_status,
    }


def _readiness_command(edl: Path, transcripts: Path, boundary: Path, audio_report: Path, semantic_report: Path, transcript_report: Path, preview_wav: Path, timeline_map: Path) -> list[str]:
    return [
        sys.executable, str(PROJECT_ROOT / "helpers" / "verify_edit_ready.py"), str(edl),
        "--transcripts", str(transcripts), "--boundary-report", str(boundary),
        "--audio-report", str(audio_report), "--semantic-report", str(semantic_report),
        "--transcript-report", str(transcript_report), "--audio", str(preview_wav),
        "--timeline-map", str(timeline_map),
    ]


def _write_safe_audit(session: SessionContext, inventory: Sequence[Mapping[str, Any]], ranges: Sequence[Mapping[str, Any]], total_duration: float) -> None:
    lines = [
        "ALANO ROUGH CUT AI - EDITORIAL AUDIT", f"session_id={session.session_id}",
        "agent_state=approved", "qc_status=pass", f"source_count={len(inventory)}",
        f"cut_count={len(ranges)}", f"duration_ms={round(total_duration * 1000)}",
    ]
    for index, item in enumerate(ranges, start=1):
        lines.append(f"range_{index:03d} source={item.get('source')} start={float(item.get('start', 0)):.3f} end={float(item.get('end', 0)):.3f}")
    session.audit_txt_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _require_valid_media_metadata(inventory: Sequence[Mapping[str, Any]]) -> None:
    for item in inventory:
        meta = item["meta"]
        if meta.get("probe_error") or not _finite_number(meta.get("duration")) or float(meta["duration"]) <= 0:
            raise PipelineFailure("MEDIA_PROBE_FAILED", "inventory")
    _single_sequence_fps(inventory)


def _single_sequence_fps(inventory: Sequence[Mapping[str, Any]]) -> str:
    rates = {format_fps_fraction(parse_fps_fraction(item["meta"]["fps"])) for item in inventory}
    if len(rates) != 1:
        raise PipelineFailure("MIXED_FRAME_RATES", "inventory")
    return next(iter(rates))


def _assert_inventory_unchanged(inventory: Sequence[Mapping[str, Any]]) -> None:
    for item in inventory:
        if _regular_identity(Path(str(item["path"]))) != item["file_identity"]:
            raise PipelineFailure("SOURCE_CHANGED", "inventory")


def _logical_source_id(filename: str) -> str:
    return "SRC_" + hashlib.sha256(filename.casefold().encode("utf-8")).hexdigest()[:16].upper()


def _regular_identity(path: Path) -> dict[str, int]:
    _reject_reparse(path, "Media source")
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Media source is not a regular file")
    return {"device": int(info.st_dev), "inode": int(info.st_ino), "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}


def _reject_reparse(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise FileNotFoundError(f"{label} is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError(f"{label} cannot be a filesystem link")


def _write_new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse(path.parent, "Agent input directory")
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise PipelineFailure("IMMUTABLE_INPUT_CONFLICT", "agent_inputs") from exc


def _strict_json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PipelineFailure("INVALID_JSON", "agent_inputs") from exc


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _editorial_brief(brief: str, video_type: str) -> str:
    return brief.strip() or (
        f"Crie um rough cut autônomo para o formato {video_type}. "
        "Resolva retakes, preserve clareza e remova apenas material sem função editorial."
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "rough_cut"


def main() -> None:
    parser = argparse.ArgumentParser(description="Alano Rough Cut internal orchestrator")
    parser.add_argument("raw_dir", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if run_autonomous_rough_cut(args.raw_dir)["status"] != "success":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
