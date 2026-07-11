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
