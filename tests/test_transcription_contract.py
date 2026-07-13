from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

import helpers.transcription_contract as contract
from helpers.transcription_contract import (
    TRANSCRIPT_SCHEMA_VERSION,
    TRANSCRIPTION_PROVIDER,
    TranscriptContractError,
    WhisperXConfig,
    analyze_alignment_quality,
    config_hash,
    convert_whisperx_result,
    is_cache_valid,
    normalize_speaker_id,
    sha256_file,
    validate_transcript,
    validate_normative_transcript,
    validate_normative_transcript_for_intervals,
    validate_provisional_normative_transcript,
    write_json_atomic,
)


SOURCE_HASH = "a" * 64


def config() -> WhisperXConfig:
    return WhisperXConfig(
        model="large-v3",
        model_revision="model-rev",
        language="pt",
        device="cuda",
        compute_type="float16",
        batch_size=1,
        beam_size=5,
        align_model="jonatasgrosman/wav2vec2-large-xlsr-53-portuguese",
        align_model_revision="align-rev",
        diarization_model_revision="diar-rev",
        min_speakers=1,
        max_speakers=3,
        whisperx_version="3.4.2",
        faster_whisper_version="1.2.0",
        pyannote_audio_version="3.3.2",
    )


def aligned_result() -> dict:
    return {
        "language": "pt",
        "segments": [
            {
                "start": 0.0,
                "end": 1.5,
                "text": "Olá mundo",
                "words": [
                    {
                        "word": " Olá",
                        "start": 0.0,
                        "end": 0.48,
                        "score": 0.99,
                        "speaker": "SPEAKER_00",
                    },
                    {
                        "word": "mundo",
                        "start": 0.44,
                        "end": 1.20,
                        "score": 0.91,
                        "speaker": "SPEAKER_00",
                    },
                ],
            },
            {
                "start": 1.8,
                "end": 2.7,
                "text": "Tudo bem?",
                "words": [
                    {"word": "Tudo", "start": 1.80, "end": 2.08, "score": 0.87},
                    {"word": "bem?", "start": 2.10, "end": 2.62, "score": 0.95},
                ],
            },
        ],
    }


def diarization() -> list[dict]:
    return [
        {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"},
        {"start": 1.7, "end": 2.8, "speaker": "SPEAKER_01"},
    ]


def canonical_transcript() -> dict:
    transcript = convert_whisperx_result(
        aligned_result(),
        diarization(),
        config=config(),
        source_sha256=SOURCE_HASH,
    )
    cfg = config()
    metadata = transcript["_alano_cut"]
    metadata.update(
        {
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
                "whisperx": cfg.whisperx_version,
                "faster_whisper": cfg.faster_whisper_version,
                "pyannote_audio": cfg.pyannote_audio_version,
                "torch": "2.8.0+cu128",
                "cuda": "12.8",
                "gpu": "NVIDIA GeForce RTX 3060 Laptop GPU",
                "device": "cuda",
                "compute_type": cfg.compute_type,
                "batch_size": cfg.batch_size,
            },
            "semantic_verification": {
                "status": "pass",
                "mode": cfg.semantic_fusion_mode,
                "revision": cfg.semantic_fusion_revision,
                "asr_mode": cfg.vad_method,
                "coverage_asr_mode": "windowed_no_vad",
                "semantic_source": "semantic_verifier",
                "contextual_token_count": 4,
                "coverage_token_count": 4,
                "cue_words": sorted(contract._normalized_cue_tokens(cfg.recording_cues)),
                "recoveries": [],
            },
            "acoustic_timing": {
                "status": "pass",
                "revision": cfg.acoustic_snap_revision,
                "blocking_outlier_count": 0,
                "blocking_outliers": [],
                "source_sha256": SOURCE_HASH,
                "rnnoise_model_sha256": contract.EXPECTED_RNNOISE_MODEL_HASH,
                "parameters": {"hop_seconds": 0.005},
                "evidence": [],
                "semantic_recovery_evidence": [],
            },
            "alignment": {
                "model": cfg.align_model,
                "timed_word_coverage": 1.0,
                **analyze_alignment_quality(transcript),
            },
            "diarization_status": {
                "status": "pass",
                "model": cfg.diarization_model,
                "exclusive": True,
                "turn_count": len(transcript["diarization"]),
            },
        }
    )
    return transcript


