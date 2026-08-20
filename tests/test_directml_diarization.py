import json
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch

from helpers.directml_diarization import (
    DirectMLDiarizer,
    doctor,
    get_available_execution_providers,
)
from helpers.transcription_contract import (
    DIARIZATION_COMMUNITY_1,
    DIARIZATION_NONE,
    VULKAN_WHISPER_TRANSCRIPTION_PROVIDER,
    VulkanWhisperConfig,
    convert_vulkan_whisper_result,
    validate_normative_transcript,
    validate_transcript,
)
from helpers.transcription_settings import TranscriptionSettings, PROVIDER_VULKAN


def test_directml_execution_providers():
    providers = get_available_execution_providers()
    assert isinstance(providers, list)
    assert "DmlExecutionProvider" in providers or "CPUExecutionProvider" in providers


def test_directml_diarization_doctor():
    doc = doctor()
    assert "status" in doc
    assert doc["runtime"] == "directml_onnx"
    assert "checks" in doc
    assert doc["checks"]["onnxruntime_installed"] is True
    assert doc["checks"]["scikit_learn_installed"] is True


def test_vulkan_whisper_config_with_diarization():
    cfg = VulkanWhisperConfig(diarization_mode=DIARIZATION_COMMUNITY_1)
    assert cfg.diarization_mode == DIARIZATION_COMMUNITY_1
    assert cfg.device == "vulkan"
    assert len(cfg.sha256) == 64


def test_convert_vulkan_whisper_result_with_diarization():
    raw_payload = {
        "result": {"language": "pt"},
        "transcription": [
            {
                "text": " Olá tudo bem? Tudo ótimo.",
                "tokens": [
                    {"text": " Olá", "offsets": {"from": 100, "to": 400}},
                    {"text": " tudo", "offsets": {"from": 450, "to": 800}},
                    {"text": " bem", "offsets": {"from": 820, "to": 1100}},
                    {"text": "?", "offsets": {"from": 1100, "to": 1150}},
                    {"text": " Tudo", "offsets": {"from": 2000, "to": 2400}},
                    {"text": " ótimo", "offsets": {"from": 2450, "to": 2900}},
                    {"text": ".", "offsets": {"from": 2900, "to": 2950}},
                ],
            }
        ],
    }
    diarization_turns = [
        {"start": 0.0, "end": 1.5, "speaker_id": "speaker_0"},
        {"start": 1.8, "end": 3.2, "speaker_id": "speaker_1"},
    ]
    cfg = VulkanWhisperConfig(diarization_mode=DIARIZATION_COMMUNITY_1)
    source_hash = "e" * 64

    transcript = convert_vulkan_whisper_result(
        raw_payload,
        config=cfg,
        diarization=diarization_turns,
        source_sha256=source_hash,
    )

    assert transcript["language_code"] == "pt"
    assert len(transcript["words"]) == 5

    # First phrase: speaker_0
    assert transcript["words"][0]["text"] == "Olá"
    assert transcript["words"][0]["speaker_id"] == "speaker_0"
    assert transcript["words"][0]["speaker_assignment"] == "turn_overlap"

    assert transcript["words"][1]["text"] == "tudo"
    assert transcript["words"][1]["speaker_id"] == "speaker_0"

    assert transcript["words"][2]["text"] == "bem?"
    assert transcript["words"][2]["speaker_id"] == "speaker_0"

    # Second phrase: speaker_1
    assert transcript["words"][3]["text"] == "Tudo"
    assert transcript["words"][3]["speaker_id"] == "speaker_1"

    assert transcript["words"][4]["text"] == "ótimo."
    assert transcript["words"][4]["speaker_id"] == "speaker_1"

    # Diarization list & status
    assert len(transcript["diarization"]) == 2
    assert transcript["diarization"][0]["speaker_id"] == "speaker_0"
    assert transcript["diarization"][1]["speaker_id"] == "speaker_1"

    meta = transcript["_alano_cut"]
    assert meta["transcription_provider"] == VULKAN_WHISPER_TRANSCRIPTION_PROVIDER
    assert meta["diarization_status"]["status"] == "pass"
    assert meta["diarization_status"]["model"] == "pyannote_onnx"
    assert meta["models"]["diarization"] == "pyannote_onnx"

    # Schema v2 compliance validation
    validate_transcript(transcript)
    validate_normative_transcript(transcript)


def test_vulkan_transcription_settings_diarization():
    settings = TranscriptionSettings.vulkan(diarization=DIARIZATION_COMMUNITY_1)
    assert settings.provider == PROVIDER_VULKAN
    assert settings.diarization == DIARIZATION_COMMUNITY_1
