"""Prefetch and diagnose the immutable model set for a transcription profile."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from helpers.pinned_silero import SILERO_REPOSITORY, SILERO_REVISION, load_pinned_silero
    from helpers.transcription_contract import (
        DEFAULT_ALIGN_MODEL_REVISION,
        DEFAULT_DIARIZATION_MODEL,
        DEFAULT_DIARIZATION_MODEL_REVISION,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PRIMARY_MODEL_REVISION,
        DEFAULT_SEMANTIC_VERIFIER_MODEL,
        DEFAULT_SEMANTIC_VERIFIER_REVISION,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
    )
    from helpers.transcription_settings import (
        TranscriptionSettings,
        resolve_settings,
    )
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from pinned_silero import SILERO_REPOSITORY, SILERO_REVISION, load_pinned_silero  # type: ignore[no-redef]
    from transcription_contract import (  # type: ignore[no-redef]
        DEFAULT_ALIGN_MODEL_REVISION,
        DEFAULT_DIARIZATION_MODEL,
        DEFAULT_DIARIZATION_MODEL_REVISION,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PRIMARY_MODEL_REVISION,
        DEFAULT_SEMANTIC_VERIFIER_MODEL,
        DEFAULT_SEMANTIC_VERIFIER_REVISION,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
    )
    from transcription_settings import TranscriptionSettings, resolve_settings  # type: ignore[no-redef]


MODEL_CACHE_ENV = "ALANOCUT_MODEL_CACHE"
MODEL_CACHE_DIR_NAME = "AlanoCut"
MODEL_ESTIMATE_GIB = 8
TOTAL_LOCAL_ESTIMATE_GIB = 16


class ModelSetupError(RuntimeError):
    """A selected transcription profile cannot acquire or verify its models."""


def default_model_cache(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    explicit = values.get(MODEL_CACHE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    local = values.get("LOCALAPPDATA")
    base = Path(local).expanduser() if local else Path.home() / "AppData" / "Local"
    return base / MODEL_CACHE_DIR_NAME / "models"


def _snapshot(
    repo_id: str,
    revision: str,
    destination: Path,
    *,
    local_only: bool,
    token: str | None = None,
) -> str:
    from huggingface_hub import snapshot_download

    kwargs: dict[str, Any] = {
        "repo_id": repo_id,
        "revision": revision,
        "cache_dir": str(destination),
        "local_files_only": local_only,
    }
    if token:
        kwargs["token"] = token
    return str(snapshot_download(**kwargs))


def _required_specs(settings: TranscriptionSettings) -> list[tuple[str, str, Path]]:
    root = default_model_cache()
    specs = [
        (
            "faster-whisper-large-v3",
            "Systran/faster-whisper-large-v3",
            root / "faster-whisper",
        ),
        (
            "faster-whisper-small",
            f"Systran/faster-whisper-{DEFAULT_SEMANTIC_VERIFIER_MODEL}",
            root / "faster-whisper-secondary",
        ),
        ("portuguese-alignment", DEFAULT_PORTUGUESE_ALIGN_MODEL, root / "alignment"),
    ]
    if settings.diarization == DIARIZATION_COMMUNITY_1:
        specs.append(("community-1", DEFAULT_DIARIZATION_MODEL, root / "pyannote"))
    return specs


def _revision_for(name: str) -> str:
    return {
        "faster-whisper-large-v3": DEFAULT_PRIMARY_MODEL_REVISION,
        "faster-whisper-small": DEFAULT_SEMANTIC_VERIFIER_REVISION,
        "portuguese-alignment": DEFAULT_ALIGN_MODEL_REVISION,
        "community-1": DEFAULT_DIARIZATION_MODEL_REVISION,
    }[name]


def prefetch_models(
    settings: TranscriptionSettings,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Download every model selected by *settings* into the shared cache."""

    if settings.provider != "whisperx":
        return {"status": "pass", "provider": settings.provider, "models": []}
    notify = progress or (lambda _message: None)
    cache = default_model_cache()
    cache.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if settings.diarization == DIARIZATION_COMMUNITY_1 and not token:
        raise ModelSetupError("HF_TOKEN is required to download Community-1")

    downloaded: list[dict[str, str]] = []
    for name, repository, destination in _required_specs(settings):
        notify(f"Downloading {name}")
        downloaded.append(
            {
                "name": name,
                "repository": repository,
                "revision": _revision_for(name),
                "path": _snapshot(
                    repository,
                    _revision_for(name),
                    destination,
                    local_only=False,
                    token=token if name == "community-1" else None,
                ),
            }
        )
    if settings.diarization == DIARIZATION_COMMUNITY_1:
        # Community-1 has transitive pipeline artifacts in addition to the
        # repository snapshot.  Loading it once is the authoritative way to
        # acquire that complete set before an edit starts.
        notify("Validating Community-1 pipeline")
        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained(
            DEFAULT_DIARIZATION_MODEL,
            revision=DEFAULT_DIARIZATION_MODEL_REVISION,
            token=token,
            cache_dir=str(cache / "pyannote"),
        )
        if pipeline is None:
            raise ModelSetupError("Community-1 could not be initialized after download")
    if settings.diarization == DIARIZATION_NONE:
        notify("Downloading pinned Silero VAD")
        load_pinned_silero(cache)
        downloaded.append(
            {
                "name": "silero-vad",
                "repository": SILERO_REPOSITORY,
                "revision": SILERO_REVISION,
                "path": str(cache / "torch-hub"),
            }
        )
    return {
        "status": "pass",
        "provider": settings.provider,
        "diarization": settings.diarization,
        "cache": str(cache),
        "models": downloaded,
    }


def doctor_models(settings: TranscriptionSettings) -> dict[str, Any]:
    """Check that selected models are cached, without downloading anything."""

    if settings.provider != "whisperx":
        return {"status": "pass", "provider": settings.provider, "checks": {}}
    cache = default_model_cache()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    checks: dict[str, bool] = {}
    for name, repository, destination in _required_specs(settings):
        try:
            _snapshot(
                repository,
                _revision_for(name),
                destination,
                local_only=True,
                token=token if name == "community-1" else None,
            )
            checks[name] = True
        except Exception:
            checks[name] = False
    if settings.diarization == DIARIZATION_NONE:
        repository_dir = f"{SILERO_REPOSITORY.replace('/', '_')}_{SILERO_REVISION}"
        checks["silero-vad"] = (
            cache / "torch-hub" / repository_dir / "hubconf.py"
        ).is_file()
    return {
        "status": "pass" if all(checks.values()) else "unhealthy",
        "provider": settings.provider,
        "diarization": settings.diarization,
        "cache": str(cache),
        "checks": checks,
        "failed_checks": [name for name, valid in checks.items() if not valid],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prefetch Alano Cut transcription models")
    parser.add_argument("command", choices=("prefetch", "doctor"))
    parser.add_argument("--profile", choices=("community-1", "none"), default=None)
    parser.add_argument("--language", default="pt")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.profile:
            settings = TranscriptionSettings.whisperx(
                diarization=args.profile,
                language=args.language,
            )
        else:
            settings = resolve_settings(default=TranscriptionSettings.whisperx())
            if settings.provider != "whisperx":
                print(json.dumps({"status": "pass", "provider": settings.provider}, indent=2))
                return 0
        if args.command == "prefetch":
            result = prefetch_models(settings, progress=lambda text: print(text, file=sys.stderr))
        else:
            result = doctor_models(settings)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stdout)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