def test_module_has_no_ml_runtime_imports():
    source = Path(contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported_roots.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported_roots.isdisjoint({"whisperx", "faster_whisper", "torch", "pyannote", "pandas"})


def test_config_is_serializable_and_hash_is_deterministic():
    first = config()
    same = WhisperXConfig(**first.to_dict())

    assert json.loads(json.dumps(first.to_dict())) == first.to_dict()
    assert first.sha256 == same.sha256 == config_hash(first.to_dict())
    assert len(first.sha256) == 64
    assert WhisperXConfig(beam_size=4).sha256 != WhisperXConfig(beam_size=5).sha256


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"beam_size": 0},
        {"min_speakers": 3, "max_speakers": 2},
        {"num_speakers": 4, "max_speakers": 3},
        {"device": "cpu"},
        {"diarization_model": "pyannote/speaker-diarization-3.1"},
        {"diarization_model": ""},
    ],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(TranscriptContractError):
        WhisperXConfig(**kwargs)


def test_sha256_file_streams_source(tmp_path):
    source = tmp_path / "source.wav"
    payload = (b"0123456789abcdef" * 100_000) + b"tail"
    source.write_bytes(payload)

    assert sha256_file(source) == hashlib.sha256(payload).hexdigest()


def test_convert_aligned_result_to_canonical_schema():
    transcript = canonical_transcript()

    assert transcript["text"] == "Olá mundo Tudo bem?"
    assert transcript["language_code"] == "pt"
    assert [word["text"] for word in transcript["words"]] == ["Olá", "mundo", "Tudo", "bem?"]
    assert [word["speaker_id"] for word in transcript["words"]] == [
        "speaker_0",
        "speaker_0",
        "speaker_1",
        "speaker_1",
    ]
    assert all(word["type"] == "word" for word in transcript["words"])
    assert all(word["timing_source"] == "forced_alignment" for word in transcript["words"])
    assert transcript["segments"][0]["speaker_id"] == "speaker_0"
    assert transcript["segments"][1]["speaker_id"] == "speaker_1"
    assert transcript["segments"][0]["start"] == 0.0
    assert transcript["segments"][0]["end"] == 1.20
    assert transcript["diarization"] == [
        {"start": 0.0, "end": 1.5, "speaker_id": "speaker_0"},
        {"start": 1.7, "end": 2.8, "speaker_id": "speaker_1"},
    ]

    metadata = transcript["_alano_cut"]
    assert metadata["schema_version"] == TRANSCRIPT_SCHEMA_VERSION
    assert metadata["transcription_provider"] == TRANSCRIPTION_PROVIDER
    assert metadata["source_sha256"] == SOURCE_HASH
    assert metadata["config"] == config().to_dict()
    assert metadata["config_sha256"] == config().sha256
    assert metadata["word_count"] == metadata["timed_word_count"] == 4
    assert metadata["timed_word_coverage"] == 1.0
    validate_transcript(transcript)
    validate_normative_transcript(transcript)


def test_normative_binding_rejects_cpu_or_nonexclusive_output():
    transcript = canonical_transcript()
    transcript["_alano_cut"]["runtime"]["torch"] = "2.8.0+cpu"
    with pytest.raises(TranscriptContractError, match="CUDA PyTorch"):
        validate_normative_transcript(transcript)

    transcript = canonical_transcript()
    transcript["_alano_cut"]["diarization_status"]["exclusive"] = False
    with pytest.raises(TranscriptContractError, match="exclusive diarization"):
        validate_normative_transcript(transcript)


