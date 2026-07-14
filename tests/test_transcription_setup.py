"""Regression coverage for guided multi-provider transcription setup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from helpers import setup_wizard as wizard
from helpers import transcribe
from helpers import preview_transcript_qc
from helpers.preview_transcript_qc import load_source_transcription_profile
from helpers.transcription_contract import (
    DIARIZATION_NONE,
    ElevenLabsConfig,
    WhisperXConfig,
    convert_elevenlabs_result,
    convert_whisperx_result,
    validate_normative_transcript,
    validate_transcript,
)
from helpers import transcription_models
from helpers.transcription_models import _required_specs
from helpers.transcription_settings import (
    DIARIZATION_COMMUNITY_1,
    PROVIDER_ELEVENLABS,
    TranscriptionSettings,
    read_settings,
    resolve_settings,
    user_settings_path,
    workspace_settings_path,
    write_settings_atomic,
)
from helpers.transcription_providers import whisperx_worker_config
from helpers.whisperx_worker import _validate_config


SOURCE_HASH = "a" * 64


def test_workspace_profile_overrides_user_preference(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    user_profile = TranscriptionSettings.elevenlabs()
    workspace_profile = TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE)
    write_settings_atomic(user_settings_path(), user_profile)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_settings_atomic(workspace_settings_path(workspace), workspace_profile)

    assert resolve_settings(workspace) == workspace_profile
    assert resolve_settings(tmp_path / "outside") == user_profile


def test_elevenlabs_result_is_canonical_and_provider_bound():
    payload = convert_elevenlabs_result(
        {
            "text": "Olá mundo.",
            "language_code": "pt",
            "words": [
                {"type": "word", "text": "Olá", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
                {"type": "word", "text": "mundo.", "start": 0.35, "end": 0.8, "speaker_id": "speaker_0"},
            ],
        },
        config=ElevenLabsConfig(),
        source_sha256=SOURCE_HASH,
    )

    assert payload["_alano_cut"]["transcription_provider"] == "elevenlabs_scribe"
    assert all(word["timing_source"] == "provider_word_timestamp" for word in payload["words"])
    assert all(word["speaker_assignment"] == "provider_word_label" for word in payload["words"])
    validate_normative_transcript(payload)


def test_local_no_diarization_uses_silero_and_empty_speakers():
    config = WhisperXConfig(
        diarization_mode=DIARIZATION_NONE,
        vad_method="silero",
        diarization_model=None,
        diarization_model_revision=None,
    )
    transcript = convert_whisperx_result(
        {
            "language": "pt",
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "text": "Olá",
                    "words": [{"word": "Olá", "start": 0.0, "end": 0.5, "score": 0.9}],
                }
            ],
        },
        [],
        config=config,
        source_sha256=SOURCE_HASH,
    )

    assert transcript["diarization"] == []
    assert transcript["words"][0]["speaker_id"] is None
    assert transcript["words"][0]["speaker_assignment"] == "disabled"
    validate_transcript(transcript)
    worker_config = whisperx_worker_config(config)
    assert _validate_config(worker_config)["vad_method"] == "silero"


def test_programmatic_configured_provider_honors_workspace_profile(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_settings_atomic(
        workspace_settings_path(workspace),
        TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE),
    )
    source = workspace / "speech.wav"
    source.write_bytes(b"synthetic source")
    captured: list[WhisperXConfig] = []

    class StubProvider:
        def __init__(self, config, **_kwargs):
            captured.append(config)

        def transcribe(self, _source):
            return convert_whisperx_result(
                {
                    "language": "pt",
                    "segments": [{
                        "start": 0.0,
                        "end": 0.5,
                        "text": "Olá",
                        "words": [{"word": "Olá", "start": 0.0, "end": 0.5}],
                    }],
                },
                [],
                config=captured[-1],
                source_sha256=SOURCE_HASH,
            )

    monkeypatch.setattr(transcribe, "WhisperXProvider", StubProvider)
    monkeypatch.setattr(
        transcribe,
        "sha256_file",
        lambda _path: SOURCE_HASH,
    )
    monkeypatch.setattr(
        transcribe,
        "validate_provisional_normative_transcript",
        lambda _payload: [],
    )

    transcribe.transcribe_one(source, workspace / "edit", verbose=False)

    assert captured[0].diarization_mode == DIARIZATION_NONE
    assert captured[0].vad_method == "silero"


def test_preview_source_profile_preserves_exact_local_config(tmp_path):
    config = WhisperXConfig(
        batch_size=1,
        diarization_mode=DIARIZATION_NONE,
        vad_method="silero",
        diarization_model=None,
        diarization_model_revision=None,
    )
    payload = convert_whisperx_result(
        {
            "language": "pt",
            "segments": [{
                "start": 0.0,
                "end": 0.5,
                "text": "Olá",
                "words": [{"word": "Olá", "start": 0.0, "end": 0.5}],
            }],
        },
        [],
        config=config,
        source_sha256=SOURCE_HASH,
    )
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "source-a.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    provider, recovered = load_source_transcription_profile(
        {"sources": {"source-a": {"path": "source-a.wav"}}},
        transcripts,
    )

    assert provider == "whisperx_faster_whisper"
    assert recovered == config


def test_preview_source_profile_rejects_mixed_provider_configs(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    whisper_config = WhisperXConfig(
        diarization_mode=DIARIZATION_NONE,
        vad_method="silero",
        diarization_model=None,
        diarization_model_revision=None,
    )
    whisper_payload = convert_whisperx_result(
        {
            "language": "pt",
            "segments": [{
                "start": 0.0,
                "end": 0.5,
                "text": "Olá",
                "words": [{"word": "Olá", "start": 0.0, "end": 0.5}],
            }],
        },
        [],
        config=whisper_config,
        source_sha256=SOURCE_HASH,
    )
    eleven_payload = convert_elevenlabs_result(
        {
            "text": "Mundo",
            "language_code": "pt",
            "words": [{
                "type": "word",
                "text": "Mundo",
                "start": 0.0,
                "end": 0.5,
                "speaker_id": "speaker_0",
            }],
        },
        config=ElevenLabsConfig(),
        source_sha256=SOURCE_HASH,
    )
    (transcripts / "source-a.json").write_text(
        json.dumps(whisper_payload), encoding="utf-8"
    )
    (transcripts / "source-b.json").write_text(
        json.dumps(eleven_payload), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="share one provider/configuration"):
        load_source_transcription_profile(
            {
                "sources": {
                    "source-a": {"path": "source-a.wav"},
                    "source-b": {"path": "source-b.wav"},
                }
            },
            transcripts,
        )


def test_preview_qc_uses_the_exact_source_profile(monkeypatch, tmp_path):
    edit_dir = tmp_path / "edit"
    transcripts = edit_dir / "transcripts"
    transcripts.mkdir(parents=True)
    write_settings_atomic(
        workspace_settings_path(tmp_path),
        TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE),
    )
    preview = edit_dir / "preview.wav"
    preview.write_bytes(b"synthetic preview wav")
    config = WhisperXConfig(
        batch_size=1,
        diarization_mode=DIARIZATION_NONE,
        vad_method="silero",
        diarization_model=None,
        diarization_model_revision=None,
    )
    source_payload = convert_whisperx_result(
        {
            "language": "pt",
            "segments": [{
                "start": 0.0,
                "end": 0.5,
                "text": "Olá",
                "words": [{"word": "Olá", "start": 0.0, "end": 0.5}],
            }],
        },
        [],
        config=config,
        source_sha256=SOURCE_HASH,
    )
    (transcripts / "source-a.json").write_text(
        json.dumps(source_payload), encoding="utf-8"
    )
    edl = edit_dir / "edl.json"
    edl.write_text(
        json.dumps({"sources": {"source-a": {"path": "source-a.wav"}}}),
        encoding="utf-8",
    )
    timeline_map = edit_dir / "preview_timeline.json"
    timeline_map.write_text(
        json.dumps({"edl_hash": preview_transcript_qc.compute_sha256(edl)}),
        encoding="utf-8",
    )
    captured: list[WhisperXConfig] = []

    class StubProvider:
        def __init__(self, recovered_config):
            captured.append(recovered_config)

        def transcribe(self, _audio_path):
            return convert_whisperx_result(
                {
                    "language": "pt",
                    "segments": [{
                        "start": 0.0,
                        "end": 0.5,
                        "text": "Olá",
                        "words": [{"word": "Olá", "start": 0.0, "end": 0.5}],
                    }],
                },
                [],
                config=captured[-1],
                source_sha256=preview_transcript_qc.compute_sha256(preview),
            )

    monkeypatch.setattr(preview_transcript_qc, "WhisperXTranscriptProvider", StubProvider)
    monkeypatch.setattr(
        preview_transcript_qc,
        "build_report",
        lambda *_args, **_kwargs: {
            "summary": {
                "status": "pass",
                "adjacent_repeat_count": 0,
                "repeated_ngram_count": 0,
                "cue_hit_count": 0,
                "join_review_count": 0,
            }
        },
    )
    output = edit_dir / "preview_transcript_qc.json"
    persisted = transcripts / "preview.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preview_transcript_qc.py",
            str(preview),
            "--edl",
            str(edl),
            "--transcripts",
            str(transcripts),
            "--timeline-map",
            str(timeline_map),
            "--provider",
            "configured",
            "--transcript-output",
            str(persisted),
            "--output",
            str(output),
        ],
    )

    preview_transcript_qc.main()

    assert captured == [config]
    assert persisted.is_file()
    assert output.is_file()


def test_model_profile_excludes_community_when_diarization_is_disabled():
    no_diarization_names = [name for name, _repo, _path in _required_specs(
        TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE)
    )]
    community_names = [name for name, _repo, _path in _required_specs(
        TranscriptionSettings.whisperx(diarization=DIARIZATION_COMMUNITY_1)
    )]

    assert "community-1" not in no_diarization_names
    assert "community-1" in community_names


def test_explicit_model_profile_wins_over_an_elevenlabs_workspace(monkeypatch):
    observed: list[TranscriptionSettings] = []
    monkeypatch.setattr(
        transcription_models,
        "resolve_settings",
        lambda **_kwargs: TranscriptionSettings.elevenlabs(),
    )
    monkeypatch.setattr(
        transcription_models,
        "doctor_models",
        lambda settings: observed.append(settings) or {"status": "pass"},
    )

    assert transcription_models.main(["doctor", "--profile", "none"]) == 0
    assert observed == [TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE)]


def test_silero_doctor_requires_the_pinned_hub_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription_models, "default_model_cache", lambda: tmp_path)
    monkeypatch.setattr(
        transcription_models,
        "_snapshot",
        lambda *_args, **_kwargs: "cached",
    )
    settings = TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE)
    (tmp_path / "torch-hub").mkdir()

    assert transcription_models.doctor_models(settings)["status"] == "unhealthy"

    checkout = tmp_path / "torch-hub" / (
        "snakers4_silero-vad_b163605b3f44c3aadf28f97b125a2f7c461e9a7f"
    )
    checkout.mkdir()
    checkout.joinpath("hubconf.py").write_text("# pinned", encoding="utf-8")
    assert transcription_models.doctor_models(settings)["status"] == "pass"


def test_local_wizard_runs_model_prefetch_in_heavy_runtime(monkeypatch):
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(wizard, "_check_space", lambda **_kwargs: None)
    monkeypatch.setattr(wizard, "_runtime_python", lambda: Path(r"C:\runtime\python.exe"))

    def fake_run(arguments, *, python=None):
        calls.append((list(arguments), str(python) if python else None))

    monkeypatch.setattr(wizard, "_run_helper", fake_run)
    wizard.provision(
        TranscriptionSettings.whisperx(diarization=DIARIZATION_NONE),
        non_interactive=True,
    )

    assert calls[0][0][-1] == "setup"
    assert calls[0][1] is None
    assert calls[1][0][-3:] == ["prefetch", "--profile", "none"]
    assert calls[1][1] == r"C:\runtime\python.exe"


def test_elevenlabs_wizard_does_not_touch_local_runtime(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-key")
    monkeypatch.setenv("ALANOCUT_SKIP_CREDENTIAL_VALIDATION", "1")
    monkeypatch.setattr(wizard, "global_env_path", lambda: env_path)
    monkeypatch.setattr(
        wizard,
        "_run_helper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime must not run")),
    )

    wizard.provision(TranscriptionSettings.elevenlabs(), non_interactive=True)

    assert "ELEVENLABS_API_KEY=private-key" in env_path.read_text(encoding="utf-8")


def test_init_wizard_preflights_before_workspace_exists(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace-not-created"
    output = tmp_path / "chosen-profile.json"
    monkeypatch.setattr(wizard, "provision", lambda *_args, **_kwargs: None)
    args = argparse.Namespace(
        command="init",
        workspace=workspace,
        settings_output=output,
        provider=PROVIDER_ELEVENLABS,
        diarization=None,
        non_interactive=True,
    )

    destination = wizard.run_wizard(args)

    assert destination == output.resolve()
    assert output.is_file()
    assert not workspace.exists()
    assert read_settings(output) == TranscriptionSettings.elevenlabs()
