"""Transcription provider utilities shared by source and preview workflows."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from helpers.transcription_settings import global_env_path
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_settings import global_env_path  # type: ignore[no-redef]


class TranscriptionProviderError(RuntimeError):
    """Safe operational failure from a provider (never contains credentials)."""


def _env_candidates(start: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in (start, Path.cwd(), Path(__file__).resolve().parent.parent):
        if root is None:
            continue
        root = root.resolve()
        if root.is_file():
            root = root.parent
        roots.extend([root, *root.parents])
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidate = root / ".env"
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    shared = global_env_path().resolve()
    if shared not in seen:
        candidates.append(shared)
    return candidates


def load_env_value(name: str, *, start: Path | None = None) -> str | None:
    """Load one value from the process or nearest .env without logging it."""
    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    for candidate in _env_candidates(start):
        if not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                resolved = value.strip().strip('"').strip("'")
                if resolved:
                    return resolved
    return None


def ensure_no_secret_fields(value: object) -> None:
    """Reject accidental credential-shaped fields before a transcript is saved."""
    secret_names = {"hf_token", "token", "authorization", "api_key", "access_token"}

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).lower() in secret_names:
                    raise TranscriptionProviderError(
                        "transcription output contains a forbidden credential field"
                    )
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)


__all__ = [
    "TranscriptionProviderError",
    "ensure_no_secret_fields",
    "load_env_value",
]
