"""Install and diagnose Alano Cut's shared CUDA WhisperX runtime.

The runtime is deliberately separate from the Alano Cut source checkout.  It is
created once per Windows user and can be reused by every project/workspace.
Neither command downloads speech/diarization models or accepts authentication
credentials; model acquisition belongs to the transcription command.

Usage::

    python helpers/whisperx_runtime.py setup
    python helpers/whisperx_runtime.py doctor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


PREFERRED_PYTHON_VERSION = "3.12"
MINIMUM_PYTHON_VERSION = (3, 10)
WHISPERX_VERSION = "3.8.6"
FASTER_WHISPER_VERSION = "1.2.1"
PYANNOTE_AUDIO_VERSION = "4.0.7"
HF_XET_VERSION = "1.5.1"
TORCH_VERSION = "2.8.0+cu128"
TORCHAUDIO_VERSION = "2.8.0+cu128"
TORCHVISION_VERSION = "0.23.0+cu128"
CUDA_BUILD_VERSION = "12.8"
CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"
UV_VERSION = "0.11.21"

RUNTIME_PYTHON_ENV = "ALANOCUT_WHISPERX_PYTHON"
RUNTIME_DIR_NAME = f"whisperx-{WHISPERX_VERSION}-py312"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNHEALTHY = 2

_DOCTOR_MARKER = "ALANOCUT_WHISPERX_DOCTOR="


class RuntimeContractError(RuntimeError):
    """Raised when the shared runtime cannot be created or inspected."""


def default_runtime_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the versioned per-user runtime directory.

    ``LOCALAPPDATA`` is authoritative on Windows.  The fallback mainly makes
    diagnostics understandable in unusual service accounts and in unit tests.
    """

    values = os.environ if env is None else env
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data).expanduser()
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "AlanoCut" / "runtimes" / RUNTIME_DIR_NAME


def runtime_python_path(runtime_dir: Path | str) -> Path:
    """Return the Windows Python executable inside a uv-created venv."""

    return Path(runtime_dir).expanduser() / "Scripts" / "python.exe"


