import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from helpers.gpu_detection import (
    detect_cuda_available,
    detect_recommended_runtime,
    get_detected_gpus,
)
from helpers.transcription_contract import (
    VULKAN_WHISPER_TRANSCRIPTION_PROVIDER,
    VulkanWhisperConfig,
    TranscriptContractError,
    config_hash,
    convert_vulkan_whisper_result,
    is_cache_valid,
    validate_normative_transcript,
    validate_transcript,
)
from helpers.transcription_settings import TranscriptionSettings, PROVIDER_VULKAN
from helpers.vulkan_runtime import (
    doctor,
    parse_whisper_cpp_tokens_to_words,
)


def test_vulkan_whisper_config_defaults_and_hash():
    cfg = VulkanWhisperConfig()
    assert cfg.model == "large-v3-turbo"
    assert cfg.language == "pt"
    assert cfg.device == "vulkan"
    assert cfg.diarization_mode == "none"

    d = cfg.to_dict()
    assert d["device"] == "vulkan"
    assert len(cfg.sha256) == 64
    assert cfg.sha256 == config_hash(cfg)


def test_vulkan_whisper_config_invalid():
    with pytest.raises(TranscriptContractError):
        VulkanWhisperConfig(device="cpu")

    with pytest.raises(TranscriptContractError):
        VulkanWhisperConfig(diarization_mode="community-1")


def test_parse_whisper_cpp_tokens_to_words():
    raw_payload = {
        "result": {"language": "pt"},
        "transcription": [
            {
                "text": " Nas próximas aulas,",
                "tokens": [
                    {"text": " Nas", "offsets": {"from": 780, "to": 790}},
                    {"text": " pr", "offsets": {"from": 780, "to": 1200}},
                    {"text": "óximas", "offsets": {"from": 1200, "to": 3120}},
                    {"text": " aulas", "offsets": {"from": 3380, "to": 4460}},
                    {"text": ",", "offsets": {"from": 4460, "to": 4500}},
                ],
            }
        ],
    }
    words = parse_whisper_cpp_tokens_to_words(raw_payload)
    assert len(words) == 3
    assert words[0]["text"] == "Nas"
    assert words[0]["start"] == 0.78
    assert words[0]["end"] == 0.79
    assert words[1]["text"] == "próximas"
    assert words[1]["start"] == 0.78
    assert words[1]["end"] == 3.12
    assert words[2]["text"] == "aulas,"
    assert words[2]["start"] == 3.38
    assert words[2]["end"] == 4.5


def test_convert_vulkan_whisper_result():
    raw_payload = {
        "result": {"language": "pt"},
        "transcription": [
            {
                "text": " Olá mundo.",
                "tokens": [
                    {"text": " Olá", "offsets": {"from": 100, "to": 400}},
                    {"text": " mundo", "offsets": {"from": 450, "to": 800}},
                    {"text": ".", "offsets": {"from": 800, "to": 850}},
                ],
            }
        ],
    }
    cfg = VulkanWhisperConfig()
    source_hash = "f" * 64
    transcript = convert_vulkan_whisper_result(
        raw_payload,
        config=cfg,
        source_sha256=source_hash,
    )

    assert transcript["language_code"] == "pt"
    assert len(transcript["words"]) == 2
    assert transcript["words"][0]["text"] == "Olá"
    assert transcript["words"][0]["start"] == 0.1
    assert transcript["words"][0]["speaker_id"] is None
    assert transcript["words"][0]["speaker_assignment"] == "disabled"
    assert transcript["words"][0]["timing_source"] == "provider_word_timestamp"

    assert transcript["words"][1]["text"] == "mundo."

    # Check segments
    assert len(transcript["segments"]) == 1
    assert transcript["segments"][0]["speaker_id"] is None

    # Check metadata
    meta = transcript["_alano_cut"]
    assert meta["transcription_provider"] == VULKAN_WHISPER_TRANSCRIPTION_PROVIDER
    assert meta["source_sha256"] == source_hash
    assert meta["config_sha256"] == cfg.sha256
    assert meta["runtime"]["device"] == "vulkan"
    assert meta["models"]["asr"] == "large-v3-turbo"

    # Validate contracts
    validate_transcript(transcript)
    validate_normative_transcript(transcript)


def test_vulkan_cache_validity(tmp_path):
    cfg = VulkanWhisperConfig()
    source_hash = "c" * 64
    raw_payload = {
        "result": {"language": "pt"},
        "transcription": [
            {
                "text": " Teste",
                "tokens": [
                    {"text": " Teste", "offsets": {"from": 0, "to": 500}},
                ],
            }
        ],
    }
    transcript = convert_vulkan_whisper_result(raw_payload, config=cfg, source_sha256=source_hash)
    cache_file = tmp_path / "vulkan_test.json"
    cache_file.write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    assert is_cache_valid(cache_file, source_sha256=source_hash, config=cfg) is True
    assert is_cache_valid(cache_file, source_sha256="d" * 64, config=cfg) is False


def test_vulkan_transcription_settings():
    settings = TranscriptionSettings.vulkan(language="pt")
    assert settings.provider == PROVIDER_VULKAN
    assert settings.language == "pt"
    assert settings.device == "vulkan"
    assert settings.diarization == "none"


def test_gpu_detection():
    hw = detect_recommended_runtime()
    assert "recommended_runtime" in hw
    assert hw["recommended_runtime"] in {"cuda", "vulkan"}
    assert "devices" in hw
    assert isinstance(hw["devices"], list)


def test_vulkan_doctor():
    doc = doctor()
    assert "status" in doc
    assert doc["provider"] == "whisper-vulkan"
    assert "checks" in doc
