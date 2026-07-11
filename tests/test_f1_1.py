"""Unit and integration tests for F1.1 timing, audio analysis, and EDL refinement."""

from __future__ import annotations

import json
import os
import shutil
import wave
from fractions import Fraction
from pathlib import Path
import sys

import numpy as np
import pytest

from helpers.timing import parse_fps_fraction, time_to_frame, frame_to_time
from helpers.audio_analysis import (
    verify_model_hash,
    compute_rms_db,
    compute_noise_floor_db,
    run_vad_hysteresis,
    align_signals,
    build_word_mask,
    get_combined_activity,
    EXPECTED_MODEL_HASH,
)
from helpers.refine_edl_boundaries import main as refine_main, write_atomic

# 1. Rational frame math tests
def test_rational_frame_math():
    # check parsing
    assert parse_fps_fraction(30) == Fraction(30, 1)
    assert parse_fps_fraction("24000/1001") == Fraction(24000, 1001)
    assert parse_fps_fraction(23.976) == Fraction(24000, 1001)
    assert parse_fps_fraction("29.97") == Fraction(30000, 1001)

    fps = Fraction(30000, 1001)
    # floor onset
    assert time_to_frame(1.0, fps, "floor") == 29
    # ceil offset + 2
    assert time_to_frame(2.0, fps, "ceil") + 2 == 62

    # conversion back to time
    assert abs(frame_to_time(29, fps) - 29 * 1001 / 30000) < 1e-6

# 2. Raw model/hash preflight tests
def test_model_hash_preflight(tmp_path):
    model_file = tmp_path / "cb.rnnn"

    # Missing model
    with pytest.raises(FileNotFoundError):
        verify_model_hash(model_file)

    # Wrong hash
    model_file.write_bytes(b"some invalid model content")
    with pytest.raises(ValueError) as excinfo:
        verify_model_hash(model_file)
    assert "Model SHA-256 mismatch" in str(excinfo.value)

    # Correct hash
    real_model = Path(__file__).parent.parent / "helpers" / "models" / "cb.rnnn"
    if real_model.exists():
        verify_model_hash(real_model)

# 3. Local word-excluded noise floor tests
def test_word_excluded_noise_floor():
    # 200 VAD frames (1s) of RMS values
    # Put noise at -50dB and a loud speech segment from index 50 to 150 at -10dB.
    rms = np.full(200, -50.0)
    rms[50:150] = -10.0

    # With a small window_s (e.g. 0.4s -> ±0.2s -> ±40 frames)
    # At frame 100, the window is [60, 141], all -10dB. Without word exclusion, noise floor at 100 would be -10dB.
    noise_floor_no_exclude = compute_noise_floor_db(rms, hop_size_ms=5, window_s=0.4, word_mask=None)
    assert noise_floor_no_exclude[100] == -10.0

    # With word exclusion: word mask covers frames [50:150].
    # At frame 60, the window is [20, 101]. Non-word frames are [20:50], which are -50dB.
    word_mask = np.zeros(200, dtype=bool)
    word_mask[50:150] = True
    noise_floor_exclude = compute_noise_floor_db(rms, hop_size_ms=5, window_s=0.4, word_mask=word_mask)
    assert noise_floor_exclude[60] == -50.0

