"""Persistent, secret-free transcription settings for Alano Cut.

The global file stores the user's preferred profile. Every initialized
workspace receives its own explicit ``alanocut.json`` so source and preview
transcription never depend on an implicit provider default. Credentials stay
in ``.env`` and are deliberately absent from this module's schema.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


SETTINGS_SCHEMA_VERSION = 1
WORKSPACE_SETTINGS_NAME = "alanocut.json"
USER_SETTINGS_NAME = "user-settings.json"
INSTALL_DIR_NAME = "alano-rought-cut-ai"

PROVIDER_VULKAN = "whisper-vulkan"
PROVIDER_ASSEMBLYAI = "assemblyai"
PROVIDER_ELEVENLABS = "elevenlabs"
PROVIDER_WHISPERX = "whisperx"  # Deprecated legacy alias

SUPPORTED_PROVIDERS = (
    PROVIDER_VULKAN,
    PROVIDER_ASSEMBLYAI,
    PROVIDER_ELEVENLABS,
)

DIARIZATION_COMMUNITY_1 = "community-1"
DIARIZATION_NONE = "none"
DIARIZATION_PROVIDER = "provider"
SUPPORTED_DIARIZATION = (
    DIARIZATION_COMMUNITY_1,
    DIARIZATION_NONE,
    DIARIZATION_PROVIDER,
)


class SettingsError(ValueError):
    """Raised when persisted or requested transcription settings are invalid."""


@dataclass(frozen=True, slots=True)
class TranscriptionSettings:
    provider: str = PROVIDER_VULKAN
    language: str = "pt"
    device: str = "vulkan"
    diarization: str = DIARIZATION_COMMUNITY_1

    def __post_init__(self) -> None:
        if self.provider == PROVIDER_WHISPERX:
            raise SettingsError(
                "WhisperX (CUDA-only) has been replaced by the unified Whisper Large Vulkan + DirectML runtime. "
                "Please configure provider='whisper-vulkan'."
            )
        if self.provider not in SUPPORTED_PROVIDERS:
            raise SettingsError(f"unsupported transcription provider: {self.provider}")
        if not str(self.language).strip():
            raise SettingsError("transcription language must not be empty")
        if self.diarization not in SUPPORTED_DIARIZATION:
            raise SettingsError(f"unsupported diarization mode: {self.diarization}")
        if self.provider == PROVIDER_VULKAN:
            if self.device != "vulkan":
                raise SettingsError("Vulkan Whisper requires device='vulkan'")
            if self.diarization not in {
                DIARIZATION_COMMUNITY_1,
                DIARIZATION_NONE,
            }:
                raise SettingsError(
                    "Vulkan Whisper diarization must be 'community-1' or 'none'"
                )
        else:
            if self.diarization != DIARIZATION_PROVIDER:
                raise SettingsError(f"{self.provider} must use provider diarization")
            if self.device != "cloud":
                raise SettingsError(f"{self.provider} device must be 'cloud'")

    @classmethod
    def vulkan(
        cls,
        *,
        diarization: str = DIARIZATION_COMMUNITY_1,
        language: str = "pt",
    ) -> "TranscriptionSettings":
        return cls(
            provider=PROVIDER_VULKAN,
            language=language,
            device="vulkan",
            diarization=diarization,
        )

    @classmethod
    def elevenlabs(cls, *, language: str = "pt") -> "TranscriptionSettings":
        return cls(
            provider=PROVIDER_ELEVENLABS,
            language=language,
            device="cloud",
            diarization=DIARIZATION_PROVIDER,
        )

    @classmethod
    def assemblyai(cls, *, language: str = "pt") -> "TranscriptionSettings":
        return cls(
            provider=PROVIDER_ASSEMBLYAI,
            language=language,
            device="cloud",
            diarization=DIARIZATION_PROVIDER,
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def install_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    appdata = values.get("APPDATA")
    base = Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming"
    return base / INSTALL_DIR_NAME


def user_settings_path(env: Mapping[str, str] | None = None) -> Path:
    return install_root(env) / USER_SETTINGS_NAME


def global_env_path(env: Mapping[str, str] | None = None) -> Path:
    return install_root(env) / ".env"


def workspace_settings_path(workspace: Path | str) -> Path:
    return Path(workspace).expanduser().resolve() / WORKSPACE_SETTINGS_NAME


def find_workspace(start: Path | str | None = None) -> Path | None:
    candidate = Path(start or Path.cwd()).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for root in (candidate, *candidate.parents):
        if (root / WORKSPACE_SETTINGS_NAME).is_file():
            return root
    return None


def _decode_settings(payload: object, *, source: Path) -> TranscriptionSettings:
    if not isinstance(payload, Mapping):
        raise SettingsError(f"settings file must contain a JSON object: {source}")
    if payload.get("schema_version") != SETTINGS_SCHEMA_VERSION:
        raise SettingsError(f"unsupported settings schema in {source}")
    transcription = payload.get("transcription")
    if not isinstance(transcription, Mapping):
        raise SettingsError(f"settings transcription object is missing: {source}")
    try:
        provider = str(transcription.get("provider") or "")
        # Automatic upgrade from deprecated whisperx
        if provider == PROVIDER_WHISPERX:
            provider = PROVIDER_VULKAN
            device = "vulkan"
            diarization = (
                DIARIZATION_COMMUNITY_1
                if transcription.get("diarization") == DIARIZATION_COMMUNITY_1
                else DIARIZATION_NONE
            )
        else:
            device = str(transcription.get("device") or "")
            diarization = str(transcription.get("diarization") or "")

        return TranscriptionSettings(
            provider=provider,
            language=str(transcription.get("language") or ""),
            device=device,
            diarization=diarization,
        )
    except (TypeError, SettingsError) as exc:
        raise SettingsError(f"invalid transcription settings in {source}: {exc}") from exc


def read_settings(path: Path | str) -> TranscriptionSettings:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"could not read settings from {source}: {exc}") from exc
    return _decode_settings(payload, source=source)


def settings_payload(settings: TranscriptionSettings) -> dict[str, Any]:
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "transcription": settings.to_dict(),
    }


def write_settings_atomic(path: Path | str, settings: TranscriptionSettings) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=destination.name + ".",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            json.dump(
                settings_payload(settings),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def resolve_settings(
    start: Path | str | None = None,
    *,
    allow_global: bool = True,
    default: TranscriptionSettings | None = None,
    env: Mapping[str, str] | None = None,
) -> TranscriptionSettings:
    """Resolve workspace -> user preference -> explicit compatibility default."""

    workspace = find_workspace(start)
    if workspace is not None:
        return read_settings(workspace / WORKSPACE_SETTINGS_NAME)
    if allow_global:
        global_path = user_settings_path(env)
        if global_path.is_file():
            return read_settings(global_path)
    if default is not None:
        return default
    raise SettingsError(
        "no transcription provider is configured; run `alanocut init` or "
        "`alanocut configure`"
    )


__all__ = [
    "DIARIZATION_COMMUNITY_1",
    "DIARIZATION_NONE",
    "DIARIZATION_PROVIDER",
    "PROVIDER_ASSEMBLYAI",
    "PROVIDER_ELEVENLABS",
    "PROVIDER_VULKAN",
    "PROVIDER_WHISPERX",
    "SETTINGS_SCHEMA_VERSION",
    "SUPPORTED_DIARIZATION",
    "SUPPORTED_PROVIDERS",
    "SettingsError",
    "TranscriptionSettings",
    "WORKSPACE_SETTINGS_NAME",
    "find_workspace",
    "global_env_path",
    "install_root",
    "read_settings",
    "resolve_settings",
    "settings_payload",
    "user_settings_path",
    "workspace_settings_path",
    "write_settings_atomic",
]
