"""Regression tests for F1.5 Fail-Closed Pipeline And Rational XML Parity."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import wave
from pathlib import Path
from fractions import Fraction
import numpy as np
import pytest

from helpers.verify_edit_ready import main as verify_ready_main, compute_sha256
from helpers.preview_transcript_qc import build_report, main as preview_transcript_main
from helpers.render import main as render_main
from helpers.edl_to_fcpxml import convert_edl_to_xml, get_timebase_and_ntsc


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
    create_synthetic_wav(edit_dir / "preview.wav")

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
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    return {
        "root": tmp_path,
        "edit": edit_dir,
        "transcripts": transcripts_dir,
        "edl_path": edl_path,
        "source_wav": edit_dir / "source1.wav",
        "preview_wav": edit_dir / "preview.wav",
    }


def test_verify_ready_fail_closed_legacy_boundary_schema(temp_workspace, monkeypatch):
    """Verify that verify_edit_ready.py fails closed with legacy boundary schema."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    # Legacy boundary report (uses high_risk_count/waveform_error_count, lacks boundary_evidence/confidence_summary)
    boundary_qc = {
        "edl": str(edl_path),
        "high_risk_count": 0,
        "waveform_error_count": 0,
        "missing_source_count": 0,
        "results": []
    }
    edit_dir.joinpath("edl_boundary_qc.json").write_text(json.dumps(boundary_qc), encoding="utf-8")

    audio_qc = {
        "edl_hash": edl_hash,
        "timeline_map_hash": "dummy",
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    # Preview transcript report with valid timed-word coverage
    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 10,
            "timed_word_count": 10,
            "timing_coverage": 1.0,
            "blocking_flags": []
        }
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

    test_argv = [
        "helpers/verify_edit_ready.py",
        str(edl_path),
        "--transcripts", str(temp_workspace["transcripts"]),
        "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
        "--audio-report", str(edit_dir / "preview_audio_qc.json"),
        "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
        "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
        "--audio", str(preview_wav),
        "--timeline-map", str(edit_dir / "preview_timeline.json")
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    # It must exit with code 1 due to legacy boundary QC report schema
    assert excinfo.value.code == 1


def test_verify_ready_fail_closed_invalid_edl_fields(temp_workspace, monkeypatch):
    """Verify that verify_edit_ready.py fails closed when EDL is missing source_in_frame, has non-integer, or review_required."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    # Valid refiner boundary QC report
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
        "timeline_map_hash": "dummy",
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 10,
            "timed_word_count": 10,
            "timing_coverage": 1.0,
            "blocking_flags": []
        }
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

    test_argv = [
        "helpers/verify_edit_ready.py",
        str(edl_path),
        "--transcripts", str(temp_workspace["transcripts"]),
        "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
        "--audio-report", str(edit_dir / "preview_audio_qc.json"),
        "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
        "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
        "--audio", str(preview_wav),
        "--timeline-map", str(edit_dir / "preview_timeline.json")
    ]

    # Scenario A: missing source_in_frame
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [{"source": "source1", "start": 0.0, "end": 0.5, "source_out_frame": 12}]
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", test_argv)
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Scenario B: non-integer source_in_frame (float)
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [{"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": 0.0, "source_out_frame": 12}]
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Scenario C: review_required is True
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [{"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": 0, "source_out_frame": 12, "review_required": True}]
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Scenario D: non-positive duration
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [{"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": 12, "source_out_frame": 12}]
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_verify_ready_fail_closed_missing_timed_words(temp_workspace, monkeypatch):
    """Verify that verify_edit_ready.py fails closed when timed_word_count is 0 or timing_coverage is 0.0."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

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
        "timeline_map_hash": "dummy",
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    # Preview transcript report with zero timed-word coverage
    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "review",
            "word_count": 10,
            "timed_word_count": 0,
            "timing_coverage": 0.0,
            "blocking_flags": ["missing_timed_words"]
        }
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

    test_argv = [
        "helpers/verify_edit_ready.py",
        str(edl_path),
        "--transcripts", str(temp_workspace["transcripts"]),
        "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
        "--audio-report", str(edit_dir / "preview_audio_qc.json"),
        "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
        "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
        "--audio", str(preview_wav),
        "--timeline-map", str(edit_dir / "preview_timeline.json")
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_preview_transcript_qc_reports_timed_words():
    """Verify that build_report in preview_transcript_qc.py reports correct word counts and adds blocking flag when empty."""
    # Scenario A: Words with timestamps exist
    transcript_data = {
        "text": "hello world lesson is start",
        "words": [
            {"text": "hello", "type": "word", "start": 0.0, "end": 0.5},
            {"text": "world", "type": "word", "start": 0.5, "end": 1.0},
            {"text": "lesson", "type": "word", "start": 1.0, "end": 1.5},
            {"text": "(cough)", "type": "audio_event"}
        ]
    }
    report = build_report(
        transcript_data=transcript_data,
        transcript_path=None,
        expected=None,
        cue_terms=[],
    )
    summary = report["summary"]
    assert summary["word_count"] == 3
    assert summary["timed_word_count"] == 3
    assert summary["timing_coverage"] == 1.0
    assert "missing_timed_words" not in summary["blocking_flags"]

    # Scenario B: No timed words exist
    transcript_data_no_times = {
        "text": "hello world",
        "words": [
            {"text": "hello", "type": "word"},
            {"text": "world", "type": "word"}
        ]
    }
    report_no_times = build_report(
        transcript_data=transcript_data_no_times,
        transcript_path=None,
        expected=None,
        cue_terms=[],
    )
    summary_no_times = report_no_times["summary"]
    assert summary_no_times["word_count"] == 2
    assert summary_no_times["timed_word_count"] == 0
    assert summary_no_times["timing_coverage"] == 0.0
    assert "missing_timed_words" in summary_no_times["blocking_flags"]
    assert summary_no_times["status"] == "review"


def test_render_requires_exact_source_frames(temp_workspace, monkeypatch):
    """Verify render.py requires exact source frames unless fallback is explicitly requested."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]

    # Modify EDL to omit source_in_frame
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5} # missing source frames
        ],
        "metadata": {"sequence_fps": "24"}
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")

    output_wav = edit_dir / "preview_out.wav"
    map_json = edit_dir / "preview_timeline_out.json"

    # Automated path (no fallback flag): must exit with 1
    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)
    with pytest.raises(SystemExit) as excinfo:
        render_main()
    assert excinfo.value.code == 1

    # Manual path (with fallback flag): must succeed
    test_argv_fallback = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json),
        "--allow-manual-fallback"
    ]
    monkeypatch.setattr(sys, "argv", test_argv_fallback)
    render_main()
    assert output_wav.exists()
    assert map_json.exists()


def test_xml_rational_ntsc_mapping():
    """Verify NTSC mapping is correct for exact vs fractional frame rates."""
    # 24000/1001, 30000/1001, 60000/1001 must be TRUE
    tb, ntsc = get_timebase_and_ntsc(Fraction(24000, 1001))
    assert tb == 24 and ntsc == "TRUE"

    tb, ntsc = get_timebase_and_ntsc(Fraction(30000, 1001))
    assert tb == 30 and ntsc == "TRUE"

    tb, ntsc = get_timebase_and_ntsc(Fraction(60000, 1001))
    assert tb == 60 and ntsc == "TRUE"

    # Exact 24/25/30/60 must be FALSE
    tb, ntsc = get_timebase_and_ntsc(Fraction(24, 1))
    assert tb == 24 and ntsc == "FALSE"

    tb, ntsc = get_timebase_and_ntsc(Fraction(25, 1))
    assert tb == 25 and ntsc == "FALSE"

    tb, ntsc = get_timebase_and_ntsc(Fraction(30, 1))
    assert tb == 30 and ntsc == "FALSE"

    tb, ntsc = get_timebase_and_ntsc(Fraction(60, 1))
    assert tb == 60 and ntsc == "FALSE"


def test_xml_sequence_fps_authority_wav_only(temp_workspace):
    """Verify that WAV-only sources with sequence_fps 30 generates 30/FALSE XML."""
    edit_dir = temp_workspace["edit"]

    # Write EDL with WAV source and sequence_fps 30
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {
                "source": "source1",
                "start": 0.0,
                "end": 1.0,
                "source_in_frame": 0,
                "source_out_frame": 30,
                "review_required": False
            }
        ],
        "metadata": {
            "sequence_fps": "30"
        }
    }
    edl_path = edit_dir / "edl_wav_30.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    xml_path = edit_dir / "timeline_wav_30.xml"

    # Run convert_edl_to_xml
    convert_edl_to_xml(edl_path, xml_path)

    assert xml_path.exists()
    xml_content = xml_path.read_text(encoding="utf-8")

    # Assert timebase is 30, NTSC is FALSE
    assert "<timebase>30</timebase>" in xml_content
    assert "<ntsc>FALSE</ntsc>" in xml_content
    assert "<duration>30</duration>" in xml_content


def test_render_divergent_metadata_probe(temp_workspace, monkeypatch, capsys):
    """Verify render.py fails when probed video frame rate diverges from metadata authority."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]

    # Create a dummy .mp4 file so it's not .wav and triggers the probe
    mp4_file = edit_dir / "source1.mp4"
    mp4_file.write_text("dummy video content")

    edl = {
        "version": 1,
        "sources": {"source1": "source1.mp4"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": 0, "source_out_frame": 12}
        ],
        "metadata": {
            "sequence_fps": "30" # Authority is 30
        }
    }
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    import subprocess
    # Mock subprocess.check_output to return probed FPS 24/1 for authority 30/1
    def mock_check_output(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd)
        if "r_frame_rate" in cmd_str:
            return json.dumps({"streams": [{"r_frame_rate": "24/1"}]})
        if "channels" in cmd_str:
            return json.dumps({"streams": [{"channels": 2}]})
        raise FileNotFoundError(f"Mock command not handled: {cmd_str}")

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    output_wav = edit_dir / "preview_out.wav"
    map_json = edit_dir / "preview_timeline_out.json"

    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        render_main()
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    assert "does not match sequence authority FPS 30" in captured.err


def test_render_invalid_frame_types(temp_workspace, monkeypatch):
    """Verify render.py fails on invalid frame boundaries."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]

    output_wav = edit_dir / "preview_out.wav"
    map_json = edit_dir / "preview_timeline_out.json"

    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    invalid_frames = [
        (0.5, 12),      # Float
        ("0", 12),      # String
        (True, 12),     # Bool
        (-5, 12),       # Negative
        (12, 10),       # Out <= In
    ]

    for in_f, out_f in invalid_frames:
        edl = {
            "version": 1,
            "sources": {"source1": "source1.wav"},
            "ranges": [
                {"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": in_f, "source_out_frame": out_f}
            ],
            "metadata": {"sequence_fps": "24"}
        }
        edl_path.write_text(json.dumps(edl), encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            render_main()
        assert excinfo.value.code == 1


def test_verify_ready_invalid_map_or_empty_edl(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails on invalid timeline map or empty EDL ranges."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]

    # Write normal reports first
    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

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
        "timeline_map_hash": "dummy",
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 1,
            "timed_word_count": 1,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        "words_evidence": [{"text": "welcome", "type": "word", "start": 0.1, "end": 0.4}]
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

    test_argv = [
        "helpers/verify_edit_ready.py",
        str(edl_path),
        "--transcripts", str(temp_workspace["transcripts"]),
        "--boundary-report", str(edit_dir / "edl_boundary_qc.json"),
        "--audio-report", str(edit_dir / "preview_audio_qc.json"),
        "--semantic-report", str(edit_dir / "edl_semantic_qc.json"),
        "--transcript-report", str(edit_dir / "preview_transcript_qc.json"),
        "--audio", str(preview_wav),
        "--timeline-map", str(edit_dir / "preview_timeline.json")
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    # 1. Map missing
    if edit_dir.joinpath("preview_timeline.json").exists():
        edit_dir.joinpath("preview_timeline.json").unlink()
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # 2. Map invalid JSON
    edit_dir.joinpath("preview_timeline.json").write_text("invalid json")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Restore map
    edit_dir.joinpath("preview_timeline.json").write_text(json.dumps({"timeline_map_hash": "dummy"}))

    # 3. EDL empty ranges
    bad_edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": []
    }
    edl_path.write_text(json.dumps(bad_edl), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_verify_ready_unknown_status(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails when a report has an unknown or missing status."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]
    preview_timeline = edit_dir / "preview_timeline.json"
    preview_timeline.write_text("{}")

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    # Valid base reports
    boundary_qc = {
        "status": "pass",
        "input_edl_hash": "dummy",
        "output_edl_hash": edl_hash,
        "boundary_evidence": [],
        "confidence_summary": {}
    }
    audio_qc = {
        "edl_hash": edl_hash,
        "timeline_map_hash": compute_sha256(preview_timeline),
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 1,
            "timed_word_count": 1,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        "words_evidence": [{"text": "welcome", "type": "word", "start": 0.1, "end": 0.4}]
    }

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

    # Scenario A: Audio status is missing
    del audio_qc["status"]
    edit_dir.joinpath("edl_boundary_qc.json").write_text(json.dumps(boundary_qc))
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc))
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc))
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc))
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Restore status to invalid "unknown"
    audio_qc["status"] = "unknown"
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc))
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_verify_ready_adulterated_evidence(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails when words_evidence is missing or differs from summary counts."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]
    preview_timeline = edit_dir / "preview_timeline.json"
    preview_timeline.write_text("{}")

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    boundary_qc = {
        "status": "pass",
        "input_edl_hash": "dummy",
        "output_edl_hash": edl_hash,
        "boundary_evidence": [],
        "confidence_summary": {}
    }
    audio_qc = {
        "edl_hash": edl_hash,
        "timeline_map_hash": compute_sha256(preview_timeline),
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }

    # Verifiable generated transcript QC report
    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 2,
            "timed_word_count": 2,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        # Adulterated words_evidence (has 3 words instead of 2!)
        "words_evidence": [
            {"text": "welcome", "type": "word", "start": 0.1, "end": 0.4},
            {"text": "to", "type": "word", "start": 0.4, "end": 0.6},
            {"text": "lesson", "type": "word", "start": 0.6, "end": 1.0}
        ]
    }

    edit_dir.joinpath("edl_boundary_qc.json").write_text(json.dumps(boundary_qc))
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc))
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc))
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc))

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

    # Recalculated counts will be 3, declared is 2. Must fail fatal.
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Missing words_evidence entirely
    del transcript_qc["words_evidence"]
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc))
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_xml_rational_30000_1001_mapping_parity(temp_workspace, monkeypatch):
    """Verify multi-range 30000/1001 parity between timeline map source_frames/samples and XML in/out/duration."""
    edit_dir = temp_workspace["edit"]

    # Create two synthetic sources
    create_synthetic_wav(edit_dir / "video1.wav", duration_s=5.0)
    create_synthetic_wav(edit_dir / "video2.wav", duration_s=5.0)

    # EDL sequence_fps is 30000/1001 (approx 29.97002997)
    edl = {
        "version": 1,
        "sources": {
            "video1": "video1.wav",
            "video2": "video2.wav"
        },
        "ranges": [
            {
                "source": "video1",
                "start": 0.0,
                "end": 2.0,
                "source_in_frame": 10,
                "source_out_frame": 70,
                "review_required": False
            },
            {
                "source": "video2",
                "start": 1.0,
                "end": 3.0,
                "source_in_frame": 30,
                "source_out_frame": 90,
                "review_required": False
            }
        ],
        "metadata": {
            "sequence_fps": "30000/1001"
        }
    }
    edl_path = edit_dir / "edl_parity.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    # Render it to produce the WAV and timeline map
    output_wav = edit_dir / "preview_parity.wav"
    map_json = edit_dir / "preview_timeline_parity.json"

    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)
    render_main()

    assert output_wav.exists()
    assert map_json.exists()

    # Read timeline map
    timeline_map = json.loads(map_json.read_text(encoding="utf-8"))

    # Convert EDL to XML
    xml_path = edit_dir / "timeline_parity.xml"
    convert_edl_to_xml(edl_path, xml_path)

    assert xml_path.exists()
    xml_content = xml_path.read_text(encoding="utf-8")

    # Parse XML content to verify in/out/duration match exactly
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_content)

    sequence_duration = int(root.find(".//sequence/duration").text)
    timebase = int(root.find(".//sequence/rate/timebase").text)
    ntsc = root.find(".//sequence/rate/ntsc").text

    assert timebase == 30
    assert ntsc == "TRUE"

    # Recalculate duration from timeline map:
    # First range duration: 70 - 10 = 60 frames
    # Second range duration: 90 - 30 = 60 frames
    # Cumulative duration = 120 frames
    assert sequence_duration == 120

    # Verify each clipitem in/out/start/end/duration
    clipitems_v = root.findall(".//video//clipitem")
    assert len(clipitems_v) == 2

    # Clip 1
    assert int(clipitems_v[0].find("in").text) == 10
    assert int(clipitems_v[0].find("out").text) == 70
    assert int(clipitems_v[0].find("start").text) == 0
    assert int(clipitems_v[0].find("end").text) == 60
    assert int(clipitems_v[0].find("duration").text) == 60

    # Clip 2
    assert int(clipitems_v[1].find("in").text) == 30
    assert int(clipitems_v[1].find("out").text) == 90
    assert int(clipitems_v[1].find("start").text) == 60
    assert int(clipitems_v[1].find("end").text) == 120
    assert int(clipitems_v[1].find("duration").text) == 60


def test_verify_ready_timeline_map_contract_divergences(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails on empty map or mismatched hash, FPS, ranges, frames, and intervals."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]
    preview_timeline = edit_dir / "preview_timeline.json"

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

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
        "timeline_map_hash": "dummy",
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 1,
            "timed_word_count": 1,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        "words_evidence": [{"text": "welcome", "type": "word", "start": 0.1, "end": 0.4}]
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

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

    # 1. Map is empty {}
    preview_timeline.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # Base valid map contents matching EDL
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

    # 2. EDL hash mismatch
    bad_map = dict(valid_map)
    bad_map["edl_hash"] = "wrong_hash"
    preview_timeline.write_text(json.dumps(bad_map), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # 3. FPS mismatch
    bad_map = json.loads(json.dumps(valid_map))
    bad_map["output_format"]["sequence_fps"] = 30.0
    preview_timeline.write_text(json.dumps(bad_map), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # 4. Ranges length mismatch
    bad_map = json.loads(json.dumps(valid_map))
    bad_map["ranges"] = []
    preview_timeline.write_text(json.dumps(bad_map), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # 5. Ranges source_frames mismatch
    bad_map = json.loads(json.dumps(valid_map))
    bad_map["ranges"][0]["source_frames"] = [0, 10]
    preview_timeline.write_text(json.dumps(bad_map), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1

    # 6. Cumulative interval not consecutive
    bad_map = json.loads(json.dumps(valid_map))
    bad_map["ranges"][0]["output_cumulative_sample_interval"] = [100, 24100]
    preview_timeline.write_text(json.dumps(bad_map), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        verify_ready_main()
    assert excinfo.value.code == 1


def test_verify_ready_word_timestamp_invalidations(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails on invalid word timestamps (string, NaN, negative, start>=end, out-of-order)."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]
    preview_timeline = edit_dir / "preview_timeline.json"

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

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
        "timeline_map_hash": compute_sha256(preview_timeline),
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    # Scenarios for invalid words_evidence
    invalid_evidence_scenarios = [
        # string timestamps
        [{"text": "w", "type": "word", "start": "0.1", "end": 0.4}],
        # NaN/inf timestamps
        [{"text": "w", "type": "word", "start": float("nan"), "end": 0.4}],
        [{"text": "w", "type": "word", "start": 0.1, "end": float("inf")}],
        # negative start
        [{"text": "w", "type": "word", "start": -0.1, "end": 0.4}],
        # start >= end
        [{"text": "w", "type": "word", "start": 0.5, "end": 0.4}],
        # out of order (desordenados)
        [
            {"text": "w1", "type": "word", "start": 0.5, "end": 0.8},
            {"text": "w2", "type": "word", "start": 0.2, "end": 0.4}
        ]
    ]

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

    for scenario in invalid_evidence_scenarios:
        transcript_qc = {
            "transcript": "generated",
            "preview_wav_hash": wav_hash,
            "transcript_hash": "dummy",
            "summary": {
                "status": "pass",
                "word_count": len(scenario),
                "timed_word_count": len(scenario),
                "timing_coverage": 1.0,
                "blocking_flags": []
            },
            "words_evidence": scenario
        }
        edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            verify_ready_main()
        assert excinfo.value.code == 1


def test_verify_ready_negative_frames(temp_workspace, monkeypatch):
    """Verify verify_edit_ready.py fails on negative frames in EDL or timeline map."""
    edit_dir = temp_workspace["edit"]
    edl_path = temp_workspace["edl_path"]
    preview_wav = temp_workspace["preview_wav"]
    preview_timeline = edit_dir / "preview_timeline.json"

    # 1. Negative in EDL range
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {
                "source": "source1",
                "start": 0.0,
                "end": 0.5,
                "source_in_frame": -5,  # negative
                "source_out_frame": 12,
                "review_required": False
            }
        ],
        "metadata": {"sequence_fps": "24"}
    }
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    edl_hash = hashlib.sha256(edl_path.read_bytes()).hexdigest()
    wav_hash = hashlib.sha256(preview_wav.read_bytes()).hexdigest()

    boundary_qc = {
        "status": "pass",
        "input_edl_hash": "dummy",
        "output_edl_hash": edl_hash,
        "boundary_evidence": [],
        "confidence_summary": {}
    }
    edit_dir.joinpath("edl_boundary_qc.json").write_text(json.dumps(boundary_qc), encoding="utf-8")

    # Map with matching negative source frames
    map_data = {
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
                "source_frames": [-5, 12],
                "source_sample_interval": [-10000, 24000],
                "output_cumulative_sample_interval": [0, 34000],
                "seconds": 0.5,
                "source_channels": 2,
                "channel_policy": "stereo_preserve"
            }
        ]
    }
    preview_timeline.write_text(json.dumps(map_data), encoding="utf-8")

    audio_qc = {
        "edl_hash": edl_hash,
        "timeline_map_hash": compute_sha256(preview_timeline),
        "preview_wav_hash": wav_hash,
        "status": "pass"
    }
    edit_dir.joinpath("preview_audio_qc.json").write_text(json.dumps(audio_qc), encoding="utf-8")

    semantic_qc = {
        "edl_hash": edl_hash,
        "transcript_hashes": {"source1": "dummy"},
        "status": "pass",
        "beats": []
    }
    edit_dir.joinpath("edl_semantic_qc.json").write_text(json.dumps(semantic_qc), encoding="utf-8")

    transcript_qc = {
        "transcript": "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": "dummy",
        "summary": {
            "status": "pass",
            "word_count": 1,
            "timed_word_count": 1,
            "timing_coverage": 1.0,
            "blocking_flags": []
        },
        "words_evidence": [{"text": "welcome", "type": "word", "start": 0.1, "end": 0.4}]
    }
    edit_dir.joinpath("preview_transcript_qc.json").write_text(json.dumps(transcript_qc), encoding="utf-8")

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
    assert excinfo.value.code == 1


def test_xml_rational_non_canonical_ntsc_false():
    """Verify that non-canonical frame rates close to NTSC values (e.g. 23.98 or 29.98) return NTSC FALSE."""
    # Close but not equal to 24000/1001 (approx 23.976)
    tb, ntsc = get_timebase_and_ntsc(Fraction(2398, 100))
    assert tb == 24
    assert ntsc == "FALSE"

    # Close but not equal to 30000/1001 (approx 29.97)
    tb, ntsc = get_timebase_and_ntsc(Fraction(2997, 100))
    assert tb == 30
    assert ntsc == "FALSE"