# 4. Hysteresis, gap fill and transient protection VAD logic
def test_vad_hysteresis_and_protection():
    # Speech segment from 10 to 30.
    # Silence gap from 30 to 34 (4 frames -> 20ms). Should be filled.
    # Speech segment from 34 to 50.
    # Short transient from 80 to 85 (5 frames -> 25ms).
    # This transient is NOT followed by speech within 60ms. Should be discarded.
    # Short transient from 120 to 125 (5 frames -> 25ms).
    # Followed by speech segment from 135 to 160 (gap is 10 frames -> 50ms <= 60ms).
    # Both transient and speech should be kept.

    rms = np.full(200, -50.0)
    rms[10:30] = -15.0
    rms[34:50] = -15.0
    rms[80:85] = -15.0
    rms[120:125] = -15.0
    rms[135:160] = -15.0

    noise_floor = np.full(200, -50.0)

    activity = run_vad_hysteresis(
        rms_db=rms,
        noise_floor_db=noise_floor,
        threshold_high_db=12.0, # active threshold: -38dB
        threshold_low_db=3.0,   # inactive threshold: -47dB
        gap_fill_frames=8,      # fill <= 40ms (8 frames)
        transient_protection_frames=12 # protect >= 60ms (12 frames)
    )

    # 1. Gap [30:34] should be filled
    assert np.all(activity[10:50])

    # 2. First transient [80:85] should be discarded
    assert not np.any(activity[80:85])

    # 3. Second transient [120:125] followed by speech [135:160] (gap 50ms) should be protected
    assert np.all(activity[120:125])
    assert np.all(activity[135:160])

# 5. Lag and correlation alignment
def test_signal_alignment():
    rms_raw = np.full(100, -60.0)
    rms_raw[20:40] = -10.0

    # RNNoise delayed by 3 frames (15ms)
    rms_rnn = np.full(100, -60.0)
    rms_rnn[23:43] = -10.0

    lag = align_signals(rms_raw, rms_rnn, max_lag_frames=6)
    assert lag == -3

# 6. Atomic safety and exclusive backup writes
def test_atomic_writes_and_backup(tmp_path):
    dest = tmp_path / "edl.json"
    dest.write_text("original content", encoding="utf-8")

    # Successful write_atomic
    write_atomic(dest, "new content")
    assert dest.read_text(encoding="utf-8") == "new content"

    # Failed write_atomic: verify target is unchanged on failure
    bad_dest = tmp_path / "some_dir"
    bad_dest.mkdir()
    with pytest.raises(Exception):
        write_atomic(bad_dest, "failed content")
    # Verify that the directory still exists and wasn't replaced/deleted
    assert bad_dest.is_dir()

