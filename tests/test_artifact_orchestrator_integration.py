"""Integration wiring tests for the artifact-driven public pipeline."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from helpers import interactive_cli, orchestrator
from helpers.agent_artifacts import ArtifactStore, EvidenceCatalog
from helpers.agent_telemetry import JsonlUsageWriter, RunLedger
from helpers.artifact_agent import AgentBudget, ArtifactDrivenEditor
from helpers.knowledge_loader import load_artifact_schema_catalog
from helpers.llm_client import ChatCompletionResult
from helpers.session_manager import SessionContext


def _session(tmp_path: Path, raw_dir: Path) -> SessionContext:
    root = tmp_path / "session"
    context = SessionContext(
        session_id="session_test_artifact_001",
        created_at="2026-08-21T00:00:00",
        working_dir=str(raw_dir),
        session_dir=str(root),
        video_type="videoaula",
        brief="",
    )
    context.transcripts_dir.mkdir(parents=True)
    context.save()
    return context


def _canonical_transcript() -> dict[str, Any]:
    return {
        "text": "Olá mundo",
        "words": [
            {"type": "word", "text": "Olá", "start": 0.0, "end": 0.4, "timing_source": "provider_word_timestamp"},
            {"type": "word", "text": "mundo", "start": 0.5, "end": 1.0, "timing_source": "provider_word_timestamp"},
        ],
    }


def _prepare_session_media(tmp_path: Path) -> tuple[Path, SessionContext, Path]:
    raw_dir = tmp_path / "private-project"
    raw_dir.mkdir()
    media = raw_dir / "camera_secret.mov"
    media.write_bytes(b"media")
    session = _session(tmp_path, raw_dir)
    (session.transcripts_dir / "camera_secret.json").write_text(
        json.dumps(_canonical_transcript()), encoding="utf-8"
    )
    return raw_dir, session, media


def test_transcript_set_must_match_frozen_inventory(tmp_path: Path) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)
    inventory = orchestrator.scan_inventory(raw_dir)

    orchestrator._assert_exact_transcript_set(inventory, session.transcripts_dir)

    (session.transcripts_dir / "unexpected_audio.json").write_text(
        json.dumps(_canonical_transcript()), encoding="utf-8"
    )
    with pytest.raises(orchestrator.PipelineFailure) as caught:
        orchestrator._assert_exact_transcript_set(inventory, session.transcripts_dir)
    assert caught.value.code == "TRANSCRIPT_SET_MISMATCH"


class _FakeStore:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.root = Path("artifacts")

    def read(self, name: str) -> SimpleNamespace:
        if name == "diagnosis.json":
            content = {"selected_archetype": "educational_explainer"}
        elif name == "cut_plan.json":
            content = {"beats": [{"id": "INTRO"}]}
        else:
            content = {"ranges": [{"source": "SRC_TEST"}]}
        return SimpleNamespace(revision=1, sha256="a" * 64, content=content)


class _ApprovedEditor:
    def __init__(self, **_: Any) -> None:
        pass

    def run(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(state="approved", final_edl=object(), error_code=None)


class _ReviewEditor:
    error_code = "NO_PROGRESS"

    def __init__(self, **_: Any) -> None:
        pass

    def run(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(
            state="needs_human_review", final_edl=None, error_code=self.error_code
        )


class _FailedEditor(_ReviewEditor):
    def run(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(state="failed", final_edl=None, error_code=self.error_code)


def _wire_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    session: SessionContext,
    *,
    editor: type[_ApprovedEditor] | type[_ReviewEditor] = _ApprovedEditor,
    refine_returncode: int = 0,
    readiness_returncode: int = 0,
    transcript_qc_returncode: int = 0,
) -> dict[str, Any]:
    captured: dict[str, Any] = {"process_calls": 0}
    monkeypatch.setattr(orchestrator, "create_new_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(
        orchestrator,
        "probe_video_file",
        lambda _path: {"fps": "30/1", "width": 1920, "height": 1080, "duration": 2.0},
    )
    monkeypatch.setattr(orchestrator, "ArtifactStore", _FakeStore)
    monkeypatch.setattr(orchestrator, "ArtifactDrivenEditor", editor)
    monkeypatch.setattr(orchestrator, "load_artifact_schema_catalog", lambda: object())
    monkeypatch.setattr(orchestrator, "JsonlUsageWriter", lambda path: object())
    monkeypatch.setattr(orchestrator, "RunLedger", lambda *args, **kwargs: object())

    def fake_checked(command: list[str], _environment: dict[str, str], stage: str) -> None:
        if stage == "transcript_pack":
            session.takes_packed_file.write_text(
                "# Packed transcripts\n\n## camera_secret  (duration: 2.0s)\n"
                "  [000.00-001.00] S0 Olá mundo\n",
                encoding="utf-8",
            )
        elif stage == "xml_export":
            session.session_xml_file.write_text("<xmeml/>", encoding="utf-8")

    monkeypatch.setattr(orchestrator, "_run_checked", fake_checked)

    def fake_process(command: list[str], *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        captured["process_calls"] += 1
        script_name = Path(command[1]).name
        if script_name == "refine_edl_boundaries.py":
            code = refine_returncode
        elif script_name == "preview_transcript_qc.py":
            code = transcript_qc_returncode
        elif script_name == "verify_edit_ready.py":
            code = readiness_returncode
        else:
            code = 0
        return SimpleNamespace(returncode=code, stdout="PRIVATE", stderr="PRIVATE")

    monkeypatch.setattr(orchestrator, "_run_process", fake_process)

    class FakeBridge:
        def __init__(self, *, source_paths: dict[str, str], **_: Any) -> None:
            captured["source_paths"] = source_paths

        def publish_and_project(self) -> tuple[object, Path]:
            edl = {
                "sources": captured["source_paths"],
                "ranges": [
                    {
                        "source": next(iter(captured["source_paths"])),
                        "start": 0.0,
                        "end": 1.0,
                    }
                ],
            }
            session.edl_file.write_text(json.dumps(edl), encoding="utf-8")
            return object(), session.edl_file

    monkeypatch.setattr(orchestrator, "EditorialEdlBridge", FakeBridge)
    return captured


def test_agent_inputs_and_messages_have_only_logical_source_ids(tmp_path: Path) -> None:
    raw_dir, session, media = _prepare_session_media(tmp_path)
    session.takes_packed_file.write_text(
        "# Packed transcripts\n\n## camera_secret  (duration: 2.0s)\n"
        "  [000.00-001.00] S0 Olá mundo\n",
        encoding="utf-8",
    )
    inventory = orchestrator.scan_inventory(raw_dir)
    inventory[0]["meta"] = {"fps": "30/1", "width": 1920, "height": 1080, "duration": 2.0}

    prepared = orchestrator._prepare_agent_inputs(
        session=session,
        inventory=inventory,
        brief=f"Use {media} sem expor camera_secret.mov",
        timeline_name="videoaula_rough_cut",
    )

    all_inputs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in session.agent_inputs_dir.rglob("*")
        if path.is_file()
    )
    source_id = inventory[0]["source_id"]
    assert source_id in all_inputs
    assert str(media) not in all_inputs
    assert "camera_secret.mov" not in all_inputs
    assert "camera_secret" not in all_inputs
    assert prepared["template"]["sources"] == {source_id: f"source:{source_id}"}

    registry = json.loads(session.source_registry_file.read_text(encoding="utf-8"))
    assert registry["sources"][0]["path"] == str(media.absolute())

    captured_messages: list[str] = []

    class CaptureProvider:
        def complete(self, *, messages, **kwargs):
            captured_messages.append(json.dumps(messages, ensure_ascii=False))
            return ChatCompletionResult(
                content=None,
                tool_calls=(),
                usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
                model="fake-model",
                finish_reason="stop",
                request_id="request-1",
                latency_ms=1,
            )

    store = ArtifactStore(
        session.agent_artifacts_dir,
        run_id=session.session_id,
        schemas=load_artifact_schema_catalog(),
        evidence=EvidenceCatalog.from_transcripts(prepared["template"], prepared["transcripts"]),
        edl_template=prepared["template"],
    )
    result = ArtifactDrivenEditor(
        run_id=session.session_id,
        model="fake-model",
        provider=CaptureProvider(),
        store=store,
        input_root=session.agent_inputs_dir,
        input_files=prepared["input_files"],
        telemetry=JsonlUsageWriter(session.llm_usage_file),
        ledger=RunLedger(session.agent_run_file, run_id=session.session_id, model="fake-model"),
        budget=AgentBudget(max_completion_calls_per_phase=1, max_empty_completions=1),
    ).run(content_type_hint="videoaula", brief=prepared["brief"])

    assert result.state == "needs_human_review"
    persisted_runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (session.agent_inputs_dir, session.agent_artifacts_dir)
        for path in root.rglob("*")
        if path.is_file()
    ) + session.llm_usage_file.read_text(encoding="utf-8") + session.agent_run_file.read_text(encoding="utf-8")
    observable = "\n".join(captured_messages) + persisted_runtime
    assert str(media) not in observable
    assert "camera_secret.mov" not in observable
    assert "camera_secret" not in observable


def test_happy_path_rehydrates_sources_only_on_host_and_exports_fixed_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, session, media = _prepare_session_media(tmp_path)
    captured = _wire_pipeline(monkeypatch, session)

    result = orchestrator.run_autonomous_rough_cut(
        raw_dir,
        llm_config={"api_key": "secret-key", "base_url": "https://example.invalid/v1", "model": "fake-model"},
    )

    source_id = next(iter(captured["source_paths"]))
    assert captured["source_paths"] == {source_id: str(media.absolute())}
    assert result["status"] == "success"
    assert Path(result["timeline_xml"]) == raw_dir / "timeline.xml"
    assert (raw_dir / "timeline.xml").read_text(encoding="utf-8") == "<xmeml/>"
    log = session.session_log_file.read_text(encoding="utf-8")
    assert "secret-key" not in log
    assert str(raw_dir) not in log
    assert "PRIVATE" not in log


@pytest.mark.parametrize(
    "error_code", ["COMPLETION_FAILED", "PROVIDER_RETRY_EXHAUSTED", "INVALID_ARGUMENT"]
)
def test_agent_failure_never_projects_or_exports_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_code: str
) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)

    _FailedEditor.error_code = error_code
    _wire_pipeline(monkeypatch, session, editor=_FailedEditor)
    monkeypatch.setattr(
        orchestrator,
        "EditorialEdlBridge",
        lambda **kwargs: pytest.fail("bridge must not run for an unapproved agent"),
    )

    with pytest.raises(orchestrator.PipelineFailure) as exc_info:
        orchestrator.run_autonomous_rough_cut(
            raw_dir,
            llm_config={"api_key": "x", "base_url": "https://example.invalid/v1", "model": "fake-model"},
        )

    assert exc_info.value.code == error_code
    assert session.status == "failed"
    assert session.last_error_code == error_code
    assert not (raw_dir / "timeline.xml").exists()
    assert not session.edl_file.exists()


def test_refiner_review_blocks_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)
    _wire_pipeline(monkeypatch, session, refine_returncode=2)

    result = orchestrator.run_autonomous_rough_cut(
        raw_dir,
        llm_config={"api_key": "x", "base_url": "https://example.invalid/v1", "model": "fake-model"},
    )

    assert result["status"] == "needs_human_review"
    assert result["error_code"] == "BOUNDARY_REVIEW_REQUIRED"
    assert session.edl_file.exists()
    assert not session.session_xml_file.exists()
    assert not (raw_dir / "timeline.xml").exists()


def test_readiness_review_blocks_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)
    _wire_pipeline(monkeypatch, session, readiness_returncode=2)

    result = orchestrator.run_autonomous_rough_cut(
        raw_dir,
        llm_config={"api_key": "x", "base_url": "https://example.invalid/v1", "model": "fake-model"},
    )

    assert result["status"] == "needs_human_review"
    assert result["error_code"] == "QC_REVIEW_REQUIRED"
    assert result["qc_status"] == "review"
    assert not session.session_xml_file.exists()
    assert not (raw_dir / "timeline.xml").exists()


def test_readiness_failure_is_technical_and_blocks_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)
    _wire_pipeline(monkeypatch, session, readiness_returncode=1)

    with pytest.raises(orchestrator.PipelineFailure) as exc_info:
        orchestrator.run_autonomous_rough_cut(
            raw_dir,
            llm_config={"api_key": "x", "base_url": "https://example.invalid/v1", "model": "fake-model"},
        )

    assert exc_info.value.code == "READINESS_FAILED"
    assert session.status == "failed"
    assert session.last_error_code == "READINESS_FAILED"
    assert not session.session_xml_file.exists()
    assert not (raw_dir / "timeline.xml").exists()


def test_transcript_qc_review_blocks_xml_as_human_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir, session, _media = _prepare_session_media(tmp_path)
    _wire_pipeline(monkeypatch, session, transcript_qc_returncode=2)

    result = orchestrator.run_autonomous_rough_cut(
        raw_dir,
        llm_config={"api_key": "x", "base_url": "https://example.invalid/v1", "model": "fake-model"},
    )

    assert result["status"] == "needs_human_review"
    assert result["error_code"] == "TRANSCRIPT_QC_REVIEW_REQUIRED"
    assert result["qc_status"] == "review"
    assert session.edl_file.exists()
    assert not session.session_xml_file.exists()
    assert not (raw_dir / "timeline.xml").exists()


def test_tui_displays_real_review_state_without_fixed_qc_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    output = StringIO()
    monkeypatch.chdir(raw_dir)
    monkeypatch.setattr(interactive_cli, "console", Console(file=output, force_terminal=False))
    monkeypatch.setattr(
        interactive_cli,
        "scan_inventory",
        lambda _path: [{"filename": "clip.mov", "meta": {"duration": 1.0, "width": 1920, "height": 1080, "fps": "30/1"}}],
    )
    choices = iter(("1", ""))
    monkeypatch.setattr(interactive_cli.Prompt, "ask", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(interactive_cli.Confirm, "ask", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        interactive_cli,
        "run_autonomous_rough_cut",
        lambda **kwargs: {
            "status": "needs_human_review", "error_code": "NO_PROGRESS",
            "agent_state": "needs_human_review", "qc_status": "pending",
            "session_dir": "session", "session_log": "session.log",
        },
    )

    with pytest.raises(SystemExit) as exit_info:
        interactive_cli.interactive_main()

    rendered = output.getvalue()
    assert exit_info.value.code == 2
    assert "REVISÃO HUMANA NECESSÁRIA" in rendered
    assert "Nenhum XML foi publicado" in rendered
    assert "APROVADO (Zero clipping" not in rendered
    assert "CONCLUÍDO COM SUCESSO" not in rendered
