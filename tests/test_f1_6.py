"""Synthetic unit tests for F1.6 adaptive lexical/acoustic boundary refinement features."""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from helpers.timing import parse_fps_fraction, time_to_frame, frame_to_time
from helpers.audio_analysis import DEFAULT_VAD_PARAMS, EXPECTED_MODEL_HASH
from helpers.refine_edl_boundaries import main as refine_main


@pytest.fixture
def setup_dirs(tmp_path):
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    analysis_dir = edit_dir / "audio_analysis"
    analysis_dir.mkdir()

    source_path = edit_dir / "test_source.wav"
    source_path.write_bytes(b"dummy wav data")

    return edit_dir, transcripts_dir, analysis_dir, source_path


def mock_setup(monkeypatch, channels=1):
    def mock_check_output(cmd, **kwargs):
        if "-version" in cmd:
            return "ffmpeg version 5.0.1"
        if "-filters" in cmd:
            return "Filters:\n  arnndn             A->A       Apply RNNoise filter"
        if "channels" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"channels": channels}]})
        if "r_frame_rate" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"r_frame_rate": "30/1"}]})
        return ""

    def mock_run(cmd, **kwargs):
        if "vfrdet" in str(cmd):
            class Proc:
                stderr = "VFR:0.000000 (0/100)"
            return Proc()
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("subprocess.check_output", mock_check_output)
    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("helpers.audio_analysis.verify_model_hash", lambda p: None)
    monkeypatch.setattr("helpers.refine_edl_boundaries.verify_model_hash", lambda p: None)


def write_dummy_cache(analysis_dir, fingerprint, source_path, raw_energy=1e12, rnn_energy=1e12):
    raw_path = analysis_dir / f"test_source_{fingerprint}_raw.pcm"
    rnn_path = analysis_dir / f"test_source_{fingerprint}_rnn.pcm"
    meta_path = analysis_dir / f"test_source_{fingerprint}_meta.json"

    np.zeros(48000, dtype=np.int16).tofile(raw_path)
    np.zeros(48000, dtype=np.int16).tofile(rnn_path)

    meta = {
        "fingerprint": fingerprint,
        "version": "1.1",
        "model": EXPECTED_MODEL_HASH,
        "params": DEFAULT_VAD_PARAMS,
        "source": {
            "path": str(source_path.resolve()),
            "size": source_path.stat().st_size,
            "mtime": source_path.stat().st_mtime
        },
        "channels": 1,
        "raw_channel_idx": 0,
        "rnn_channel_idx": 0,
        "raw_energy": raw_energy,
        "rnn_energy": rnn_energy
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_independent_sweeps(setup_dirs, monkeypatch):
    """Test 1: Resolve start and end independently. One failed end sweep must not invalidate start."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"},
            {"text": "world", "start": 0.5, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Mock combined activity (agreement)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:140] = True # Covers both hello [40:80] and world [100:140]
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    # Mock VAD hysteresis such that start sweep succeeds (constant onset 40)
    # but end sweep fails (unstable offset: different values)
    vad_call_count = 0
    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        nonlocal vad_call_count
        vad_call_count += 1
        act = np.zeros(200, dtype=bool)
        if vad_call_count in (1, 2, 3, 4):
            # high_t / low_t checks
            act[40:140] = True
        elif vad_call_count == 5:
            # delta high/low sweep
            act[40:120] = True
        elif vad_call_count == 6:
            act[40:130] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.75}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2 # LOW confidence review exit due to end sweep failure

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start sweep was stable, so confidence is high/medium
    assert evidence["confidence"]["start"] in ("high", "medium")
    # End sweep was unstable, so confidence is low
    assert evidence["confidence"]["end"] == "low"

    # Start time should be updated to 0.2 (onset = 40 * 0.005 = 0.2 -> frame 6.0)
    updated_edl = json.loads(edl_path.read_text(encoding="utf-8"))
    assert updated_edl["ranges"][0]["start"] == 0.2
    # End time should remain original 0.75 because end confidence is low
    assert updated_edl["ranges"][0]["end"] == 0.75


def test_lexical_fallback(setup_dirs, monkeypatch):
    """Test 2: Quiet anchor word missed by VAD should use lexical fallback."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"},
            {"text": "world", "start": 0.5, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Mock combined activity where VAD misses "hello" (onset is empty)
    # but finds "world" (offset is active)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[100:140] = True # Misses hello [40:80], only has world [100:140]
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[100:140] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.75}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit as e:
        # If start is fallback (medium) and end is high/medium, it might exit 0
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start should fall back to lexical 0.2 and have medium confidence
    assert evidence["confidence"]["start"] == "medium"
    assert evidence["final_times"]["start"] == 0.2


