"""Regression coverage for unified multi-provider transcription setup."""

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
    DIARIZATION_COMMUNITY_1,
    DIARIZATION_NONE,
    ElevenLabsConfig,
    AssemblyAIConfig,
    VulkanWhisperConfig,
    convert_elevenlabs_result,
    convert_assemblyai_result,
    convert_vulkan_whisper_result,
    validate_normative_transcript,
    validate_transcript,
)
from helpers.transcription_settings import (
    PROVIDER_ASSEMBLYAI,
    PROVIDER_ELEVENLABS,
    PROVIDER_VULKAN,
    TranscriptionSettings,
    read_settings,
    resolve_settings,
    user_settings_path,
    workspace_settings_path,
    write_settings_atomic,
)


SOURCE_HASH = "a" * 64


def test_workspace_profile_overrides_user_preference(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    user_profile = TranscriptionSettings.elevenlabs()
    workspace_profile = TranscriptionSettings.vulkan(diarization=DIARIZATION_NONE)
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


def test_vulkan_result_is_canonical_and_provider_bound():
    payload = convert_vulkan_whisper_result(
        [
            {"text": "Olá", "start": 0.0, "end": 0.3},
            {"text": "mundo.", "start": 0.35, "end": 0.8},
        ],
        config=VulkanWhisperConfig(diarization_mode=DIARIZATION_NONE),
        source_sha256=SOURCE_HASH,
    )

    assert payload["_alano_cut"]["transcription_provider"] == "whisper_vulkan_large"
    assert all(word["timing_source"] == "provider_word_timestamp" for word in payload["words"])
    assert payload["words"][0]["speaker_id"] is None
    assert payload["words"][0]["speaker_assignment"] == "disabled"
    validate_transcript(payload)
    validate_normative_transcript(payload)


def test_programmatic_configured_provider_honors_workspace_profile(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_settings_atomic(
        workspace_settings_path(workspace),
        TranscriptionSettings.vulkan(diarization=DIARIZATION_NONE),
    )
    source = workspace / "speech.wav"
    source.write_bytes(b"synthetic source")
    captured: list[VulkanWhisperConfig] = []

    def stub_vulkan_transcript(src, *, language, config):
        captured.append(config)
        return convert_vulkan_whisper_result(
            [{"text": "Olá", "start": 0.0, "end": 0.5}],
            config=config,
            source_sha256=SOURCE_HASH,
        )

    monkeypatch.setattr(transcribe, "_vulkan_transcript", stub_vulkan_transcript)
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
    assert captured[0].device == "vulkan"


def test_preview_source_profile_preserves_exact_vulkan_config(tmp_path):
    config = VulkanWhisperConfig(diarization_mode=DIARIZATION_COMMUNITY_1)
    payload = convert_vulkan_whisper_result(
        [{"text": "Olá", "start": 0.0, "end": 0.5}],
        config=config,
        diarization=[{"start": 0.0, "end": 0.5, "speaker_id": "speaker_0"}],
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

    assert provider == "whisper_vulkan_large"
    assert recovered == config


def test_preview_source_profile_rejects_mixed_provider_configs(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    vulkan_config = VulkanWhisperConfig(diarization_mode=DIARIZATION_NONE)
    vulkan_payload = convert_vulkan_whisper_result(
        [{"text": "Olá", "start": 0.0, "end": 0.5}],
        config=vulkan_config,
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
        json.dumps(vulkan_payload), encoding="utf-8"
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


def test_elevenlabs_wizard_saves_key_in_env(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-key")
    monkeypatch.setenv("ALANOCUT_SKIP_CREDENTIAL_VALIDATION", "1")
    monkeypatch.setattr(wizard, "global_env_path", lambda: env_path)

    wizard.provision(TranscriptionSettings.elevenlabs(), non_interactive=True)

    assert "ELEVENLABS_API_KEY=private-key" in env_path.read_text(encoding="utf-8")


def test_init_wizard_creates_workspace_settings(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace-new"
    monkeypatch.setattr(wizard, "provision", lambda *_args, **_kwargs: None)

    settings = wizard.run_init(
        workspace,
        provider=PROVIDER_VULKAN,
        diarization=DIARIZATION_NONE,
        non_interactive=True,
    )

    assert settings.provider == PROVIDER_VULKAN
    assert settings.diarization == DIARIZATION_NONE
    assert workspace_settings_path(workspace).is_file()
    assert read_settings(workspace_settings_path(workspace)) == settings
