"""Unit and integration tests for F1.3 Semantic Coverage, Preview Transcript, and Readiness Gate."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import wave
from pathlib import Path
import sys
import numpy as np
import pytest

from helpers.semantic_qc import run_semantic_qc, main as semantic_main
from helpers.preview_transcript_qc import (
    build_report,
    compare_expected,
    MockTranscriptProvider,
    main as preview_transcript_main,
)
import helpers.preview_transcript_qc as preview_transcript_qc
from helpers.verify_edit_ready import main as verify_ready_main


def create_synthetic_wav(path: Path, duration_s: float = 0.5) -> None:
    """Generate a simple synthetic WAV file for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 48000
    channels = 2
    num_samples = int(sample_rate * duration_s)
    t = np.arange(num_samples) / sample_rate
    data = np.sin(2 * np.pi * 440.0 * t) * 10000
    data = data.astype(np.int16)
    samples = np.column_stack((data, data))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())


@pytest.fixture
def temp_workspace(tmp_path):
    """Setup a standard workspace structure with mock inputs."""
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir()

    # Create dummy source files
    create_synthetic_wav(edit_dir / "source1.wav")

    # Create source transcript
    transcript1 = {
        "words": [
            {"text": "welcome", "start": 0.1, "end": 0.4, "type": "word"},
            {"text": "to", "start": 0.4, "end": 0.6, "type": "word"},
            {"text": "the", "start": 0.6, "end": 0.8, "type": "word"},
            {"text": "lesson", "start": 0.8, "end": 1.2, "type": "word"},
        ]
    }
    (transcripts_dir / "source1.json").write_text(json.dumps(transcript1), encoding="utf-8")

    return {
        "root": tmp_path,
        "edit": edit_dir,
        "transcripts": transcripts_dir,
        "source_wav": edit_dir / "source1.wav",
        "source_json": transcripts_dir / "source1.json",
    }


def test_semantic_qc_empty_required_beats(temp_workspace):
    """Test that an empty required_beats list passes schema validation."""
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 1.5}
        ],
        "metadata": {
            "required_beats": []
        }
    }
    edl_path = temp_workspace["edit"] / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "pass"
    assert len(report["beats"]) == 0
    assert "error" not in report


def test_semantic_qc_schema_validation_failures(temp_workspace):
    """Test that invalid beat schemas result in fail status."""
    edl_missing_beats_key = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [],
        "metadata": {}
    }
    edl_path = temp_workspace["edit"] / "edl.json"
    edl_path.write_text(json.dumps(edl_missing_beats_key), encoding="utf-8")

    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "fail"
    assert "required_beats is mandatory" in report["error"]

    # Beat missing id
    edl_bad_schema = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [],
        "metadata": {
            "required_beats": [
                {"description": "Introduction", "evidence_any_of": ["welcome"]}
            ]
        }
    }
    edl_path.write_text(json.dumps(edl_bad_schema), encoding="utf-8")
    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "fail"
    assert "missing a non-empty string 'id'" in report["error"]

    # Beat empty evidence
    edl_empty_evidence = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [],
        "metadata": {
            "required_beats": [
                {"id": "b1", "description": "Intro", "evidence_any_of": []}
            ]
        }
    }
    edl_path.write_text(json.dumps(edl_empty_evidence), encoding="utf-8")
    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "fail"
    assert "empty 'evidence_any_of' list" in report["error"]


def test_semantic_qc_valid_evidence_satisfied(temp_workspace):
    """Test evidence detection where range contains matching word midpoint."""
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5, "beat_id": "b1"}
        ],
        "metadata": {
            "required_beats": [
                {"id": "b1", "description": "Introduction", "evidence_any_of": ["welcome"]}
            ]
        }
    }
    edl_path = temp_workspace["edit"] / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "pass"
    assert report["beats"][0]["satisfied"] is True
    assert report["beats"][0]["matched_evidence"] == "welcome"


