import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from helpers.transcription_contract import (
    ASSEMBLYAI_TRANSCRIPTION_PROVIDER,
    AssemblyAIConfig,
    TranscriptContractError,
    config_hash,
    convert_assemblyai_result,
    is_cache_valid,
    normalize_speaker_id,
    validate_normative_transcript,
    validate_transcript,
)
from helpers.transcribe import transcribe_one
from helpers.transcription_settings import TranscriptionSettings, PROVIDER_ASSEMBLYAI


def test_assemblyai_config_defaults_and_hash():
    cfg = AssemblyAIConfig()
    assert cfg.speech_model == "best"
    assert cfg.language_code == "pt"
    assert cfg.speaker_labels is True
    assert cfg.punctuate is True
    assert cfg.format_text is True

    d = cfg.to_dict()
    assert d["speech_model"] == "best"
    assert len(cfg.sha256) == 64
    assert cfg.sha256 == config_hash(cfg)


def test_assemblyai_config_invalid():
    with pytest.raises(TranscriptContractError):
        AssemblyAIConfig(speech_model="invalid_model")

    with pytest.raises(TranscriptContractError):
        AssemblyAIConfig(speaker_labels=False)


def test_speaker_normalization_letters_and_digits():
    assert normalize_speaker_id("A") == "speaker_0"
    assert normalize_speaker_id("B") == "speaker_1"
    assert normalize_speaker_id("C") == "speaker_2"
    assert normalize_speaker_id("SPEAKER_00") == "speaker_0"
    assert normalize_speaker_id("speaker_1") == "speaker_1"
    assert normalize_speaker_id(0) == "speaker_0"
    assert normalize_speaker_id(1) == "speaker_1"


def test_convert_assemblyai_result():
    raw_payload = {
        "id": "mock-assembly-id",
        "language_code": "pt",
        "text": "Olá mundo, bem-vindos.",
        "words": [
            {
                "text": "Olá",
                "start": 100,  # 0.100s
                "end": 400,    # 0.400s
                "confidence": 0.95,
                "speaker": "A",
            },
            {
                "text": "mundo,",
                "start": 450,  # 0.450s
                "end": 800,    # 0.800s
                "confidence": 0.98,
                "speaker": "A",
            },
            {
                "text": "bem-vindos.",
                "start": 1200, # 1.200s
                "end": 1800,   # 1.800s
                "confidence": 0.92,
                "speaker": "B",
            },
        ],
    }
    cfg = AssemblyAIConfig()
    source_hash = "a" * 64
    transcript = convert_assemblyai_result(
        raw_payload,
        config=cfg,
        source_sha256=source_hash,
    )

    assert transcript["language_code"] == "pt"
    assert len(transcript["words"]) == 3
    assert transcript["words"][0]["text"] == "Olá"
    assert transcript["words"][0]["start"] == 0.1
    assert transcript["words"][0]["end"] == 0.4
    assert transcript["words"][0]["speaker_id"] == "speaker_0"
    assert transcript["words"][0]["score"] == 0.95

    assert transcript["words"][2]["speaker_id"] == "speaker_1"

    # Check segments
    assert len(transcript["segments"]) == 2
    assert transcript["segments"][0]["speaker_id"] == "speaker_0"
    assert transcript["segments"][1]["speaker_id"] == "speaker_1"

    # Check metadata
    meta = transcript["_alano_cut"]
    assert meta["transcription_provider"] == ASSEMBLYAI_TRANSCRIPTION_PROVIDER
    assert meta["source_sha256"] == source_hash
    assert meta["config_sha256"] == cfg.sha256

    # Validate contracts
    validate_transcript(transcript)
    validate_normative_transcript(transcript)


def test_assemblyai_cache_validity(tmp_path):
    cfg = AssemblyAIConfig()
    source_hash = "b" * 64
    raw_payload = {
        "id": "mock-id",
        "language_code": "pt",
        "words": [
            {
                "text": "Teste",
                "start": 0,
                "end": 500,
                "confidence": 0.99,
                "speaker": "A",
            }
        ],
    }
    transcript = convert_assemblyai_result(raw_payload, config=cfg, source_sha256=source_hash)
    cache_file = tmp_path / "test.json"
    cache_file.write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    assert is_cache_valid(cache_file, source_sha256=source_hash, config=cfg) is True
    assert is_cache_valid(cache_file, source_sha256="c" * 64, config=cfg) is False


def test_assemblyai_transcription_settings():
    settings = TranscriptionSettings.assemblyai(language="pt")
    assert settings.provider == PROVIDER_ASSEMBLYAI
    assert settings.language == "pt"
    assert settings.device == "cloud"
    assert settings.diarization == "provider"