def _completed_json(stdout: str, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(f"{context} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeContractError(f"{context} returned a non-object JSON payload")
    return payload


def validate_python_executable(python_path: Path | str) -> dict[str, Any]:
    """Validate that *python_path* is a runnable, supported Python executable."""

    candidate = Path(python_path).expanduser()
    if not candidate.exists():
        raise RuntimeContractError(f"WhisperX Python executable not found: {candidate}")
    if not candidate.is_file():
        raise RuntimeContractError(f"WhisperX Python path is not a file: {candidate}")

    probe = (
        "import json,sys; "
        "print(json.dumps({'executable':sys.executable,"
        "'version':[sys.version_info.major,sys.version_info.minor,sys.version_info.micro]}))"
    )
    try:
        completed = subprocess.run(
            [str(candidate), "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeContractError(
            f"WhisperX Python executable could not be started: {candidate}: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()[-1000:]
        raise RuntimeContractError(
            f"WhisperX Python executable failed validation: {candidate}: {detail}"
        )

    info = _completed_json(completed.stdout, context="Python validation")
    version = info.get("version")
    if (
        not isinstance(version, list)
        or len(version) < 2
        or not all(isinstance(part, int) for part in version[:2])
    ):
        raise RuntimeContractError("Python validation did not report a valid version")
    if tuple(version[:2]) < MINIMUM_PYTHON_VERSION:
        found = ".".join(str(part) for part in version[:3])
        minimum = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
        raise RuntimeContractError(
            f"WhisperX runtime requires Python {minimum}+; found {found} at {candidate}"
        )
    return info


def locate_runtime_python(
    env: Mapping[str, str] | None = None,
    *,
    validate: bool = True,
) -> Path:
    """Locate the runtime Python, honoring the explicit override first.

    An invalid explicit override is a configuration error.  It does not silently
    fall back to the shared runtime, which would make deployments unpredictable.
    """

    values = os.environ if env is None else env
    override = values.get(RUNTIME_PYTHON_ENV, "").strip()
    candidate = (
        Path(override).expanduser()
        if override
        else runtime_python_path(default_runtime_dir(values))
    )
    if validate:
        validate_python_executable(candidate)
    return candidate


def _run_setup_command(command: Sequence[str], *, label: str) -> None:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeContractError(f"{label} could not be started: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()[-3000:]
        raise RuntimeContractError(f"{label} failed (exit {completed.returncode}): {detail}")


def _resolve_uv_command(uv_executable: Path | str | None = None) -> list[str]:
    if uv_executable is not None:
        return [str(uv_executable)]
    discovered = shutil.which("uv")
    if discovered:
        return [discovered]
    _run_setup_command(
        [sys.executable, "-m", "pip", "install", f"uv=={UV_VERSION}"],
        label=f"Installing uv {UV_VERSION}",
    )
    return [sys.executable, "-m", "uv"]


def _assert_windows_system_drive(runtime_dir: Path) -> None:
    """Keep the large CUDA runtime on C: as defined by the product contract."""

    if os.name == "nt" and runtime_dir.drive.upper() != "C:":
        raise RuntimeContractError(
            f"WhisperX shared runtime must be created on C:, got: {runtime_dir}"
        )


_DOCTOR_SCRIPT = r'''
import contextlib
import importlib
import importlib.metadata
import io
import json
import platform
import sys

marker = "ALANOCUT_WHISPERX_DOCTOR="
packages = {}
for distribution in (
    "whisperx", "faster-whisper", "pyannote.audio",
    "hf-xet", "torch", "torchaudio", "torchvision",
):
    try:
        packages[distribution] = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        packages[distribution] = None

imports = {}
loaded = {}
for module_name in (
    "whisperx", "faster_whisper", "pyannote.audio",
    "hf_xet", "torch", "torchaudio", "torchvision",
):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loaded[module_name] = importlib.import_module(module_name)
        imports[module_name] = True
    except Exception as exc:
        imports[module_name] = False
        imports[module_name + "_error"] = type(exc).__name__ + ": " + str(exc)[:500]

torch = loaded.get("torch")
cuda_available = False
gpu_name = None
gpu_capability = None
cuda_build = None
if torch is not None:
    cuda_build = getattr(getattr(torch, "version", None), "cuda", None)
    try:
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
            gpu_capability = list(torch.cuda.get_device_capability(0))
    except Exception as exc:
        imports["cuda_probe_error"] = type(exc).__name__ + ": " + str(exc)[:500]

payload = {
    "python_executable": sys.executable,
    "python_version": platform.python_version(),
    "packages": packages,
    "imports": imports,
    "cuda": {
        "available": cuda_available,
        "build_version": cuda_build,
        "gpu_name": gpu_name,
        "capability": gpu_capability,
    },
}
print(marker + json.dumps(payload, ensure_ascii=False))
'''


def _parse_doctor_output(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_DOCTOR_MARKER):
            return _completed_json(line[len(_DOCTOR_MARKER) :], context="WhisperX doctor")
    raise RuntimeContractError("WhisperX doctor did not return its JSON result")


def _python_is_preferred(version: object) -> bool:
    if not isinstance(version, str):
        return False
    parts = version.split(".")
    return len(parts) >= 2 and ".".join(parts[:2]) == PREFERRED_PYTHON_VERSION


def doctor_runtime(
    python_path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Inspect imports, pinned versions, and CUDA without downloading models."""

    candidate = (
        locate_runtime_python(env, validate=validate)
        if python_path is None
        else Path(python_path).expanduser()
    )
    if python_path is not None and validate:
        validate_python_executable(candidate)

    try:
        completed = subprocess.run(
            [str(candidate), "-c", _DOCTOR_SCRIPT],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeContractError(f"WhisperX doctor could not be started: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()[-2000:]
        raise RuntimeContractError(
            f"WhisperX doctor failed in {candidate} (exit {completed.returncode}): {detail}"
        )

    payload = _parse_doctor_output(completed.stdout)
    packages = payload.get("packages") if isinstance(payload.get("packages"), dict) else {}
    imports = payload.get("imports") if isinstance(payload.get("imports"), dict) else {}
    cuda = payload.get("cuda") if isinstance(payload.get("cuda"), dict) else {}

    torch_family = {
        "torch": TORCH_VERSION,
        "torchaudio": TORCHAUDIO_VERSION,
        "torchvision": TORCHVISION_VERSION,
    }
    checks: dict[str, bool] = {
        "python_3_12": _python_is_preferred(payload.get("python_version")),
        "whisperx_version": packages.get("whisperx") == WHISPERX_VERSION,
        "faster_whisper_version": packages.get("faster-whisper") == FASTER_WHISPER_VERSION,
        "pyannote_audio_version": packages.get("pyannote.audio") == PYANNOTE_AUDIO_VERSION,
        "hf_xet_version": packages.get("hf-xet") == HF_XET_VERSION,
        "torch_version": packages.get("torch") == TORCH_VERSION,
        "torchaudio_version": packages.get("torchaudio") == TORCHAUDIO_VERSION,
        "torchvision_version": packages.get("torchvision") == TORCHVISION_VERSION,
        "no_cpu_torch_wheels": all(
            isinstance(packages.get(name), str) and "+cpu" not in packages[name].lower()
            for name in torch_family
        ),
        "whisperx_import": imports.get("whisperx") is True,
        "faster_whisper_import": imports.get("faster_whisper") is True,
        "pyannote_import": imports.get("pyannote.audio") is True,
        "hf_xet_import": imports.get("hf_xet") is True,
        "torch_import": imports.get("torch") is True,
        "torchaudio_import": imports.get("torchaudio") is True,
        "torchvision_import": imports.get("torchvision") is True,
        "cuda_available": cuda.get("available") is True,
        "cuda_12_8_build": str(cuda.get("build_version") or "").startswith(CUDA_BUILD_VERSION),
        "gpu_detected": bool(cuda.get("gpu_name")),
        "gpu_capability_detected": (
            isinstance(cuda.get("capability"), list)
            and len(cuda["capability"]) == 2
            and all(isinstance(part, int) for part in cuda["capability"])
        ),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    payload.update(
        {
            "schema_version": 1,
            "status": "pass" if not failed_checks else "unhealthy",
            "runtime_python": str(candidate),
            "expected": {
                "python": PREFERRED_PYTHON_VERSION,
                "whisperx": WHISPERX_VERSION,
                "faster_whisper": FASTER_WHISPER_VERSION,
                "pyannote_audio": PYANNOTE_AUDIO_VERSION,
                "hf_xet": HF_XET_VERSION,
                "torch": TORCH_VERSION,
                "torchaudio": TORCHAUDIO_VERSION,
                "torchvision": TORCHVISION_VERSION,
                "cuda_build": CUDA_BUILD_VERSION,
            },
            "checks": checks,
            "failed_checks": failed_checks,
        }
    )
    return payload


def setup_runtime(
    runtime_dir: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    uv_executable: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create/update the pinned shared WhisperX CUDA runtime using ``uv``."""

    values = os.environ if env is None else env
    destination = Path(runtime_dir or default_runtime_dir(values)).expanduser()
    _assert_windows_system_drive(destination)

    uv = _resolve_uv_command(uv_executable)

    destination.parent.mkdir(parents=True, exist_ok=True)
    runtime_python = runtime_python_path(destination)
    notify = progress or (lambda _message: None)

    commands = [
        (
            "Creating the shared Python 3.12 runtime",
            [
                *uv, "venv", "--allow-existing", "--python",
                PREFERRED_PYTHON_VERSION, str(destination),
            ],
        ),
        (
            "Installing pinned CUDA 12.8 PyTorch wheels",
            [
                *uv,
                "pip",
                "install",
                "--python",
                str(runtime_python),
                "--upgrade",
                f"torch=={TORCH_VERSION}",
                f"torchaudio=={TORCHAUDIO_VERSION}",
                f"torchvision=={TORCHVISION_VERSION}",
                "--index-url",
                CUDA_INDEX_URL,
            ],
        ),
        (
            f"Installing WhisperX {WHISPERX_VERSION}",
            [
                *uv,
                "pip",
                "install",
                "--python",
                str(runtime_python),
                "--upgrade",
                f"whisperx=={WHISPERX_VERSION}",
                f"faster-whisper=={FASTER_WHISPER_VERSION}",
                f"pyannote.audio=={PYANNOTE_AUDIO_VERSION}",
                f"hf_xet=={HF_XET_VERSION}",
            ],
        ),
    ]
    for label, command in commands:
        notify(label)
        _run_setup_command(command, label=label)

    validate_python_executable(runtime_python)
    doctor = doctor_runtime(runtime_python, validate=False)
    return {
        "schema_version": 1,
        "status": "pass" if doctor.get("status") == "pass" else "unhealthy",
        "runtime_dir": str(destination),
        "runtime_python": str(runtime_python),
        "doctor": doctor,
    }


def _write_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up or diagnose Alano Cut's shared CUDA WhisperX runtime"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser(
        "setup", help="Create/update the pinned shared runtime using uv"
    )
    setup_parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="Override the runtime directory (Windows: must be on C:)",
    )
    subparsers.add_parser(
        "doctor", help="Verify package versions, imports, CUDA, and the detected GPU"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            report = setup_runtime(
                args.runtime_dir,
                progress=lambda message: print(f"[whisperx setup] {message}...", file=sys.stderr),
            )
        else:
            report = doctor_runtime()
    except RuntimeContractError as exc:
        _write_json({"schema_version": 1, "status": "error", "error": str(exc)})
        return EXIT_ERROR

    _write_json(report)
    return EXIT_OK if report.get("status") == "pass" else EXIT_UNHEALTHY


if __name__ == "__main__":
    raise SystemExit(main())