# 7. Refiner integration, preflight rejections and idempotence
def test_refiner_integration(tmp_path, monkeypatch):
    # Setup directories
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    model_dir = tmp_path / "helpers" / "models"
    model_dir.mkdir(parents=True)
    (model_dir / "cb.rnnn").write_bytes(b"dummy model content")

    # Create two source files (WAV)
    source1_path = edit_dir / "source1.wav"
    source2_path = edit_dir / "source2.wav"

    # Create mono 16-bit PCM wav files
    for p in [source1_path, source2_path]:
        with wave.open(str(p), "wb") as w:
            w.setnchannels(2) # 2 channels to test maximum-energy channel selection!
            w.setsampwidth(2)
            w.setframerate(48000)
            # 1 second of audio
            samples = np.zeros(96000 * 2, dtype=np.int16)
            # Channel 0 has sound, Channel 1 is quiet
            samples[::2] = 5000
            w.writeframes(samples.tobytes())

    # Transcripts
    transcript1 = {
        "words": [
            {"text": "hello", "start": 0.1, "end": 0.4, "type": "word"},
            {"text": "world", "start": 0.5, "end": 0.8, "type": "word"}
        ]
    }
    transcript2 = {
        "words": [
            {"text": "test", "start": 0.2, "end": 0.7, "type": "word"}
        ]
    }
    (transcripts_dir / "source1.json").write_text(json.dumps(transcript1), encoding="utf-8")
    (transcripts_dir / "source2.json").write_text(json.dumps(transcript2), encoding="utf-8")

    # EDL
    edl = {
        "version": 1,
        "sources": {
            "source1": "source1.wav",
            "source2": "source2.wav"
        },
        "ranges": [
            {
                "source": "source1",
                "start": 0.05,
                "end": 0.85,
                "unknown_key": "preserved"
            }
        ],
        "metadata": {
            "sequence_fps": 30.0
        }
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    report_path = edit_dir / "report.json"

    # Subprocess / FFmpeg Mocks
    def mock_check_output(cmd, **kwargs):
        if "-version" in cmd:
            return "ffmpeg version 5.0.1-essentials_build-www.gyan.dev"
        if "-filters" in cmd:
            return "Filters:\n  arnndn             A->A       Apply RNNoise filter"
        if "channels" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"channels": 2}]})
        if "r_frame_rate" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"r_frame_rate": "30/1"}]})
        return ""

    def mock_run(cmd, **kwargs):
        if "arnndn" in str(cmd):
            in_file = cmd[cmd.index("-i") + 1]
            out_file = cmd[-1]
            shutil.copy(in_file, out_file)
        elif "-acodec" in cmd:
            out_file = cmd[-1]
            # Write 48000 samples of stereo s16le (192KB)
            data = np.zeros(48000 * 2, dtype=np.int16)
            data[::2] = 5000
            Path(out_file).write_bytes(data.tobytes())
        elif "vfrdet" in str(cmd):
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

    # Run the main refiner CLI
    test_argv = [
        "helpers/refine_edl_boundaries.py",
        str(edl_path),
        "--transcripts", str(transcripts_dir),
        "--report", str(report_path)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    # Run refiner main
    try:
        refine_main()
    except SystemExit as e:
        assert e.code in (0, 2)

    # Check that backup exists
    backups = list((edit_dir / "backups").glob("edl.*.json"))
    assert len(backups) == 1

    # Check that unknown_key is preserved
    updated_edl = json.loads(edl_path.read_text(encoding="utf-8"))
    assert updated_edl["ranges"][0]["unknown_key"] == "preserved"
    assert "source_in_frame" in updated_edl["ranges"][0]
    assert "source_out_frame" in updated_edl["ranges"][0]

    # Check idempotency: run again
    try:
        refine_main()
    except SystemExit as e:
        assert e.code in (0, 2)

    second_updated_edl = json.loads(edl_path.read_text(encoding="utf-8"))
    assert updated_edl == second_updated_edl


# 8. Cache metadata validation unit test
from helpers.audio_analysis import validate_cache_metadata

def test_cache_metadata_validation(tmp_path):
    meta_path = tmp_path / "meta.json"
    source_path = tmp_path / "source.wav"
    source_path.write_text("content", encoding="utf-8")
    stat = source_path.stat()

    params = {"sample_rate": 48000}
    meta_data = {
        "fingerprint": "fp123",
        "version": "1.1",
        "model": "model123",
        "params": params,
        "source": {
            "path": str(source_path.resolve()),
            "size": stat.st_size,
            "mtime": stat.st_mtime
        }
    }

    meta_path.write_text(json.dumps(meta_data), encoding="utf-8")

    # Valid metadata
    assert validate_cache_metadata(meta_path, "fp123", "1.1", params, "model123", source_path) is True

    # Fingerprint mismatch
    assert validate_cache_metadata(meta_path, "wrong_fp", "1.1", params, "model123", source_path) is False

    # Version mismatch
    assert validate_cache_metadata(meta_path, "fp123", "1.2", params, "model123", source_path) is False

    # Model mismatch
    assert validate_cache_metadata(meta_path, "fp123", "1.1", params, "wrong_model", source_path) is False

    # Params mismatch
    assert validate_cache_metadata(meta_path, "fp123", "1.1", {"sample_rate": 44100}, "model123", source_path) is False

    # Source path mismatch
    bad_source_path = tmp_path / "other.wav"
    assert validate_cache_metadata(meta_path, "fp123", "1.1", params, "model123", bad_source_path) is False


# 9. Comprehensive refiner confidence scoring tests (High, Medium, Low, SNR, correlation, tail, no-anchor)
def test_refiner_confidence_levels(tmp_path, monkeypatch):
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    analysis_dir = edit_dir / "audio_analysis"
    analysis_dir.mkdir()

    # Create dummy source wav
    source_path = edit_dir / "test_source.wav"
    source_path.write_bytes(b"dummy wav data")

    # Subprocess / FFmpeg Mocks
    def mock_check_output(cmd, **kwargs):
        if "-version" in cmd:
            return "ffmpeg version 5.0.1"
        if "-filters" in cmd:
            return "Filters:\n  arnndn             A->A       Apply RNNoise filter"
        if "channels" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"channels": 1}]})
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

    from helpers.audio_analysis import get_source_fingerprint, DEFAULT_VAD_PARAMS, EXPECTED_MODEL_HASH

    # Transcript with words
    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    fingerprint = get_source_fingerprint(
        source_path,
        EXPECTED_MODEL_HASH,
        DEFAULT_VAD_PARAMS,
        "ffmpeg version 5.0.1",
        transcript_path
    )

    # Helper to generate PCM with seeded generator for determinism
    def gen_pcm_data(start_s, end_s, noise_std=5, speech_std=10000, lag_samples=0, seed=42):
        sr = 48000
        rng = np.random.default_rng(seed)
        samples = rng.normal(0, noise_std, 48000)
        t = np.arange(int((end_s - start_s) * sr))
        speech = np.sin(2 * np.pi * 1000 * t / sr) * speech_std
        start_idx = int(start_s * sr)
        samples[start_idx:start_idx + len(speech)] += speech
        if lag_samples != 0:
            samples = np.roll(samples, lag_samples)
        return samples.astype(np.int16)

    def write_cache_files(raw_pcm, rnn_pcm):
        raw_path = analysis_dir / f"test_source_{fingerprint}_raw.pcm"
        rnn_path = analysis_dir / f"test_source_{fingerprint}_rnn.pcm"
        meta_path = analysis_dir / f"test_source_{fingerprint}_meta.json"

        raw_pcm.tofile(raw_path)
        rnn_pcm.tofile(rnn_path)

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
            "raw_energy": float(np.sum(raw_pcm.astype(np.float64) ** 2)),
            "rnn_energy": float(np.sum(rnn_pcm.astype(np.float64) ** 2))
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # A. Test HIGH confidence
    raw_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10000, lag_samples=0)
    rnn_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10000, lag_samples=0)
    write_cache_files(raw_pcm, rnn_pcm)

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
        assert e.code == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "high"
    assert evidence["confidence"]["end"] == "high"

    # B. Test MEDIUM confidence (slightly offset RNNoise by 2 VAD frames = 10ms = 480 samples)
    raw_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10000, lag_samples=0)
    rnn_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10000, lag_samples=480)
    write_cache_files(raw_pcm, rnn_pcm)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 0  # Should still exit 0 because medium is approved!

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] in ("high", "medium")
    assert evidence["confidence"]["end"] in ("high", "medium")

    # C. Test LOW confidence due to Low SNR (< 8 dB)
    raw_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10, lag_samples=0)
    rnn_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10, lag_samples=0)
    write_cache_files(raw_pcm, rnn_pcm)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2  # Low confidence review exit

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "review"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "low"
    assert evidence["confidence"]["end"] == "low"

    # D. Test LOW confidence due to Low Correlation (< 0.8)
    raw_pcm = gen_pcm_data(0.2, 0.7, noise_std=5, speech_std=10000, lag_samples=0)
    rng_d = np.random.default_rng(42)
    rnn_pcm = rng_d.normal(0, 5, 48000).astype(np.int16) # just noise
    write_cache_files(raw_pcm, rnn_pcm)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "review"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "low"
    assert evidence["confidence"]["end"] == "low"

    # E. Test NO-ANCHOR case
    edl_no_anchor = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.8, "end": 0.95}]
    }
    edl_path.write_text(json.dumps(edl_no_anchor), encoding="utf-8")

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "review"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "low"
    assert evidence["confidence"]["end"] == "low"
    assert evidence["anchor_words"] is None


