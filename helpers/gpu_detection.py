"""Hardware inspection and GPU runtime auto-detection for Alano Cut."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any


def detect_cuda_available() -> bool:
    """Check if an NVIDIA GPU with CUDA is available."""
    # 1. Try nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            completed = subprocess.run(
                ["nvidia-smi"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                return True
        except Exception:
            pass

    # 2. Check torch if available
    try:
        import torch
        if torch.cuda.is_available():
            return True
    except Exception:
        pass

    # 3. Check Windows video controllers
    if sys.platform == "win32":
        try:
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
            completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True, timeout=5)
            if completed.returncode == 0:
                output = completed.stdout.upper()
                if any(tag in output for tag in ["NVIDIA", "GEFORCE", "RTX", "GTX", "QUADRO", "TESLA"]):
                    return True
        except Exception:
            pass

    return False


def get_detected_gpus() -> list[dict[str, str]]:
    """Return a list of detected graphics devices with their vendor."""
    devices: list[dict[str, str]] = []
    if sys.platform == "win32":
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
            ]
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                for line in completed.stdout.splitlines():
                    name = line.strip()
                    if not name:
                        continue
                    upper = name.upper()
                    if any(k in upper for k in ["NVIDIA", "GEFORCE", "RTX", "GTX", "QUADRO"]):
                        vendor = "nvidia"
                    elif any(k in upper for k in ["AMD", "RADEON", "ATI"]):
                        vendor = "amd"
                    elif any(k in upper for k in ["INTEL", "ARC", "IRIS", "UHD"]):
                        vendor = "intel"
                    else:
                        vendor = "other"
                    devices.append({"name": name, "vendor": vendor})
        except Exception:
            pass
    return devices


def detect_recommended_runtime() -> dict[str, Any]:
    """Determine the optimal local transcription runtime based on available hardware.

    Priority:
      1. CUDA (WhisperX local) if NVIDIA GPU is present.
      2. Vulkan (Whisper Large Vulkan) for AMD, Intel, or cross-vendor GPU acceleration.
    """
    is_cuda = detect_cuda_available()
    devices = get_detected_gpus()
    runtime = "cuda" if is_cuda else "vulkan"
    return {
        "recommended_runtime": runtime,
        "cuda_available": is_cuda,
        "vulkan_available": True,
        "devices": devices,
    }


__all__ = [
    "detect_cuda_available",
    "detect_recommended_runtime",
    "get_detected_gpus",
]
