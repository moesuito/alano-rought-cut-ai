from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from helpers import transcribe, transcribe_batch
from helpers.transcription_contract import WhisperXConfig
from helpers.transcription_providers import (
    TranscriptionProviderError,
    WhisperXProvider,
    ensure_no_secret_fields,
    load_env_value,
    whisperx_worker_config,
)
from helpers.whisperx_worker import _redact, _validate_config, assign_speakers


def raw_worker_result(source: Path) -> dict:
    cfg = WhisperXConfig()
    return {
        "worker_schema_version": 2,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "language": "pt",
        "segments": [
            {
                "start": 0.1,
                "end": 0.8,
                "text": "Olá mundo",
                "speaker": "speaker_0",
                "words": [
                    {
                        "word": "Olá",
                        "start": 0.1,
                        "end": 0.35,
                        "score": 0.98,
                        "speaker": "speaker_0",
                    },
                    {
                        "word": "mundo",
                        "start": 0.4,
                        "end": 0.8,
                        "score": 0.96,
                        "speaker": "speaker_0",
                    },
                ],
            }
        ],
        "diarization": [{"start": 0.0, "end": 1.0, "speaker": "speaker_0"}],
        "models": {
            "asr": cfg.model,
            "semantic_verifier": cfg.semantic_verifier_model,
            "alignment": cfg.align_model,
            "diarization": cfg.diarization_model,
        },
        "model_revisions": {
            "asr": cfg.model_revision,
            "semantic_verifier": cfg.semantic_verifier_revision,
            "alignment": cfg.align_model_revision,
            "diarization": cfg.diarization_model_revision,
        },
        "runtime": {
            "whisperx": "3.8.6",
            "faster_whisper": "1.2.1",
            "pyannote_audio": "4.0.7",
            "torch": "2.8.0+cu128",
            "cuda": "12.8",
            "gpu": "NVIDIA GeForce RTX 3060 Laptop GPU",
            "device": "cuda",
            "compute_type": "float16",
            "batch_size": 2,
        },
        "semantic_verification": {
            "status": "pass",
            "mode": cfg.semantic_fusion_mode,
            "revision": cfg.semantic_fusion_revision,
            "asr_mode": cfg.vad_method,
            "coverage_asr_mode": "windowed_no_vad",
            "semantic_source": "semantic_verifier",
            "contextual_token_count": 2,
            "coverage_token_count": 2,
            "cue_words": sorted(cfg.recording_cues.split(",")),
            "recoveries": [],
        },
        "diarization_exclusive": True,
        "phase_seconds": {"asr": 1.0, "alignment": 0.5, "diarization": 0.5},
        "peak_vram_bytes": {"asr": 123},
    }


def fake_acoustic_refiner(source: Path, raw: dict, **kwargs):
    return raw, {
        "status": "pass",
        "blocking_outlier_count": 0,
        "blocking_outliers": [],
        "source_sha256": raw["source_sha256"],
        "rnnoise_model_sha256": (
            "F1357C4E5BE9DEE8467BEAD486DFCED2D75B640C26AD0B594FA7F102322371D9"
        ),
        "parameters": {"hop_seconds": 0.005},
        "evidence": [],
        "semantic_recovery_evidence": [],
    }


def fake_provisional_acoustic_refiner(source: Path, raw: dict, **kwargs):
    refined, report = fake_acoustic_refiner(source, raw, **kwargs)
    report.update({
        "status": "review",
        "blocking_outlier_count": 1,
        "blocking_outliers": [
            {
                "type": "unattributed_bilateral_activity",
                "component": {
                    "index": 2,
                    "start": 2.0,
                    "end": 2.1,
                    "bilateral_start": 2.0,
                    "bilateral_end": 2.1,
                },
            }
        ],
    })
    return refined, report


def test_env_loader_prefers_process_and_reads_nearest_file(monkeypatch, tmp_path):
    project = tmp_path / "project"
    nested = project / "audio"
    nested.mkdir(parents=True)
    (project / ".env").write_text("HF_TOKEN=file-secret\n", encoding="utf-8")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert load_env_value("HF_TOKEN", start=nested) == "file-secret"

    monkeypatch.setenv("HF_TOKEN", "process-secret")
    assert load_env_value("HF_TOKEN", start=nested) == "process-secret"


def test_worker_config_never_contains_authentication_fields():
    payload = whisperx_worker_config(WhisperXConfig())
    serialized = json.dumps(payload).lower()
    assert "token" not in serialized
    assert "secret" not in serialized
    assert payload["device"] == "cuda"
    assert payload["batch_size"] == 2