def test_scoped_normative_validation_allows_only_out_of_selection_acoustic_orphans():
    transcript = canonical_transcript()
    blocker = {
        "type": "unattributed_bilateral_activity",
        "component": {
            "index": 7,
            "start": 5.0,
            "end": 5.5,
            "bilateral_start": 5.05,
            "bilateral_end": 5.45,
        },
    }
    acoustic = transcript["_alano_cut"]["acoustic_timing"]
    acoustic.update({
        "status": "review",
        "blocking_outlier_count": 1,
        "blocking_outliers": [blocker],
    })

    assert validate_provisional_normative_transcript(transcript) == [blocker]
    assert is_cache_valid(
        transcript,
        source_sha256=SOURCE_HASH,
        config=config(),
    )
    audited = validate_normative_transcript_for_intervals(
        transcript,
        [(0.0, 2.5)],
    )

    assert audited == [blocker]
    assert transcript["_alano_cut"]["acoustic_timing"]["status"] == "review"
    with pytest.raises(TranscriptContractError, match="overlaps selected"):
        validate_normative_transcript_for_intervals(transcript, [(5.25, 6.0)])


def test_scoped_normative_validation_never_waives_structural_acoustic_errors():
    transcript = canonical_transcript()
    acoustic = transcript["_alano_cut"]["acoustic_timing"]
    acoustic.update({
        "status": "review",
        "blocking_outlier_count": 1,
        "blocking_outliers": [{"type": "invalid_acoustic_interval"}],
    })

    with pytest.raises(TranscriptContractError, match="non-scopeable"):
        validate_provisional_normative_transcript(transcript)
    assert not is_cache_valid(
        transcript,
        source_sha256=SOURCE_HASH,
        config=config(),
    )
    with pytest.raises(TranscriptContractError, match="requires review"):
        validate_normative_transcript_for_intervals(transcript, [(0.0, 1.0)])


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (
            "thresholds",
            {
                "max_word_duration_seconds": 99.0,
                "max_seconds_per_character": 0.50,
                "low_score_threshold": 0.05,
            },
        ),
        (
            "outliers",
            [
                {
                    "word_index": 0,
                    "text": "Olá",
                    "start": 0.0,
                    "end": 0.48,
                    "duration_seconds": 0.48,
                    "seconds_per_character": 0.16,
                    "reasons": ["fabricated"],
                }
            ],
        ),
        (
            "low_score_warnings",
            [{"word_index": 0, "text": "Olá", "score": 0.99}],
        ),
    ],
)
def test_normative_binding_rejects_tampered_alignment_quality_report(
    field, replacement
):
    transcript = canonical_transcript()
    transcript["_alano_cut"]["alignment"][field] = replacement

    with pytest.raises(TranscriptContractError, match="does not match transcript"):
        validate_normative_transcript(transcript)


def test_stretched_word_cannot_fake_pass_normative_validation_or_cache():
    transcript = canonical_transcript()
    transcript["words"][-1]["end"] = 5.0
    transcript["segments"][-1]["words"][-1]["end"] = 5.0
    transcript["segments"][-1]["end"] = 5.0

    assert analyze_alignment_quality(transcript)["status"] == "review"
    assert transcript["_alano_cut"]["alignment"]["status"] == "pass"
    with pytest.raises(TranscriptContractError, match="does not match transcript"):
        validate_normative_transcript(transcript)
    assert not is_cache_valid(
        transcript, source_sha256=SOURCE_HASH, config=config()
    )


def test_numeric_notation_is_not_judged_by_written_character_density():
    transcript = canonical_transcript()
    for word in (
        transcript["words"][-1],
        transcript["segments"][-1]["words"][-1],
    ):
        word["text"] = "4.1"
        word["start"] = 2.10
        word["end"] = 3.55
    transcript["segments"][-1]["end"] = 3.55
    transcript["diarization"][-1]["end"] = 4.0

    report = analyze_alignment_quality(transcript)

    assert report["status"] == "pass"
    assert report["blocking_outlier_count"] == 0