def test_rejected_neighbor_guarding_cue(setup_dirs, monkeypatch):
    """Test 3: Rejected cue word 'corta' connected to the anchor is guarded."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "corta", "start": 0.1, "end": 0.3, "type": "word"},
            {"text": "manter", "start": 0.35, "end": 0.6, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Both words are in the same acoustic component starting at 0.1s (frame 20)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[20:130] = True # From 0.1s to 0.65s
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[20:130] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    # Edit range selects 'manter' starting at 0.35
    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.33, "end": 0.7}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit as e:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start should be guarded to floor of first_word start (0.35s -> frame 10)
    assert evidence["confidence"]["start"] == "medium"
    assert evidence["final_frames"]["in"] == 10 # 0.333 seconds (floor of 0.35 * 30 is 10)
    # The cue should be excluded
    assert evidence["final_times"]["start"] >= 0.3


def test_rejected_neighbor_guarding_non_cue(setup_dirs, monkeypatch):
    """Test 4: Non-cue rejected word 'casa' connected to anchor remains low/review."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "casa", "start": 0.1, "end": 0.3, "type": "word"},
            {"text": "manter", "start": 0.35, "end": 0.6, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Shared acoustic component
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[20:130] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[20:130] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.33, "end": 0.7}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Non-cue collision cannot be separated defensibly -> return low/review and keep original
    assert evidence["confidence"]["start"] == "low"
    assert evidence["final_times"]["start"] == 0.33


def test_protect_endings(setup_dirs, monkeypatch):
    """Test 5: Never move out-point before the selected last word, and lexical-safe ending."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.5, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # VAD offset is early (e.g. at 0.4s), which is before word end (0.5s)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:80] = True # Active only 0.2s to 0.4s
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[40:80] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.6}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit as e:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # End out-point must not be moved before the last word's end (0.5s)
    # Since VAD was early, the original out-point was 0.6.
    # 0.6 is >= 0.5 (contains last word), doesn't collide with next word (none), and has no clipping.
    # So it should be lexical-safe ending with medium confidence, preserving 0.6.
    assert evidence["confidence"]["end"] == "medium"
    assert evidence["final_times"]["end"] == 0.6


def test_early_silence_trim(setup_dirs, monkeypatch):
    """Test early silence is trimmed using refined VAD onset."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # VAD starts at 0.35s (index 70)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[70:120] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[70:120] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start should be refined to frame 10 (0.333333s) with high confidence
    assert evidence["confidence"]["start"] == "high"
    assert abs(evidence["final_times"]["start"] - 0.333333) < 1e-4


