"""Unit and integration tests for F1.2 WAV Preview, Timeline Map, and Join QC."""

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

from helpers.timing import parse_fps_fraction, time_to_frame
from helpers.render import main as render_main, frame_to_sample
from helpers.preview_audio_qc import main as qc_main


def create_synthetic_wav(
    path: Path,
    sample_rate: int = 48000,
    channels: int = 2,
    duration_s: float = 1.0,
    frequency: float = 440.0,
    volume: float = 0.5,
    out_of_phase: bool = False,
) -> None:
    """Generate a synthetic WAV file for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_s)
    t = np.arange(num_samples) / sample_rate

    # Generate a sine wave
    data = np.sin(2 * np.pi * frequency * t) * 32767 * volume
    data = data.astype(np.int16)

    if channels == 1:
        samples = data
    elif channels == 2:
        if out_of_phase:
            samples = np.column_stack((data, -data))
        else:
            samples = np.column_stack((data, data))
    else:
        # Multichannel
        cols = [data] * channels
        samples = np.column_stack(cols)

    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())


def test_frame_to_sample_parity():
    """Verify exact frame-to-sample parity for standard frame rates."""
    sample_rate = 48000

    # 23.976 (24000/1001)
    fps_23 = parse_fps_fraction(23.976)
    assert frame_to_sample(24, fps_23, sample_rate) == 48048

    # 25
    fps_25 = parse_fps_fraction(25)
    assert frame_to_sample(25, fps_25, sample_rate) == 48000

    # 29.97 (30000/1001)
    fps_29 = parse_fps_fraction(29.97)
    assert frame_to_sample(30, fps_29, sample_rate) == 48048

    # 30
    fps_30 = parse_fps_fraction(30)
    assert frame_to_sample(30, fps_30, sample_rate) == 48000

    # 59.94 (60000/1001)
    fps_59 = parse_fps_fraction(59.94)
    assert frame_to_sample(60, fps_59, sample_rate) == 48048


def test_render_rejects_mp4(tmp_path, monkeypatch):
    """Verify that render.py rejects .mp4 output files."""
    edl_path = tmp_path / "edl.json"
    edl_path.write_text("{}", encoding="utf-8")

    output_mp4 = tmp_path / "output.mp4"
    map_json = tmp_path / "map.json"

    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_mp4),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        render_main()
    assert excinfo.value.code == 1


def test_render_atomic_failure(tmp_path, monkeypatch):
    """Verify that failure during render.py does not modify existing outputs."""
    # Create pre-existing valid output files
    existing_wav = tmp_path / "preview.wav"
    existing_wav.write_text("mock wav data", encoding="utf-8")
    existing_map = tmp_path / "map.json"
    existing_map.write_text("mock map data", encoding="utf-8")

    # Invalid EDL to trigger a failure
    bad_edl = tmp_path / "edl.json"
    bad_edl.write_text("{invalid json}", encoding="utf-8")

    test_argv = [
        "helpers/render.py",
        str(bad_edl),
        "-o", str(existing_wav),
        "--timeline-map", str(existing_map)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)

    with pytest.raises(SystemExit) as excinfo:
        render_main()
    assert excinfo.value.code == 1

    # Check that original files were untouched
    assert existing_wav.read_text(encoding="utf-8") == "mock wav data"
    assert existing_map.read_text(encoding="utf-8") == "mock map data"


def test_render_and_qc_integration(tmp_path, monkeypatch):
    """Integration test for mono/stereo/multichannel, frame rates, clean joins and pops."""
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()

    # 1. Create synthetic sources
    mono_source = edit_dir / "mono.wav"
    stereo_source = edit_dir / "stereo.wav"
    antiphase_source = edit_dir / "antiphase.wav"
    multichannel_source = edit_dir / "multichannel.wav"

    # Create WAVs (all 48kHz, but different channels/phases)
    create_synthetic_wav(mono_source, channels=1, duration_s=2.0, frequency=440.0)
    create_synthetic_wav(stereo_source, channels=2, duration_s=2.0, frequency=440.0)
    create_synthetic_wav(antiphase_source, channels=2, duration_s=2.0, frequency=440.0, out_of_phase=True)
    create_synthetic_wav(multichannel_source, channels=4, duration_s=2.0, frequency=440.0)

    # 2. Write an EDL
    # We will test a sequence with 4 ranges:
    # Range 0: mono source
    # Range 1: stereo source
    # Range 2: antiphase source
    # Range 3: multichannel source
    edl = {
        "version": 1,
        "sources": {
            "mono": "mono.wav",
            "stereo": "stereo.wav",
            "antiphase": "antiphase.wav",
            "multi": "multichannel.wav"
        },
        "ranges": [
            {
                "source": "mono",
                "start": 0.5,
                "end": 1.5,
                "source_in_frame": 15,
                "source_out_frame": 45
            },
            {
                "source": "stereo",
                "start": 0.2,
                "end": 1.2
                # source_in_frame/source_out_frame omitted to test fallback rational conversion
            },
            {
                "source": "antiphase",
                "start": 0.1,
                "end": 1.1,
                "source_in_frame": 3,
                "source_out_frame": 33
            },
            {
                "source": "multi",
                "start": 0.0,
                "end": 1.0,
                "source_in_frame": 0,
                "source_out_frame": 30
            }
        ],
        "metadata": {
            "sequence_fps": 30.0
        }
    }

    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl, indent=2), encoding="utf-8")

    preview_wav = edit_dir / "preview.wav"
    timeline_map_path = edit_dir / "preview_timeline.json"
    qc_report_path = edit_dir / "preview_audio_qc.json"

    # Run render.py
    test_argv_render = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(preview_wav),
        "--timeline-map", str(timeline_map_path),
        "--preview" # Deprecated no-op
    ]
    monkeypatch.setattr(sys, "argv", test_argv_render)
    render_main()

    # Assert output files exist
    assert preview_wav.exists()
    assert timeline_map_path.exists()

    # Verify timeline map contents
    t_map = json.loads(timeline_map_path.read_text(encoding="utf-8"))
    assert t_map["output_format"]["format"] == "PCM16"
    assert t_map["output_format"]["sample_rate"] == 48000
    assert t_map["output_format"]["channels"] == 2
    assert t_map["output_format"]["channel_policy"] == "mixed"
    assert len(t_map["ranges"]) == 4

    # Check mono to stereo conversion
    assert t_map["ranges"][0]["source_channels"] == 1
    assert t_map["ranges"][0]["channel_policy"] == "mono_to_stereo"

    # Check stereo preserve
    assert t_map["ranges"][1]["source_channels"] == 2
    assert t_map["ranges"][1]["channel_policy"] == "stereo_preserve"

    # Check multichannel downmix
    assert t_map["ranges"][3]["source_channels"] == 4
    assert t_map["ranges"][3]["channel_policy"] == "multichannel_downmix"

    # Check output PCM properties
    with wave.open(str(preview_wav), "rb") as w:
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48000

    # Run preview_audio_qc.py
    test_argv_qc = [
        "helpers/preview_audio_qc.py",
        str(preview_wav),
        "-m", str(timeline_map_path),
        "-o", str(qc_report_path),
        "-e", str(edl_path)
    ]
    monkeypatch.setattr(sys, "argv", test_argv_qc)
    qc_main()

    assert qc_report_path.exists()
    qc_data = json.loads(qc_report_path.read_text(encoding="utf-8"))

    assert qc_data["wav_format_ok"] is True
    assert qc_data["sample_count_parity_ok"] is True
    assert "joins" in qc_data
    assert len(qc_data["joins"]) == 3

    # Clean up
    shutil.rmtree(edit_dir, ignore_errors=True)


def test_qc_pop_detection(tmp_path, monkeypatch):
    """Test pop detection (severe/warning/pass) on synthetic joins."""
    qc_dir = tmp_path / "qc_test"
    qc_dir.mkdir()

    # We will generate a preview WAV directly with a severe pop at the join.
    # Join point is at sample index 48000.
    # Left segment: constant 5000
    # Right segment: constant -5000 (discontinuity delta = 10000)
    # Background noise delta in window: very low (0).
    # Since background delta is 0, local_p95_delta = 0.
    # severe threshold is max(3276.8, 8 * 0) = 3276.8.
    # warning threshold is max(1638.4, 4 * 0) = 1638.4.
    # Delta 10000 >= 3276.8, so severe pop!

    wav_path = qc_dir / "preview_pop.wav"
    samples_left = np.full(48000, 5000, dtype=np.int16)
    samples_right = np.full(48000, -5000, dtype=np.int16)
    samples_all = np.concatenate([samples_left, samples_right])
    # Stereo
    samples_stereo = np.column_stack((samples_all, samples_all))

    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(samples_stereo.tobytes())

    # Write a map matching this
    t_map = {
        "edl_hash": "dummy_hash",
        "output_format": {
            "format": "PCM16",
            "sample_rate": 48000,
            "channels": 2,
            "channel_policy": "stereo_preserve",
            "sequence_fps": 30.0
        },
        "ranges": [
            {
                "source": "s1",
                "source_frames": [0, 30],
                "source_sample_interval": [0, 48000],
                "output_cumulative_sample_interval": [0, 48000],
                "seconds": 1.0,
                "source_channels": 2,
                "channel_policy": "stereo_preserve"
            },
            {
                "source": "s2",
                "source_frames": [0, 30],
                "source_sample_interval": [0, 48000],
                "output_cumulative_sample_interval": [48000, 96000],
                "seconds": 1.0,
                "source_channels": 2,
                "channel_policy": "stereo_preserve"
            }
        ]
    }
    map_path = qc_dir / "preview_timeline.json"
    map_path.write_text(json.dumps(t_map), encoding="utf-8")

    qc_report_path = qc_dir / "preview_audio_qc.json"

    test_argv = [
        "helpers/preview_audio_qc.py",
        str(wav_path),
        "-m", str(map_path),
        "-o", str(qc_report_path)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)
    qc_main()

    qc_data = json.loads(qc_report_path.read_text(encoding="utf-8"))
    assert qc_data["severe_pops_count"] == 1
    assert qc_data["status"] == "review"
    assert qc_data["joins"][0]["status"] == "severe"

    # 2. Test warning pop:
    # Delta of 2000 (e.g. left constant 1000, right constant -1000).
    # Discontinuity delta = 2000.
    # 2000 is < 3276.8 (severe threshold) but >= 1638.4 (warning threshold).
    # So this should be a warning!
    samples_left_w = np.full(48000, 1000, dtype=np.int16)
    samples_right_w = np.full(48000, -1000, dtype=np.int16)
    samples_all_w = np.concatenate([samples_left_w, samples_right_w])
    samples_stereo_w = np.column_stack((samples_all_w, samples_all_w))

    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(samples_stereo_w.tobytes())

    qc_main()

    qc_data = json.loads(qc_report_path.read_text(encoding="utf-8"))
    assert qc_data["severe_pops_count"] == 0
    assert qc_data["warning_pops_count"] == 1
    assert qc_data["status"] == "warning"
    assert qc_data["joins"][0]["status"] == "warning"

    # Clean up
    shutil.rmtree(qc_dir, ignore_errors=True)