def test_stretched_word_with_current_report_still_requires_review():
    transcript = canonical_transcript()
    transcript["words"][-1]["end"] = 5.0
    transcript["segments"][-1]["words"][-1]["end"] = 5.0
    transcript["segments"][-1]["end"] = 5.0
    transcript["_alano_cut"]["alignment"].update(
        analyze_alignment_quality(transcript)
    )

    with pytest.raises(TranscriptContractError, match="quality requires review"):
        validate_normative_transcript(transcript)
    assert not is_cache_valid(
        transcript, source_sha256=SOURCE_HASH, config=config()
    )


def test_word_speaker_wins_and_missing_speaker_uses_maximum_turn_overlap():
    result = aligned_result()
    result["segments"][0]["words"][0]["speaker"] = "SPEAKER_09"
    result["segments"][1]["words"][0].pop("speaker", None)
    turns = [
        {"start": 1.70, "end": 1.92, "speaker": "SPEAKER_03"},
        {"start": 1.92, "end": 2.20, "speaker": "SPEAKER_04"},
    ]

    transcript = convert_whisperx_result(
        result,
        turns,
        config=config(),
        source_sha256=SOURCE_HASH,
    )

    assert transcript["words"][0]["speaker_id"] == "speaker_9"
    assert transcript["words"][2]["speaker_id"] == "speaker_4"


def test_speaker_gap_interpolation_is_bounded_and_unambiguous():
    turns = [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_0"}]
    assert contract._speaker_for_interval(1.10, 1.20, turns) == "speaker_0"
    assert contract._speaker_for_interval(1.30, 1.40, turns) is None

    ambiguous = [
        {"start": 0.0, "end": 1.0, "speaker_id": "speaker_0"},
        {"start": 1.2, "end": 2.0, "speaker_id": "speaker_1"},
    ]
    assert contract._speaker_for_interval(1.05, 1.15, ambiguous) is None


def test_diarization_and_word_speakers_are_mandatory():
    with pytest.raises(TranscriptContractError, match="no diarization turns"):
        convert_whisperx_result(
            aligned_result(), [], config=config(), source_sha256=SOURCE_HASH
        )

    result = aligned_result()
    for segment in result["segments"]:
        for word in segment["words"]:
            word.pop("speaker", None)
    with pytest.raises(TranscriptContractError, match="no diarized speaker"):
        convert_whisperx_result(
            result,
            [{"start": 10.0, "end": 11.0, "speaker": "SPEAKER_00"}],
            config=config(),
            source_sha256=SOURCE_HASH,
        )


def test_accepts_pandas_like_diarization_without_importing_pandas():
    class FrameLike:
        def to_dict(self, orient):
            assert orient == "records"
            return diarization()

    transcript = convert_whisperx_result(
        aligned_result(),
        FrameLike(),
        config=config(),
        source_sha256=SOURCE_HASH,
    )

    assert transcript["diarization"][1]["speaker_id"] == "speaker_1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SPEAKER_00", "speaker_0"),
        ("speaker_001", "speaker_1"),
        ("Speaker 12", "speaker_12"),
        (3, "speaker_3"),
        (None, None),
    ],
)
def test_normalize_speaker_id(value, expected):
    assert normalize_speaker_id(value) == expected


@pytest.mark.parametrize("value", [True, -1, "host", "SPEAKER_A"])
def test_rejects_noncanonical_speaker_labels(value):
    with pytest.raises(TranscriptContractError):
        normalize_speaker_id(value)


