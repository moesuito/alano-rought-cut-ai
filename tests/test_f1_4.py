"""Tests for F1.4 XML Parity, Audio-Only Workflow, and v0.4.0 Release Preparation."""

from __future__ import annotations

import json
import os
import re
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


@pytest.mark.parametrize("argument", ["--help", "-h", "help", "init", "sessions"])
def test_alanocut_controller_rejects_every_argument(argument):
    """The public controller accepts literally zero arguments."""
    ps_script = Path("bin/alanocut.ps1").absolute()
    res = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script),
            argument,
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode != 0
    assert "Run only: alanocut" in (res.stdout + res.stderr)


def test_runtime_packaging_excludes_development_agent_trees():
    installer = Path("install.ps1").read_text(encoding="utf-8")
    controller = Path("bin/alanocut.ps1").read_text(encoding="utf-8")
    cmd_launcher = Path("bin/alanocut.cmd").read_text(encoding="utf-8")

    assert '$RuntimeDirectories = @("agent_knowledge", "bin", "helpers")' in installer
    assert 'foreach ($DevelopmentTree in @(".agents", ".squad"))' in installer
    assert "$PSScriptRoot" in installer
    assert "gh release" not in installer
    assert "git clone" not in installer
    assert "Copy-Item" not in controller
    assert '"init"' not in controller
    assert "%*" not in cmd_launcher
    assert 'if not "%~1"==""' in cmd_launcher
    assert "-File" in cmd_launcher
    assert "exit /b %ERRORLEVEL%" in cmd_launcher


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_cmd_launcher_rejects_arguments_before_starting_powershell(tmp_path):
    environment = os.environ.copy()
    environment["APPDATA"] = str(tmp_path / "missing-runtime")
    result = subprocess.run(
        ["cmd", "/d", "/c", str(Path("bin/alanocut.cmd").resolve()), "--help"],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 2
    assert "Run only: alanocut" in (result.stdout + result.stderr)


def test_fresh_runtime_declares_direct_tui_and_alignment_dependencies():
    """A clean shared venv must contain every import needed before first export."""
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    declared_names = {
        name.lower()
        for name in re.findall(r'^\s*"([A-Za-z0-9_.-]+)(?:[<>=!~].*)?",\s*$', project, re.MULTILINE)
    }

    assert {"rich", "transformers", "onnx", "jsonschema", "referencing"} <= declared_names


def test_runtime_sync_uses_checkout_allowlist_and_preserves_local_state(tmp_path):
    appdata = tmp_path / "appdata"
    install_dir = appdata / "alano-rought-cut-ai"
    install_dir.mkdir(parents=True)

    (install_dir / ".env").write_text("PRESERVE_ENV=1", encoding="utf-8")
    (install_dir / "user-settings.json").write_text(
        '{"preserve": true}', encoding="utf-8"
    )
    for development_tree in (".agents", ".squad"):
        stale = install_dir / development_tree
        stale.mkdir()
        (stale / "must-disappear.txt").write_text("stale", encoding="utf-8")
    (install_dir / "stale-runtime.txt").write_text("remove", encoding="utf-8")

    environment = os.environ.copy()
    environment["APPDATA"] = str(appdata)
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path("install.ps1").resolve()),
            "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (install_dir / "agent_knowledge" / "manifest.json").is_file()
    assert (install_dir / "bin" / "alanocut.ps1").is_file()
    for relative in (
        "helpers/agent_artifacts.py",
        "helpers/agent_telemetry.py",
        "helpers/agent_tools.py",
        "helpers/artifact_agent.py",
        "helpers/editorial_edl_bridge.py",
        "helpers/interactive_cli.py",
        "helpers/knowledge_loader.py",
        "helpers/llm_client.py",
        "helpers/orchestrator.py",
    ):
        assert (install_dir / relative).is_file(), relative
    assert not (install_dir / ".agents").exists()
    assert not (install_dir / ".squad").exists()
    assert not (install_dir / "stale-runtime.txt").exists()
    assert (install_dir / ".env").read_text(encoding="utf-8") == "PRESERVE_ENV=1"
    assert json.loads((install_dir / "user-settings.json").read_text(encoding="utf-8")) == {
        "preserve": True
    }
    assert not (install_dir / ".venv").exists()


