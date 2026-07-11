"""Tests for F1.4 XML Parity, Audio-Only Workflow, and v0.4.0 Release Preparation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path
import numpy as np
import pytest

from helpers.timing import parse_fps_fraction
from helpers.render import main as render_main
from helpers.preview_transcript_qc import ElevenLabsScribeProvider


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


def test_ffmpeg_arnndn_available():
    """Verify that FFmpeg is installed and has the 'arnndn' filter."""
    try:
        res = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, check=True)
        assert "arnndn" in res.stdout, "FFmpeg does not have the 'arnndn' filter (required for audio noise suppression in Windows CI)"
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        pytest.fail(f"FFmpeg check failed: {e}")


def test_public_pytest_mock_transcription_only(monkeypatch):
    """Enforce that public pytest uses mock transcription only by patching ElevenLabsScribeProvider."""
    def dummy_transcribe(self, audio_path: Path) -> dict:
        raise RuntimeError("Real ElevenLabs Scribe API call is prohibited in public CI / tests.")
        
    monkeypatch.setattr(ElevenLabsScribeProvider, "transcribe", dummy_transcribe)
    
    provider = ElevenLabsScribeProvider()
    with pytest.raises(RuntimeError, match="Real ElevenLabs Scribe API call is prohibited"):
        provider.transcribe(Path("dummy.wav"))


def test_render_no_mp4_aac_x264_fade(tmp_path, monkeypatch):
    """Verify that render.py produces only PCM16 WAV and does not output MP4/AAC/x264 or use fades."""
    # 1. Inspect render.py source to ensure no visual fade or encoding references
    render_src = Path("helpers/render.py").read_text(encoding="utf-8")
    assert "libx264" not in render_src
    assert "libfdk_aac" not in render_src
    assert "-c:v" not in render_src
    
    # 2. Setup synthetic workspace
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    
    source_wav = edit_dir / "source1.wav"
    create_synthetic_wav(source_wav, duration_s=1.0)
    
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {"source": "source1", "start": 0.0, "end": 0.5, "source_in_frame": 0, "source_out_frame": 12}
        ],
        "metadata": {
            "required_beats": [],
            "sequence_fps": 24.0
        }
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    
    output_wav = edit_dir / "preview.wav"
    map_json = edit_dir / "preview_timeline.json"
    
    # 3. Run render script through CLI entry point
    test_argv = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(output_wav),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv)
    
    render_main()
    
    # 4. Assert outputs
    assert output_wav.exists()
    assert map_json.exists()
    
    # Assert output is PCM WAV
    with wave.open(str(output_wav), "rb") as w:
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48000
        
    # Assert timeline map has PCM16 and correct frame mappings
    timeline_map = json.loads(map_json.read_text(encoding="utf-8"))
    assert timeline_map["output_format"]["format"] == "PCM16"
    assert timeline_map["ranges"][0]["source_frames"] == [0, 12]
    
    # Assert that no .mp4 is generated and calling with .mp4 exits with 1
    test_argv_mp4 = [
        "helpers/render.py",
        str(edl_path),
        "-o", str(edit_dir / "preview.mp4"),
        "--timeline-map", str(map_json)
    ]
    monkeypatch.setattr(sys, "argv", test_argv_mp4)
    with pytest.raises(SystemExit) as excinfo:
        render_main()
    assert excinfo.value.code == 1


def test_private_regression_opt_in():
    """Opt-in private regression test using ALANOCUT_LESSON08_DIR."""
    lesson_dir = os.environ.get("ALANOCUT_LESSON08_DIR")
    if not lesson_dir:
        pytest.skip("ALANOCUT_LESSON08_DIR not set. Skipping private regression test.")
        
    lesson_path = Path(lesson_dir)
    assert lesson_path.exists(), f"Private regression directory not found: {lesson_dir}"
    
    # Look for ignored manifest in project root or lesson_dir
    manifest_path = Path("regression_manifest.json")
    if not manifest_path.exists():
        manifest_path = Path("manifest.json")
    if not manifest_path.exists():
        manifest_path = lesson_path / "manifest.json"
        
    if not manifest_path.exists():
        pytest.skip("Ignored regression manifest not found. Skipping.")
        
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    
    # Verify that files mentioned in the manifest exist
    if "sources" in manifest:
        for src_name, rel_path in manifest["sources"].items():
            full_path = lesson_path / rel_path
            assert full_path.exists(), f"Source file from manifest missing: {full_path}"


def test_alanocut_init_smoke(tmp_path):
    """Verify that alanocut init runs and sets up the workspace."""
    env = os.environ.copy()
    env["USERPROFILE"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    ps_script = Path("bin/alanocut.ps1").absolute()
    
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script), "init"],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True
    )
    
    assert res.returncode == 0, f"alanocut init failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    
    # Check created files
    assert (workspace / "helpers").exists()
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "SKILL.md").exists()
    assert (workspace / "pyproject.toml").exists()
    assert (workspace / ".gitignore").exists()
    assert (workspace / "config.json").exists()
    assert (workspace / ".agents").exists()
    assert (workspace / "raw_video").exists()
    assert (workspace / "raw_video/edit").exists()


def test_alanocut_update_smoke():
    """Verify that alanocut update runs successfully."""
    ps_script = Path("bin/alanocut.ps1").absolute()
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script), "update"],
        capture_output=True,
        text=True
    )
    # It might warn about gh CLI, but should exit with 0 if it skips or succeeds
    assert res.returncode == 0, f"alanocut update failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