# 10. Test refiner sweep confidence, tail low, and independent low endpoint preservation
def test_refiner_sweep_and_tail_scenarios(tmp_path, monkeypatch):
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    analysis_dir = edit_dir / "audio_analysis"
    analysis_dir.mkdir()

    source_path = edit_dir / "test_source.wav"
    source_path.write_bytes(b"dummy wav data")

    # Mocks
    def mock_check_output(cmd, **kwargs):
        if "-version" in cmd:
            return "ffmpeg version 5.0.1"
        if "-filters" in cmd:
            return "Filters:\n  arnndn             A->A       Apply RNNoise filter"
        if "channels" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"channels": 1}]})
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

    from helpers.audio_analysis import get_source_fingerprint, DEFAULT_VAD_PARAMS, EXPECTED_MODEL_HASH
    from helpers.refine_edl_boundaries import main as refine_main

    # 1. Sweep Medium Confidence Test (spread_in = 2)
    transcript_data = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.7, "type": "word"}
        ]
    }
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    fingerprint = get_source_fingerprint(
        source_path,
        EXPECTED_MODEL_HASH,
        DEFAULT_VAD_PARAMS,
        "ffmpeg version 5.0.1",
        transcript_path
    )

    # Write dummy raw and rnn PCM files
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
        "raw_energy": 1e12,
        "rnn_energy": 1e12
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    dummy_rms = np.full(200, -10.0)
    dummy_rms[::2] = -9.0
    dummy_nf = np.full(200, -50.0)

    def mock_get_combined_activity(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:140] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity)

    vad_call_count = 0
    def mock_run_vad_medium(rms, nf, high_t, low_t, gap, trans):
        nonlocal vad_call_count
        vad_call_count += 1
        print(f"DEBUG medium call {vad_call_count}")
        act = np.zeros(200, dtype=bool)
        if vad_call_count in (1, 2, 3, 4):
            act[40:140] = True
        else:
            act[54:140] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad_medium)

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
        assert e.code == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "medium"
    assert evidence["confidence"]["end"] == "high"

    # 2. Sweep Low Confidence Test (spread_in = 3)
    vad_call_count = 0
    def mock_run_vad_low(rms, nf, high_t, low_t, gap, trans):
        nonlocal vad_call_count
        vad_call_count += 1
        print(f"DEBUG low call {vad_call_count}")
        act = np.zeros(200, dtype=bool)
        if vad_call_count in (1, 2, 3, 4):
            act[40:140] = True
        else:
            act[62:140] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad_low)

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "review"
    evidence = report["boundary_evidence"][0]
    assert evidence["confidence"]["start"] == "low"
    assert evidence["confidence"]["end"] == "high"

    # 3. Tail Low + Independent Endpoint Preservation Test
    transcript_data_tail = {
        "words": [
            {"text": "hello", "start": 0.2, "end": 0.7, "type": "word"},
            {"text": "next", "start": 0.71, "end": 0.9, "type": "word"}
        ]
    }
    transcript_path.write_text(json.dumps(transcript_data_tail), encoding="utf-8")

    fingerprint_tail = get_source_fingerprint(
        source_path,
        EXPECTED_MODEL_HASH,
        DEFAULT_VAD_PARAMS,
        "ffmpeg version 5.0.1",
        transcript_path
    )

    raw_path_tail = analysis_dir / f"test_source_{fingerprint_tail}_raw.pcm"
    rnn_path_tail = analysis_dir / f"test_source_{fingerprint_tail}_rnn.pcm"
    meta_path_tail = analysis_dir / f"test_source_{fingerprint_tail}_meta.json"

    np.zeros(48000, dtype=np.int16).tofile(raw_path_tail)
    np.zeros(48000, dtype=np.int16).tofile(rnn_path_tail)

    meta_tail = {
        "fingerprint": fingerprint_tail,
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
        "raw_energy": 1e12,
        "rnn_energy": 1e12
    }
    meta_path_tail.write_text(json.dumps(meta_tail, indent=2), encoding="utf-8")

    # Mock get_combined_activity and run_vad_hysteresis to be deterministic
    def mock_get_combined_activity_tail(raw_pcm_path, rnn_pcm_path, params, words=None):
        act = np.zeros(200, dtype=bool)
        act[40:140] = True
        return act, act.copy(), 0, dummy_rms, dummy_rms.copy(), dummy_nf, dummy_nf.copy()

    monkeypatch.setattr("helpers.refine_edl_boundaries.get_combined_activity", mock_get_combined_activity_tail)

    def mock_run_vad_tail(rms, nf, high_t, low_t, gap, trans):
        act = np.zeros(200, dtype=bool)
        act[40:140] = True
        return act

    monkeypatch.setattr("helpers.refine_edl_boundaries.run_vad_hysteresis", mock_run_vad_tail)

    edl_tail = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.70}]
    }
    edl_path.write_text(json.dumps(edl_tail), encoding="utf-8")

    try:
        refine_main()
    except SystemExit as e:
        assert e.code == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report["boundary_evidence"][0]

    assert evidence["confidence"]["start"] in ("high", "medium")
    assert evidence["confidence"]["end"] == "low"
    assert evidence["tail_frames"] is not None
    assert evidence["tail_frames"] < 2

    updated_edl = json.loads(edl_path.read_text(encoding="utf-8"))
    updated_range = updated_edl["ranges"][0]
    assert updated_range["start"] != 0.15
    assert updated_range["end"] == 0.70