def test_provider_passes_token_only_in_environment(monkeypatch, tmp_path):
    source = tmp_path / "clip.wav"
    source.write_bytes(b"fake wav")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"")
    secret = "private-test-token"
    monkeypatch.setenv("HF_TOKEN", secret)
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = list(command)
        observed["env"] = kwargs["env"]
        assert secret not in " ".join(command)
        assert kwargs["capture_output"] if "capture_output" in kwargs else True
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(raw_worker_result(source)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    transcript = WhisperXProvider(
        WhisperXConfig(),
        runtime_python=runtime,
        runner=runner,
        acoustic_refiner=fake_acoustic_refiner,
    ).transcribe(source)

    assert observed["env"]["HF_TOKEN"] == secret
    assert transcript["_alano_cut"]["diarization_status"]["exclusive"] is True
    assert transcript["_alano_cut"]["runtime"]["device"] == "cuda"
    assert all(word["timing_source"] == "forced_alignment" for word in transcript["words"])
    assert secret not in json.dumps(transcript)


def test_provider_failure_is_secret_free(monkeypatch, tmp_path):
    source = tmp_path / "clip.wav"
    source.write_bytes(b"fake wav")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"")
    secret = "private-test-token"
    monkeypatch.setenv("HF_TOKEN", secret)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1)

    with pytest.raises(TranscriptionProviderError) as captured:
        WhisperXProvider(
            runtime_python=runtime,
            runner=runner,
            acoustic_refiner=fake_acoustic_refiner,
        ).transcribe(source)
    assert secret not in str(captured.value)


def test_first_run_provisional_transcript_succeeds_and_next_run_uses_cache(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "clip.wav"
    source.write_bytes(b"fake wav")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"")
    config = WhisperXConfig()

    def runner(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(raw_worker_result(source)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    payload = WhisperXProvider(
        config,
        runtime_python=runtime,
        runner=runner,
        acoustic_refiner=fake_provisional_acoustic_refiner,
    ).transcribe(source)

    class StubProvider:
        def transcribe(self, _source):
            return payload

    monkeypatch.setattr(transcribe, "WhisperXProvider", lambda *args, **kwargs: StubProvider())
    edit_dir = tmp_path / "edit"
    first = transcribe.transcribe_one(
        source,
        edit_dir,
        provider="whisperx",
        config=config,
        verbose=False,
    )

    def unexpected_provider(*args, **kwargs):
        raise AssertionError("valid provisional cache should avoid retranscription")

    monkeypatch.setattr(transcribe, "WhisperXProvider", unexpected_provider)
    second = transcribe.transcribe_one(
        source,
        edit_dir,
        provider="whisperx",
        config=config,
        verbose=False,
    )

    assert first == second
    assert json.loads(first.read_text(encoding="utf-8"))["_alano_cut"]["acoustic_timing"]["status"] == "review"


@pytest.mark.parametrize("field", ["token", "hf_token", "api_key", "authorization"])
def test_forbidden_secret_fields_are_rejected(field):
    with pytest.raises(TranscriptionProviderError, match="credential field"):
        ensure_no_secret_fields({"metadata": {field: "value"}})


def test_worker_rejects_cpu_legacy_diarization_and_mixed_speaker_constraints():
    base = whisperx_worker_config(WhisperXConfig())
    assert _validate_config(dict(base))["device"] == "cuda"
    with pytest.raises(ValueError, match="device='cuda'"):
        _validate_config(base | {"device": "cpu"})
    with pytest.raises(ValueError, match="diarization_model"):
        _validate_config(base | {"diarization_model": "pyannote/speaker-diarization-3.1"})
    with pytest.raises(ValueError, match="cannot be combined"):
        _validate_config(base | {"num_speakers": 2, "min_speakers": 1})


def test_worker_speaker_assignment_uses_exclusive_turn_overlap():
    aligned = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "words": [
                    {"word": "um", "start": 0.2, "end": 0.4},
                    {"word": "dois", "start": 1.2, "end": 1.5},
                ],
            }
        ]
    }
    assigned = assign_speakers(
        aligned,
        [
            {"start": 0.0, "end": 1.0, "speaker": "speaker_0"},
            {"start": 1.0, "end": 2.0, "speaker": "speaker_1"},
        ],
    )
    assert [word["speaker"] for word in assigned["segments"][0]["words"]] == [
        "speaker_0",
        "speaker_1",
    ]
    # Exact segment tie retains the first chronological turn.
    assert assigned["segments"][0]["speaker"] == "speaker_0"


def test_worker_error_redaction(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "never-print-this")
    assert "never-print-this" not in _redact("bad never-print-this value")


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


def test_transcribe_cli_defaults_to_normative_cuda_stack():
    args = transcribe.build_parser().parse_args(["clip.wav"])
    assert args.provider == "whisperx"
    assert args.model == "large-v3"
    assert args.compute_type == "float16"
    assert args.batch_size == 2
    assert args.diarization_model == "pyannote/speaker-diarization-community-1"


def test_no_hf_token_cli_argument_exists():
    worker_help = Path("helpers/whisperx_worker.py").read_text(encoding="utf-8")
    provider_source = Path("helpers/transcription_providers.py").read_text(encoding="utf-8")
    assert "--hf_token" not in worker_help
    assert "--hf-token" not in worker_help
    assert "--hf_token" not in provider_source
