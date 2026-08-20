"""Sequential transcription for every media source in a directory."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    from helpers.transcribe import transcribe_one
    from helpers.transcription_contract import (
        ElevenLabsConfig,
        AssemblyAIConfig,
        VulkanWhisperConfig,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
    )
    from helpers.transcription_settings import PROVIDER_VULKAN, resolve_settings
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcribe import transcribe_one  # type: ignore[no-redef]
    from transcription_contract import (  # type: ignore[no-redef]
        ElevenLabsConfig,
        AssemblyAIConfig,
        VulkanWhisperConfig,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
    )
    from transcription_settings import PROVIDER_VULKAN, resolve_settings  # type: ignore[no-redef]


MEDIA_EXTENSIONS = {
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


def find_sources(directory: Path, *, recursive: bool = False) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    sources = sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )
    by_stem: dict[str, list[Path]] = {}
    for source in sources:
        by_stem.setdefault(source.stem.casefold(), []).append(source)
    collisions = [paths for paths in by_stem.values() if len(paths) > 1]
    if collisions:
        descriptions = "; ".join(
            ", ".join(str(path) for path in paths) for paths in collisions
        )
        raise ValueError(
            "multiple sources would overwrite the same transcript stem: " + descriptions
        )
    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe a media directory with the selected provider"
    )
    parser.add_argument("sources_dir", type=Path)
    parser.add_argument("--edit-dir", type=Path, default=None)
    parser.add_argument(
        "--provider",
        choices=("configured", "whisper-vulkan", "vulkan", "assemblyai", "elevenlabs"),
        default="configured",
    )
    parser.add_argument("--language", default="pt")
    parser.add_argument(
        "--diarization",
        choices=(DIARIZATION_COMMUNITY_1, DIARIZATION_NONE),
        default=None,
    )
    speaker_group = parser.add_mutually_exclusive_group()
    speaker_group.add_argument("--num-speakers", type=int, default=None)
    speaker_group.add_argument("--speaker-range", nargs=2, type=int, metavar=("MIN", "MAX"))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    sources_dir = args.sources_dir.resolve()
    if not sources_dir.is_dir():
        print(f"not a directory: {sources_dir}", file=sys.stderr)
        return 1
    try:
        sources = find_sources(sources_dir, recursive=args.recursive)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if not sources:
        print(f"no supported media found in {sources_dir}", file=sys.stderr)
        return 1

    try:
        settings = resolve_settings(sources_dir) if args.provider == "configured" else None
        provider = settings.provider if settings else args.provider
        configured_language = settings.language if settings else args.language
        language = None if str(configured_language).lower() == "auto" else configured_language
        if provider in {"whisper-vulkan", "vulkan", "whisperx", PROVIDER_VULKAN}:
            diarization_mode = (
                args.diarization
                or (settings.diarization if settings else DIARIZATION_COMMUNITY_1)
            )
            config = VulkanWhisperConfig(
                language=language or "pt",
                diarization_mode=diarization_mode,
            )
        elif provider == "elevenlabs":
            config = ElevenLabsConfig(language=language)
        elif provider == "assemblyai":
            config = AssemblyAIConfig(language_code=language or "pt")
        else:
            raise ValueError(f"unsupported transcription provider: {provider}")
    except Exception as error:
        print(f"invalid transcription configuration: {error}", file=sys.stderr)
        return 1

    edit_dir = (args.edit_dir or sources_dir / "edit").resolve()
    print(
        f"found {len(sources)} source(s); provider={provider}",
        flush=True,
    )
    started = time.perf_counter()
    failures: list[tuple[Path, str]] = []
    for index, source in enumerate(sources, 1):
        print(f"[{index}/{len(sources)}] {source.name}", flush=True)
        try:
            transcribe_one(
                source,
                edit_dir,
                language=language,
                num_speakers=args.num_speakers,
                force=args.force,
                provider=provider,
                config=config,
            )
        except Exception as error:
            failures.append((source, f"{type(error).__name__}: {error}"))
            print(f"  failed: {type(error).__name__}: {error}", file=sys.stderr)

    elapsed = time.perf_counter() - started
    print(f"completed in {elapsed:.1f}s; failures={len(failures)}", flush=True)
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