def test_semantic_qc_missing_beat_evidence(temp_workspace):
    """Test when ranges do not cover required beats or evidence is missing."""
    # Scenario A: No range references the beat
    edl_no_range = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5}
        ],
        "metadata": {
            "required_beats": [
                {"id": "b1", "description": "Intro", "evidence_any_of": ["welcome"]}
            ]
        }
    }
    edl_path = temp_workspace["edit"] / "edl.json"
    edl_path.write_text(json.dumps(edl_no_range), encoding="utf-8")

    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "review"
    assert report["beats"][0]["satisfied"] is False

    # Scenario B: Range references the beat, but text doesn't contain evidence
    edl_wrong_evidence = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5, "beat_id": "b1"}
        ],
        "metadata": {
            "required_beats": [
                {"id": "b1", "description": "Intro", "evidence_any_of": ["lesson"]}
            ]
        }
    }
    edl_path.write_text(json.dumps(edl_wrong_evidence), encoding="utf-8")

    report = run_semantic_qc(edl_path, temp_workspace["transcripts"])
    assert report["status"] == "review"
    assert report["beats"][0]["satisfied"] is False


def test_preview_transcript_qc_hashing_and_binding(temp_workspace):
    """Verify that WAV SHA-256 and transcript hash are computed and bound."""
    # Create a synthetic WAV file
    preview_wav = temp_workspace["edit"] / "preview.wav"
    create_synthetic_wav(preview_wav, duration_s=1.0)

    transcript_data = {
        "text": "This is a clean synthetic preview transcript.",
        "words": [
            {"text": "This", "start": 0.0, "end": 0.2, "type": "word"},
            {"text": "is", "start": 0.2, "end": 0.4, "type": "word"},
        ]
    }

    report = build_report(
        transcript_data=transcript_data,
        transcript_path=None,
        expected=None,
        cue_terms=[],
        wav_path=preview_wav,
    )

    assert report["preview_wav_hash"] != ""
    assert report["transcript_hash"] != ""
    assert report["summary"]["status"] == "pass"


def test_preview_transcript_qc_content_gates():
    """Verify expected fuzzy score, recall, and missing span triggers."""
    actual_text = "one two three four five six"

    # 1. Fuzzy score failure (< 0.85)
    expected_text_bad_fuzzy = "one two three apple banana cherry orange grape"
    diff_bad_fuzzy = compare_expected(actual_text, expected_text_bad_fuzzy)
    assert diff_bad_fuzzy["similarity"] < 0.85

    # 2. Expected token recall failure (< 0.90)
    # Expected: "one two three four five six seven eight nine ten"
    # Actual: "one two three four five six"
    # Recall = 6 / 10 = 0.60
    expected_text_low_recall = "one two three four five six seven eight nine ten"
    diff_low_recall = compare_expected(actual_text, expected_text_low_recall)
    assert diff_low_recall["recall"] < 0.90

    # 3. Missing span of >= 3 tokens
    # Expected has "apple banana cherry" in the middle, which actual doesn't have.
    expected_text_missing_span = "one two apple banana cherry three four five six"
    diff_missing_span = compare_expected(actual_text, expected_text_missing_span)
    assert "apple banana cherry" in diff_missing_span["missing_spans"]

    # 4. Successful pass
    expected_text_ok = "one two three four five"
    diff_ok = compare_expected(actual_text, expected_text_ok)
    assert diff_ok["similarity"] >= 0.85
    assert diff_ok["recall"] >= 0.90
    assert len(diff_ok["missing_spans"]) == 0


def test_preview_transcript_qc_recording_cues_and_duplicates():
    """Verify that leftover recording cues and duplicates block the gate."""
    transcript_data_cue = {
        "text": "gravando welcome to the lesson",
        "words": [{"text": "gravando", "type": "word", "start": 0.0, "end": 0.5}]
    }
    report_cue = build_report(
        transcript_data=transcript_data_cue,
        transcript_path=None,
        expected=None,
        cue_terms=["gravando"],
    )
    assert report_cue["summary"]["status"] == "review"
    assert "possible_leftover_direction_or_audio_event" in report_cue["summary"]["blocking_flags"]

    transcript_data_dup = {
        "text": "welcome to the lesson. welcome to the lesson.",
        "words": []
    }
    report_dup = build_report(
        transcript_data=transcript_data_dup,
        transcript_path=None,
        expected=None,
        cue_terms=[],
    )
    assert report_dup["summary"]["status"] == "review"
    assert "possible_duplicate_content" in report_dup["summary"]["blocking_flags"]