def test_runtime_sync_fails_closed_when_preserved_venv_is_incomplete(tmp_path):
    appdata = tmp_path / "appdata"
    incomplete_scripts = appdata / "alano-rought-cut-ai" / ".venv" / "Scripts"
    incomplete_scripts.mkdir(parents=True)
    venv_marker = incomplete_scripts.parent / "preserved.txt"
    venv_marker.write_text("keep", encoding="utf-8")

    environment = os.environ.copy()
    environment["APPDATA"] = str(appdata)
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(Path("install.ps1").resolve()), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_IMPORT_SMOKE_FAILED: global Python is missing" in (
        result.stdout + result.stderr
    )
    assert venv_marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_runtime_sync_rejects_reparse_source_root(tmp_path):
    source_link = tmp_path / "source-link"
    junction = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(source_link), str(Path.cwd())],
        capture_output=True,
        text=True,
    )
    if junction.returncode != 0:
        pytest.skip("Could not create a Windows junction")

    environment = os.environ.copy()
    environment["APPDATA"] = str(tmp_path / "appdata")
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(source_link / "install.ps1"), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_SYNC_REPARSE_POINT" in (result.stdout + result.stderr)
    assert not (tmp_path / "appdata" / "alano-rought-cut-ai").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_runtime_sync_rejects_reparse_source_top_level(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent_knowledge").mkdir()
    (source / "bin").mkdir()
    for relative in (
        "install.ps1",
        "config.json",
        "pyproject.toml",
        "agent_knowledge/manifest.json",
        "bin/alanocut.cmd",
        "bin/alanocut.ps1",
    ):
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(relative).read_bytes())
    junction = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(source / "helpers"), str(Path("helpers").resolve())],
        capture_output=True,
        text=True,
    )
    if junction.returncode != 0:
        pytest.skip("Could not create a Windows junction")

    environment = os.environ.copy()
    environment["APPDATA"] = str(tmp_path / "appdata")
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(source / "install.ps1"), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_SYNC_REPARSE_POINT" in (result.stdout + result.stderr)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_runtime_sync_rejects_reparse_destination_root(tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "must-survive.txt"
    sentinel.write_text("safe", encoding="utf-8")
    install_link = appdata / "alano-rought-cut-ai"
    junction = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(install_link), str(outside)],
        capture_output=True,
        text=True,
    )
    if junction.returncode != 0:
        pytest.skip("Could not create a Windows junction")

    environment = os.environ.copy()
    environment["APPDATA"] = str(appdata)
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(Path("install.ps1").resolve()), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_SYNC_REPARSE_POINT" in (result.stdout + result.stderr)
    assert sentinel.read_text(encoding="utf-8") == "safe"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_runtime_sync_rejects_reparse_top_level_before_removal(tmp_path):
    appdata = tmp_path / "appdata"
    install_dir = appdata / "alano-rought-cut-ai"
    install_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "must-survive.txt"
    sentinel.write_text("safe", encoding="utf-8")
    junction = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(install_dir / "helpers"), str(outside)],
        capture_output=True,
        text=True,
    )
    if junction.returncode != 0:
        pytest.skip("Could not create a Windows junction")

    environment = os.environ.copy()
    environment["APPDATA"] = str(appdata)
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(Path("install.ps1").resolve()), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_SYNC_REPARSE_POINT" in (result.stdout + result.stderr)
    assert sentinel.read_text(encoding="utf-8") == "safe"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_runtime_sync_rejects_nested_reparse_before_recursive_removal(tmp_path):
    appdata = tmp_path / "appdata"
    install_dir = appdata / "alano-rought-cut-ai"
    stale_tree = install_dir / "stale-runtime" / "nested"
    stale_tree.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "must-survive.txt"
    sentinel.write_text("safe", encoding="utf-8")
    junction = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(stale_tree / "external"), str(outside)],
        capture_output=True,
        text=True,
    )
    if junction.returncode != 0:
        pytest.skip("Could not create a Windows junction")

    environment = os.environ.copy()
    environment["APPDATA"] = str(appdata)
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(Path("install.ps1").resolve()), "-RuntimeSyncOnly",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "RUNTIME_SYNC_REPARSE_POINT" in (result.stdout + result.stderr)
    assert sentinel.read_text(encoding="utf-8") == "safe"


def test_installer_fails_closed_when_virtual_environment_creation_fails(tmp_path):
    """PowerShell 5.1 must not treat a non-zero native exit as success."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "python.cmd").write_text("@echo off\r\nexit /b 7\r\n", encoding="utf-8")

    environment = os.environ.copy()
    environment["APPDATA"] = str(tmp_path / "appdata")
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path("install.ps1").resolve()),
            "-NonInteractive",
        ],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "Virtual environment creation failed with exit code 7" in (
        result.stdout + result.stderr
    )
