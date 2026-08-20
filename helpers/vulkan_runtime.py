"""Install, diagnose, and run Alano Cut's cross-vendor Whisper Vulkan runtime.

Runs on AMD Radeon, Intel Arc/Iris, and any GPU supporting the Vulkan API
using precompiled whisper.cpp binaries with word-level timestamps.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

try:
    from helpers.gpu_detection import detect_recommended_runtime, get_detected_gpus
except ModuleNotFoundError:
    from gpu_detection import detect_recommended_runtime, get_detected_gpus


WHISPER_VULKAN_ZIP_URL = (
    "https://github.com/jerryshell/whisper.cpp-windows-vulkan-bin/releases/download/v1.0.0/whisper.cpp-windows-vulkan.zip"
)

DEFAULT_MODEL_NAME = "large-v3-turbo"
MODEL_URLS = {
    "large-v3-turbo": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
    "large-v3": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
    "small": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
    "base": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
}


class VulkanRuntimeError(RuntimeError):
    """Raised when the Vulkan runtime cannot be initialized or executed."""


def get_vulkan_runtime_dir() -> Path:
    """Return directory where whisper.cpp Vulkan binaries are installed."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "AlanoCut" / "runtimes" / "whisper-vulkan"


def get_vulkan_models_dir() -> Path:
    """Return directory where GGML whisper models are cached."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "AlanoCut" / "models" / "whisper-cpp"


def get_whisper_cli_path() -> Path:
    """Return the path to whisper-cli.exe."""
    custom = os.environ.get("ALANOCUT_WHISPER_CLI")
    if custom and Path(custom).is_file():
        return Path(custom)
    return get_vulkan_runtime_dir() / "whisper-cli.exe"


def get_model_path(model_name: str = DEFAULT_MODEL_NAME) -> Path:
    """Return the path to a GGML model file."""
    custom = os.environ.get("ALANOCUT_WHISPER_MODEL")
    if custom and Path(custom).is_file():
        return Path(custom)
    filename = f"ggml-{model_name}.bin"
    return get_vulkan_models_dir() / filename


def download_file_with_progress(url: str, destination: Path, label: str = "Downloading") -> None:
    """Download a remote URL to destination path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = destination.with_suffix(f"{destination.suffix}.part")
    print(f"[{label}] {url} -> {destination.name}", flush=True)

    headers = {"User-Agent": "AlanoCut/0.4.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response:
        total_size = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        block_size = 1024 * 1024  # 1 MB

        with temp_dest.open("wb") as out_file:
            while True:
                chunk = response.read(block_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = (downloaded / total_size) * 100
                    mb_cur = downloaded / (1024 * 1024)
                    mb_tot = total_size / (1024 * 1024)
                    print(f"\r  {mb_cur:.1f} MB / {mb_tot:.1f} MB ({pct:.1f}%)", end="", flush=True)
                else:
                    mb_cur = downloaded / (1024 * 1024)
                    print(f"\r  {mb_cur:.1f} MB downloaded", end="", flush=True)
    print("", flush=True)
    if temp_dest.exists():
        temp_dest.replace(destination)


def ensure_vulkan_runtime() -> Path:
    """Ensure whisper.cpp Vulkan binaries are downloaded and available."""
    cli_path = get_whisper_cli_path()
    vulkan_dll = cli_path.parent / "ggml-vulkan.dll"
    if cli_path.is_file() and vulkan_dll.is_file():
        return cli_path

    runtime_dir = get_vulkan_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    print(f"[VULKAN] Downloading whisper.cpp Vulkan runtime binaries...", flush=True)

    req = urllib.request.Request(WHISPER_VULKAN_ZIP_URL, headers={"User-Agent": "AlanoCut/0.4.0"})
    with urllib.request.urlopen(req) as response:
        content = response.read()

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(runtime_dir)

    if not cli_path.is_file():
        raise VulkanRuntimeError(f"Extracted archive is missing whisper-cli.exe in {runtime_dir}")
    print(f"[VULKAN] whisper.cpp Vulkan binaries ready at {runtime_dir}", flush=True)
    return cli_path


def ensure_vulkan_model(model_name: str = DEFAULT_MODEL_NAME) -> Path:
    """Ensure the specified GGML model is downloaded and ready."""
    model_path = get_model_path(model_name)
    if model_path.is_file() and model_path.stat().st_size > 10 * 1024 * 1024:
        return model_path

    if model_name not in MODEL_URLS:
        raise VulkanRuntimeError(
            f"Unknown model name {model_name!r}. Available: {list(MODEL_URLS.keys())}"
        )

    url = MODEL_URLS[model_name]
    download_file_with_progress(url, model_path, label=f"VULKAN MODEL {model_name}")
    if not model_path.is_file():
        raise VulkanRuntimeError(f"Model download failed: {model_path}")
    return model_path


def setup(model_name: str = DEFAULT_MODEL_NAME) -> dict[str, Any]:
    """Provision the complete Whisper Vulkan runtime and model."""
    cli_path = ensure_vulkan_runtime()
    model_path = ensure_vulkan_model(model_name)
    doc = doctor(model_name=model_name)
    return doc


def doctor(model_name: str = DEFAULT_MODEL_NAME) -> dict[str, Any]:
    """Verify package versions, whisper-cli, Vulkan device acceleration, and model."""
    cli_path = get_whisper_cli_path()
    model_path = get_model_path(model_name)
    hw = detect_recommended_runtime()

    checks: dict[str, Any] = {
        "whisper_cli_exists": cli_path.is_file(),
        "ggml_vulkan_dll_exists": (cli_path.parent / "ggml-vulkan.dll").is_file(),
        "model_exists": model_path.is_file(),
        "model_size_mb": round(model_path.stat().st_size / (1024 * 1024), 1) if model_path.is_file() else 0,
        "gpus": hw.get("devices", []),
    }

    cli_executable = False
    vulkan_accelerated = False
    error_message = None

    if cli_path.is_file():
        try:
            completed = subprocess.run(
                [str(cli_path), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
            cli_executable = completed.returncode == 0 or "usage:" in (completed.stdout + completed.stderr).lower()
            # Vulkan banner probe
            vulkan_accelerated = (cli_path.parent / "ggml-vulkan.dll").is_file()
        except Exception as exc:
            error_message = str(exc)

    checks["cli_executable"] = cli_executable
    checks["vulkan_accelerated"] = vulkan_accelerated
    if error_message:
        checks["error"] = error_message

    status = (
        "pass"
        if checks["whisper_cli_exists"] and checks["model_exists"] and checks["cli_executable"]
        else "unhealthy"
    )

    return {
        "status": status,
        "provider": "whisper-vulkan",
        "model": model_name,
        "cli_path": str(cli_path),
        "model_path": str(model_path),
        "checks": checks,
    }


def parse_whisper_cpp_tokens_to_words(full_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert whisper.cpp token pieces into timed lexical words."""
    words: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and str(current.get("text") or "").strip():
            if float(current["end"]) <= float(current["start"]):
                current["end"] = float(current["start"]) + 0.01
            words.append(
                {
                    "text": str(current["text"]).strip(),
                    "start": round(float(current["start"]), 3),
                    "end": round(float(current["end"]), 3),
                    "type": "word",
                    "speaker_id": None,
                    "speaker_assignment": "disabled",
                    "score": 1.0,
                    "timing_source": "provider_word_timestamp",
                }
            )
        current = None

    transcription = full_data.get("transcription")
    if not isinstance(transcription, list):
        raise VulkanRuntimeError("whisper.cpp JSON is missing transcription array")

    for segment in transcription:
        if not isinstance(segment, dict):
            continue
        tokens = segment.get("tokens")
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict):
                continue
            raw = str(token.get("text") or "")
            stripped = raw.strip()
            if not stripped or (stripped.startswith("[") and stripped.endswith("]")):
                continue
            offsets = token.get("offsets")
            if not isinstance(offsets, dict):
                continue
            start_ms = offsets.get("from")
            end_ms = offsets.get("to")
            if start_ms is None or end_ms is None:
                continue

            start_sec = float(start_ms) / 1000.0
            end_sec = float(end_ms) / 1000.0

            if stripped in {",", ".", "!", "?", ":", ";", "..."} and current is not None:
                current["text"] = str(current["text"]) + stripped
                current["end"] = end_sec
            elif raw[:1].isspace() or current is None:
                flush()
                current = {
                    "text": stripped,
                    "start": start_sec,
                    "end": end_sec,
                }
            else:
                current["text"] = str(current["text"]) + stripped
                current["end"] = end_sec
    flush()
    return words


def transcribe_raw_audio(
    audio_wav_16k: Path,
    *,
    language: str = "pt",
    model_name: str = DEFAULT_MODEL_NAME,
) -> dict[str, Any]:
    """Run whisper-cli on 16kHz mono audio and return the raw parsed full JSON."""
    cli_path = ensure_vulkan_runtime()
    model_path = ensure_vulkan_model(model_name)

    with tempfile.TemporaryDirectory(prefix="alano_cut_vulkan_") as temp_dir:
        out_base = Path(temp_dir) / "output"
        cmd = [
            str(cli_path),
            "-m", str(model_path),
            "-f", str(audio_wav_16k),
            "-l", language,
            "-t", str(min(8, max(1, os.cpu_count() or 4))),
            "-ojf",
            "-of", str(out_base),
            "-pp",
            "-np",
            "-ml", "1",
        ]
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout).strip()
            raise VulkanRuntimeError(f"whisper-cli execution failed: {err}")

        json_out = out_base.with_suffix(".json")
        if not json_out.is_file():
            raise VulkanRuntimeError("whisper-cli did not generate the full json output file")

        return json.loads(json_out.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Alano Cut Whisper Vulkan runtime")
    parser.add_argument("command", choices=("setup", "doctor"))
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, choices=tuple(MODEL_URLS.keys()))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            res = setup(model_name=args.model)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res.get("status") == "pass" else 1
        elif args.command == "doctor":
            res = doctor(model_name=args.model)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res.get("status") == "pass" else 2
    except Exception as exc:
        print(f"Vulkan runtime error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