def test_quiet_lexical_fallback_acceptance(setup_dirs, monkeypatch):
    """Test quiet lexical fallback is accepted (medium confidence) if local SNR is good."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    # Two words: hello is quiet (no VAD), world is normal (VAD active)
    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"},
            {"text": "world", "start": 0.5, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # VAD is active during world (0.5 to 0.7 -> index 100 to 140)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[100:140] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[100:140] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.8}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start should fallback to 0.2 (medium) because local metrics are good and there is later speech
    assert evidence["confidence"]["start"] == "medium"
    assert evidence["final_times"]["start"] == 0.2


def test_quiet_lexical_fallback_rejection(setup_dirs, monkeypatch):
    """Test quiet lexical fallback is rejected if local SNR is poor."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # local SNR is extremely low (rms is close to noise floor)
    dummy_rms = np.full(200, -45.0)
    dummy_rms[::2] = -44.0
    dummy_nf = np.full(200, -45.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        return np.zeros(200, dtype=bool)

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Start should be low confidence because local SNR is poor
    assert evidence["confidence"]["start"] == "low"


def test_tight_plosive_sibilant_scoring(setup_dirs, monkeypatch):
    """Test that a component starting slightly outside the lexical boundary (e.g. 20ms before) is successfully scored and selected."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "plosive", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Component starts 20ms before w_start (at 0.18s -> VAD index 36)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[36:80] = True # Overlaps [0.2, 0.4] but starts at 0.18s
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[36:80] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # Selected onset should be 0.18s (index 36) because it overlaps the padded word boundary
    assert evidence["start_side"]["selected_component"]["start"] == 0.18


def test_breath_transient_protection(setup_dirs, monkeypatch):
    """Test that a short transient/breath component (e.g. 50ms) is penalized and not selected over a valid word component."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "word", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # Two active regions:
    # 1. 0.18 - 0.23 (breath, duration 50ms)
    # 2. 0.25 - 0.42 (word, duration 170ms)
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[36:46] = True # 0.18 to 0.23
        act[50:84] = True # 0.25 to 0.42
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[36:46] = True
        act[50:84] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # The word component (0.25 to 0.42) should be selected because the breath component is penalized for being short
    assert evidence["start_side"]["selected_component"]["start"] == 0.25


def test_weak_tail_crossing_cutoff(setup_dirs, monkeypatch):
    """Test that a weak tail crossing the original out-point prevents promoting it to medium confidence."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # original out point is 0.5s. VAD has active tail continuing up to 0.52s.
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:104] = True # Hello, VAD goes up to 104 * 0.005 = 0.52s
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[40:104] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]
    # End confidence must remain low because tail crosses the original out-point (0.5s is active in VAD)
    assert evidence["confidence"]["end"] == "low"


def test_neighbor_frame_limits_guard(setup_dirs, monkeypatch):
    """Test that start/end frame limits strictly guard the neighbors."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "prev", "start": 0.1, "end": 0.3, "type": "word"},
            {"text": "anchor", "start": 0.35, "end": 0.5, "type": "word"},
            {"text": "next", "start": 0.55, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    # VAD covers everything
    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.ones(200, dtype=bool)
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        return np.ones(200, dtype=bool)

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.33, "end": 0.52}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]

    # prev ends at 0.3 -> ceil(0.3 * 30) = 9
    assert evidence["start_side"]["exact_neighbor_frame_limit"] == 9
    # next starts at 0.55 -> floor(0.55 * 30) = 16
    assert evidence["end_side"]["exact_neighbor_frame_limit"] == 16


def test_overlapping_cue_anchor_timestamps(setup_dirs, monkeypatch):
    """Test that overlapping cue/anchor timestamps prevents applying cue guard."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    # Cue and anchor timestamps overlap: prev_end 0.36 > anchor_start 0.35
    transcript_data = {
        "words": [
            {"text": "corta", "start": 0.1, "end": 0.36, "type": "word"},
            {"text": "anchor", "start": 0.35, "end": 0.6, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.ones(200, dtype=bool)
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        return np.ones(200, dtype=bool)

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.38, "end": 0.7}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]

    # Since they overlap, no cue guard is allowed, returns low/review
    assert evidence["confidence"]["start"] == "low"
    assert "overlapping_lexical_intervals" in evidence["start_side"]["rejection_reasons"]


def test_report_evidence_completeness(setup_dirs, monkeypatch):
    """Test that boundary evidence report contains all the new audit schema fields."""
    edit_dir, transcripts_dir, analysis_dir, source_path = setup_dirs
    mock_setup(monkeypatch)

    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.4, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    from helpers.audio_analysis import get_source_fingerprint
    fingerprint = get_source_fingerprint(source_path, EXPECTED_MODEL_HASH, DEFAULT_VAD_PARAMS, "ffmpeg version 5.0.1", transcript_path)
    write_dummy_cache(analysis_dir, fingerprint, source_path)

    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)
    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:80] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    def mock_run_vad(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[40:80] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad)

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.5}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    report_path = edit_dir / "report.json"

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    try:
        refine_main()
    except SystemExit:
        pass

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]

    assert "start_side" in evidence
    assert "end_side" in evidence
    assert "local_metrics" in evidence
    assert "candidates" in evidence["start_side"]
    assert "selected_component" in evidence["start_side"]
    assert "rejection_reasons" in evidence["start_side"]
    assert "sweep_results" in evidence["start_side"]