@pytest.mark.parametrize("field", ["start", "end"])
def test_missing_word_timing_is_fatal_and_never_dropped(field):
    result = aligned_result()
    result["segments"][0]["words"][1].pop(field)

    with pytest.raises(TranscriptContractError, match="finite number"):
        convert_whisperx_result(
            result,
            diarization(),
            config=config(),
            source_sha256=SOURCE_HASH,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-0.01, 0.5),
        (0.5, 0.5),
        (0.6, 0.5),
        (float("nan"), 0.5),
        (0.2, float("inf")),
    ],
)
def test_word_timestamps_must_be_finite_nonnegative_and_positive_duration(start, end):
    result = aligned_result()
    result["segments"][0]["words"][0].update({"start": start, "end": end})

    with pytest.raises(TranscriptContractError):
        convert_whisperx_result(
            result,
            diarization(),
            config=config(),
            source_sha256=SOURCE_HASH,
        )


def test_light_overlap_is_allowed_but_reverse_order_is_not():
    transcript = canonical_transcript()
    # The fixture already has a 40 ms overlap between Olá and mundo.
    validate_transcript(transcript)

    reversed_words = copy.deepcopy(transcript)
    reversed_words["words"][1]["start"] = -0.001
    reversed_words["segments"][0]["words"][1]["start"] = -0.001
    with pytest.raises(TranscriptContractError):
        validate_transcript(reversed_words)


def test_large_overlap_is_rejected():
    result = aligned_result()
    result["segments"][0]["words"][0]["end"] = 0.90
    result["segments"][0]["words"][1]["start"] = 0.44

    with pytest.raises(TranscriptContractError, match="overlap exceeds"):
        convert_whisperx_result(
            result,
            diarization(),
            config=config(),
            source_sha256=SOURCE_HASH,
        )


def test_source_path_and_declared_hash_must_match(tmp_path):
    source = tmp_path / "clip.wav"
    source.write_bytes(b"audio")

    with pytest.raises(TranscriptContractError, match="does not match"):
        convert_whisperx_result(
            aligned_result(),
            diarization(),
            config=config(),
            source_path=source,
            source_sha256=SOURCE_HASH,
        )


def test_cache_requires_exact_source_config_provider_schema_and_full_validation(tmp_path):
    transcript = canonical_transcript()
    path = tmp_path / "transcript.json"
    write_json_atomic(path, transcript)

    assert is_cache_valid(path, source_sha256=SOURCE_HASH, config=config())
    assert not is_cache_valid(path, source_sha256="b" * 64, config=config())
    assert not is_cache_valid(
        path,
        source_sha256=SOURCE_HASH,
        config=WhisperXConfig(**(config().to_dict() | {"beam_size": 2})),
    )

    stale = copy.deepcopy(transcript)
    stale["_alano_cut"]["schema_version"] = 0
    assert not is_cache_valid(stale, source_sha256=SOURCE_HASH, config=config())

    wrong_provider = copy.deepcopy(transcript)
    wrong_provider["_alano_cut"]["transcription_provider"] = "whisper_cpp"
    assert not is_cache_valid(wrong_provider, source_sha256=SOURCE_HASH, config=config())

    untimed = copy.deepcopy(transcript)
    untimed["words"][0]["timing_source"] = "segment_estimate"
    untimed["segments"][0]["words"][0]["timing_source"] = "segment_estimate"
    assert not is_cache_valid(untimed, source_sha256=SOURCE_HASH, config=config())


def test_validate_rejects_divergent_nested_segment_words():
    transcript = canonical_transcript()
    transcript["segments"][0]["words"][0]["text"] = "Divergiu"

    with pytest.raises(TranscriptContractError, match="do not match"):
        validate_transcript(transcript)


def test_atomic_json_write_round_trips_and_leaves_no_temp(tmp_path):
    destination = tmp_path / "nested" / "transcript.json"
    transcript = canonical_transcript()

    write_json_atomic(destination, transcript)

    assert json.loads(destination.read_text(encoding="utf-8")) == transcript
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_failure_preserves_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "transcript.json"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(contract.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        write_json_atomic(destination, canonical_transcript())

    assert destination.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_unserializable_atomic_payload_never_touches_existing_file(tmp_path):
    destination = tmp_path / "transcript.json"
    destination.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError):
        write_json_atomic(destination, {"bad": float("nan")})

    assert destination.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []
