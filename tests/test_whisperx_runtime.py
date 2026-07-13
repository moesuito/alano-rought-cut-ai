"""Unit tests for the shared WhisperX CUDA runtime manager."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from helpers import whisperx_runtime as runtime


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def healthy_probe_payload() -> dict:
    return {
        "python_executable": r"C:\runtime\Scripts\python.exe",
        "python_version": "3.12.10",
        "packages": {
            "whisperx": runtime.WHISPERX_VERSION,
            "faster-whisper": runtime.FASTER_WHISPER_VERSION,
            "pyannote.audio": runtime.PYANNOTE_AUDIO_VERSION,
            "hf-xet": runtime.HF_XET_VERSION,
            "torch": runtime.TORCH_VERSION,
            "torchaudio": runtime.TORCHAUDIO_VERSION,
            "torchvision": runtime.TORCHVISION_VERSION,
        },
        "imports": {
            "whisperx": True,
            "faster_whisper": True,
            "pyannote.audio": True,
            "hf_xet": True,
            "torch": True,
            "torchaudio": True,
            "torchvision": True,
        },
        "cuda": {
            "available": True,
            "build_version": "12.8",
            "gpu_name": "NVIDIA GeForce RTX 3060 Laptop GPU",
            "capability": [8, 6],
        },
    }


def doctor_stdout(payload: dict | None = None) -> str:
    value = payload or healthy_probe_payload()
    return "optional import noise\n" + runtime._DOCTOR_MARKER + json.dumps(value) + "\n"


def test_default_runtime_dir_uses_local_app_data():
    path = runtime.default_runtime_dir({"LOCALAPPDATA": r"C:\Users\A\AppData\Local"})

    assert path == (
        Path(r"C:\Users\A\AppData\Local")
        / "AlanoCut"
        / "runtimes"
        / "whisperx-3.8.6-py312"
    )


def test_runtime_python_path_is_windows_venv_executable():
    assert runtime.runtime_python_path(Path(r"C:\shared\whisperx")) == (
        Path(r"C:\shared\whisperx") / "Scripts" / "python.exe"
    )


def test_locate_prefers_explicit_python_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-python.exe"
    override.write_bytes(b"")
    seen = []
    monkeypatch.setattr(runtime, "validate_python_executable", lambda path: seen.append(Path(path)))

    found = runtime.locate_runtime_python(
        {
            runtime.RUNTIME_PYTHON_ENV: str(override),
            "LOCALAPPDATA": str(tmp_path / "local"),
        }
    )

    assert found == override
    assert seen == [override]


def test_invalid_explicit_override_does_not_fall_back(monkeypatch, tmp_path):
    missing = tmp_path / "missing.exe"
    monkeypatch.setattr(
        runtime,
        "validate_python_executable",
        lambda path: (_ for _ in ()).throw(runtime.RuntimeContractError(f"bad: {path}")),
    )

    with pytest.raises(runtime.RuntimeContractError, match="missing.exe"):
        runtime.locate_runtime_python(
            {
                runtime.RUNTIME_PYTHON_ENV: str(missing),
                "LOCALAPPDATA": str(tmp_path / "fallback"),
            }
        )


def test_validate_python_executable_probes_without_shell(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(json.dumps({"executable": str(python), "version": [3, 12, 4]}))

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    result = runtime.validate_python_executable(python)

    assert result["version"] == [3, 12, 4]
    assert calls[0][0][:2] == [str(python), "-c"]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["timeout"] == 30


def test_validate_python_rejects_unsupported_version(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: completed(
            json.dumps({"executable": str(python), "version": [3, 9, 19]})
        ),
    )

    with pytest.raises(runtime.RuntimeContractError, match=r"requires Python 3\.10\+"):
        runtime.validate_python_executable(python)


def test_doctor_returns_complete_healthy_contract(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(doctor_stdout())

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    report = runtime.doctor_runtime(Path(r"C:\runtime\Scripts\python.exe"), validate=False)

    assert report["status"] == "pass"
    assert report["failed_checks"] == []
    assert all(report["checks"].values())
    assert report["expected"]["torch"] == "2.8.0+cu128"
    assert report["cuda"]["gpu_name"] == "NVIDIA GeForce RTX 3060 Laptop GPU"
    assert calls[0][0][0] == r"C:\runtime\Scripts\python.exe"
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    [
        (lambda value: value["packages"].__setitem__("torch", "2.8.0+cpu"), "torch_version"),
        (lambda value: value["cuda"].__setitem__("available", False), "cuda_available"),
        (lambda value: value["imports"].__setitem__("pyannote.audio", False), "pyannote_import"),
        (lambda value: value.__setitem__("python_version", "3.11.9"), "python_3_12"),
    ],
)
def test_doctor_marks_contract_mismatch_unhealthy(monkeypatch, mutation, failed_check):
    payload = healthy_probe_payload()
    mutation(payload)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: completed(doctor_stdout(payload)),
    )

    report = runtime.doctor_runtime("python.exe", validate=False)

    assert report["status"] == "unhealthy"
    assert failed_check in report["failed_checks"]


def test_doctor_rejects_missing_json_marker(monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: completed("third-party import output only"),
    )

    with pytest.raises(runtime.RuntimeContractError, match="did not return its JSON"):
        runtime.doctor_runtime("python.exe", validate=False)


def test_setup_uses_uv_and_pinned_cuda_index(monkeypatch, tmp_path):
    destination = tmp_path / "runtime"
    commands = []
    messages = []

    monkeypatch.setattr(runtime, "_assert_windows_system_drive", lambda _path: None)
    monkeypatch.setattr(
        runtime,
        "_run_setup_command",
        lambda command, *, label: commands.append((label, list(command))),
    )
    monkeypatch.setattr(runtime, "validate_python_executable", lambda _path: {})
    monkeypatch.setattr(
        runtime,
        "doctor_runtime",
        lambda path, validate=False: {"status": "pass", "runtime_python": str(path)},
    )

    report = runtime.setup_runtime(
        destination,
        uv_executable=r"C:\Tools\uv.exe",
        progress=messages.append,
    )

    expected_python = str(destination / "Scripts" / "python.exe")
    assert report["status"] == "pass"
    assert len(commands) == 3
    assert commands[0][1] == [
        r"C:\Tools\uv.exe",
        "venv",
        "--allow-existing",
        "--python",
        "3.12",
        str(destination),
    ]
    assert "torch==2.8.0+cu128" in commands[1][1]
    assert "torchaudio==2.8.0+cu128" in commands[1][1]
    assert "torchvision==0.23.0+cu128" in commands[1][1]
    assert commands[1][1][-2:] == ["--index-url", runtime.CUDA_INDEX_URL]
    assert commands[2][1][4] == expected_python
    assert "whisperx==3.8.6" in commands[2][1]
    assert "faster-whisper==1.2.1" in commands[2][1]
    assert "pyannote.audio==4.0.7" in commands[2][1]
    assert "hf_xet==1.5.1" in commands[2][1]
    assert len(messages) == 3


def test_missing_uv_is_bootstrapped_without_shell(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
    calls = []
    monkeypatch.setattr(
        runtime,
        "_run_setup_command",
        lambda command, *, label: calls.append((list(command), label)),
    )

    command = runtime._resolve_uv_command()

    assert command == [runtime.sys.executable, "-m", "uv"]
    assert calls == [
        (
            [runtime.sys.executable, "-m", "pip", "install", "uv==0.11.21"],
            "Installing uv 0.11.21",
        )
    ]


def test_setup_command_failure_has_clear_exit_context(monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: completed(stderr="resolver failed", returncode=7),
    )

    with pytest.raises(runtime.RuntimeContractError, match=r"install failed \(exit 7\): resolver failed"):
        runtime._run_setup_command(["uv", "pip", "install"], label="install")


def test_cli_doctor_exit_codes_and_json(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "doctor_runtime", lambda: {"status": "unhealthy", "checks": {}})

    exit_code = runtime.main(["doctor"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == runtime.EXIT_UNHEALTHY
    assert output["status"] == "unhealthy"


def test_cli_operational_error_is_json_and_exit_one(monkeypatch, capsys):
    monkeypatch.setattr(
        runtime,
        "doctor_runtime",
        lambda: (_ for _ in ()).throw(runtime.RuntimeContractError("runtime missing")),
    )

    exit_code = runtime.main(["doctor"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == runtime.EXIT_ERROR
    assert output == {"error": "runtime missing", "schema_version": 1, "status": "error"}


def test_parser_exposes_no_authentication_argument():
    parser = runtime.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["setup", "--token", "secret"])