# 11. Test report + EDL replacement failure rollback behavior
def test_refiner_rollback_on_replace_failure(tmp_path, monkeypatch):
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    analysis_dir = edit_dir / "audio_analysis"
    analysis_dir.mkdir()

    source_path = edit_dir / "test_source.wav"
    source_path.write_bytes(b"dummy wav data")

    def mock_check_output(cmd, **kwargs):
        if "-version" in cmd:
            return "ffmpeg version 5.0.1"
        if "-filters" in cmd:
            return "Filters:\n  arnndn             A->A       Apply RNNoise filter"
        if "channels" in cmd[cmd.index("-show_entries")+1]:
            return json.dumps({"streams": [{"channels": 1}]})
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

    from helpers.audio_analysis import get_source_fingerprint, DEFAULT_VAD_PARAMS, EXPECTED_MODEL_HASH
    from helpers.refine_edl_boundaries import main as refine_main

    transcript_data = {"words": [{"text": "hello", "start": 0.2, "end": 0.7, "type": "word"}]}
    transcript_path = transcripts_dir / "test_source.json"
    transcript_path.write_text(json.dumps(transcript_data), encoding="utf-8")

    fingerprint = get_source_fingerprint(
        source_path,
        EXPECTED_MODEL_HASH,
        DEFAULT_VAD_PARAMS,
        "ffmpeg version 5.0.1",
        transcript_path
    )

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
        "raw_energy": 0.0,
        "rnn_energy": 0.0
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    edl = {
        "version": 1,
        "sources": {"test_source": "test_source.wav"},
        "ranges": [{"source": "test_source", "start": 0.15, "end": 0.75}]
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    original_edl_bytes = edl_path.read_bytes()

    report_path = edit_dir / "report.json"
    report_path.write_text("old report content", encoding="utf-8")
    original_report_bytes = report_path.read_bytes()

    replace_calls = []
    real_replace = os.replace

    def mock_replace(src, dst):
        replace_calls.append((src, dst))
        if len(replace_calls) == 2:
            raise OSError("Simulated second replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", mock_replace)

    test_argv = ["helpers/refine_edl_boundaries.py", str(edl_path), "--transcripts", str(transcripts_dir), "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        refine_main()
    assert excinfo.value.code == 1

    # Verify rollback: report and EDL must be restored to their original bytes
    assert report_path.read_bytes() == original_report_bytes
    assert edl_path.read_bytes() == original_edl_bytes