def test_verify_ready_gate_freshness_and_statuses(temp_workspace, monkeypatch):
    """Test readiness gate exit codes across pass, review, fatal, and stale reports."""
    edit_dir = temp_workspace["edit"]

    # 1. Create a dummy preview WAV & timeline map
    preview_wav = edit_dir / "preview.wav"
    create_synthetic_wav(preview_wav)

    edl_path = edit_dir / "edl.json"
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {
                "source": "source1",
                "start": 0.0,
                "end": 0.5,
                "source_in_frame": 0,
                "source_out_frame": 12,
                "review_required": False
            }
        ],
        "metadata": {
            "required_beats": [],
            "sequence_fps": "24"
        }
    }
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    preview_timeline = edit_dir / "preview_timeline.json"
    valid_map = {
        "edl_hash": edl_hash,
        "output_format": {
            "format": "PCM16",
            "sample_rate": 48000,
            "channels": 2,
            "sequence_fps": 24.0
        },
        "ranges": [
            {
                "source": "source1",
                "source_frames": [0, 12],
                "source_sample_interval": [0, 24000],
                "output_cumulative_sample_interval": [0, 24000],
                "seconds": 0.5,
                "source_channels": 2,
                "channel_policy": "stereo_preserve"
            }
        ]
    }
    preview_timeline.write_text(json.dumps(valid_map), encoding="utf-8")
    map_hash = hashlib.sha256(preview_timeline.read_bytes()).hexdigest()
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()
    map_hash = hashlib.sha256(preview_timeline.read_bytes()).hexdigest()
    trans_hash = hashlib.sha256(temp_workspace["source_json"].read_bytes()).hexdigest()

    # 2. Write four passing and fresh reports
    boundary_qc = {
        "status": "pass",
        "input_edl_hash": "dummy",
        "output_edl_hash": edl_hash,
        "boundary_evidence": [],
        "confidence_summary": {}
    }
    edit_dir.joinpath("edl_boundary_qc.json").write_text(json.dumps(boundary_qc), encoding="utf-8")

    audio_qc = {
        "edl_hash": edl_hash,
        "timeline_map_hash": map_hash,
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc_data = {
        "edl_path": str(edl_path),
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": trans_hash},
        "status": "pass",
        "beats": [],
        "errors": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc_data), encoding="utf-8")

    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "some_hash",
        "summary": {
            "status": "pass",
            "word_count": 10,
            "timed_word_count": 10,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        "words_evidence": [
            {"text": f"word{i}", "type": "word", "start": float(i), "end": float(i) + 0.5}
            for i in range(10)
        ]
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

    # Verify pass case: status=0
    test_argv = [
        "helpers/verify_edit_ready.py",
        str(edl_path),
        "--transcripts", str(temp_workspace["transcripts"]),
        "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
        "--audio-report", str(edit_dir / "preview_audio_qc.json"),
        "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
        "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
        "--audio", str(preview_wav),
        "--timeline-map", str(preview_timeline)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 0

    # 3. Verify review case: status=2 (change audio_qc to review)
    audio_qc["status"] = "review"
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 2

    # Reset audio_qc to pass
    audio_qc["status"] = "pass"
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    # 4. Verify fatal case: status=1 (change semantic_qc_data to fail)
    semantic_qc_data["status"] = "fail"
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc_data), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Reset semantic_qc_data to pass
    semantic_qc_data["status"] = "pass"
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc_data), encoding="utf-8")

    # 5. Verify stale report detection (change EDL file to trigger hash mismatches)
    edl["new_key"] = "changed"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    # Now edl_hash will mismatch in audio and semantic reports
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_preview_transcript_qc_cli_mockable_provider(temp_workspace, monkeypatch):
    """Test preview_transcript_qc.py CLI with --mock-transcript option."""
    edit_dir = temp_workspace["edit"]
    preview_wav = edit_dir / "preview.wav"
    create_synthetic_wav(preview_wav)

    mock_transcript_path = edit_dir / "mock_transcript.json"
    mock_data = {
        "text": "welcome to the test mock",
        "words": []
    }
    mock_transcript_path.write_text(json.dumps(mock_data), encoding="utf-8")

    output_path = edit_dir / "preview_transcript_qc.json"

    test_argv = [
        "helpers/preview_transcript_qc.py",
        str(preview_wav),
        "-o", str(output_path),
        "--mock-transcript", str(mock_transcript_path)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    preview_transcript_main()
    assert output_path.exists()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["text"] == "welcome to the test mock"
    assert report["preview_wav_hash"] != ""
