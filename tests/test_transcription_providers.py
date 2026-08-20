from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import transcribe, transcribe_batch
from helpers.transcription_contract import (
    VulkanWhisperConfig,
    DIARIZATION_COMMUNITY_1,
    DIARIZATION_NONE,
)
from helpers.transcription_providers import (
    TranscriptionProviderError,
    ensure_no_secret_fields,
    load_env_value,
)


def test_env_loader_prefers_process_and_reads_nearest_file(monkeypatch, tmp_path):
    project = tmp_path / "project"
    nested = project / "audio"
    nested.mkdir(parents=True)
    (project / ".env").write_text("HF_TOKEN=file-secret\n", encoding="utf-8")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert load_env_value("HF_TOKEN", start=nested) == "file-secret"

    monkeypatch.setenv("HF_TOKEN", "process-secret")
    assert load_env_value("HF_TOKEN", start=nested) == "process-secret"


@pytest.mark.parametrize("field", ["token", "hf_token", "api_key", "authorization"])
def test_forbidden_secret_fields_are_rejected(field):
    with pytest.raises(TranscriptionProviderError, match="credential field"):
        ensure_no_secret_fields({"metadata": {field: "value"}})


def test_batch_finds_audio_and_video_and_rejects_colliding_stems(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "b.MOV").write_bytes(b"")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")
    assert [path.name for path in transcribe_batch.find_sources(tmp_path)] == [
        "a.wav",
        "b.MOV",
    ]

    (tmp_path / "a.mp4").write_bytes(b"")
    with pytest.raises(ValueError, match="same transcript stem"):
        transcribe_batch.find_sources(tmp_path)


def test_transcribe_cli_defaults():
    args = transcribe.build_parser().parse_args(["clip.wav"])
    assert args.provider == "configured"
    assert args.language == "pt"
