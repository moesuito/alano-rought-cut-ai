"""Transcribe one source with the local CUDA WhisperX stack.

The normative path is:

``faster-whisper large-v3 -> WhisperX forced alignment -> Community-1 diarization``

ElevenLabs remains an explicit compatibility provider, but it is never an
automatic fallback: losing forced alignment or local diarization must be a
visible operator decision.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

try:
    from helpers.transcription_contract import (
        DEFAULT_DIARIZATION_MODEL,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PORTUGUESE_HOTWORDS,
        DEFAULT_PORTUGUESE_INITIAL_PROMPT,
        TranscriptContractError,
        WhisperXConfig,
        is_cache_valid,
        sha256_file,
        validate_provisional_normative_transcript,
        write_json_atomic,
    )
    from helpers.transcription_providers import (
        WhisperXProvider,
        ensure_no_secret_fields,
        load_env_value,
    )
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_contract import (  # type: ignore[no-redef]
        DEFAULT_DIARIZATION_MODEL,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PORTUGUESE_HOTWORDS,
        DEFAULT_PORTUGUESE_INITIAL_PROMPT,
        TranscriptContractError,
        WhisperXConfig,
        is_cache_valid,
        sha256_file,
        validate_provisional_normative_transcript,
        write_json_atomic,
    )
    from transcription_providers import (  # type: ignore[no-redef]
        WhisperXProvider,
        ensure_no_secret_fields,
        load_env_value,
    )


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class TranscriptionReviewRequired(RuntimeError):
    """The transcript was persisted for audit but is unsafe for editing."""


def load_api_key() -> str:
    value = load_env_value("ELEVENLABS_API_KEY")
    if not value:
        raise RuntimeError("ELEVENLABS_API_KEY not found in .env or environment")
    return value


def extract_audio(video_path: Path, dest: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {completed.stderr.strip()}")


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict[str, Any]:
    """Compatibility adapter for explicit ElevenLabs use."""
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)
    with audio_path.open("rb") as handle:
        response = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, handle, "audio/wav")},
            data=data,
            timeout=1800,
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Scribe returned HTTP {response.status_code}; response body omitted"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Scribe returned a non-object response")
    return payload


def _scribe_transcript(
    source: Path,
    *,
    api_key: str,
    language: str | None,
    num_speakers: int | None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alano_cut_scribe_") as temp_dir:
        audio = Path(temp_dir) / f"{source.stem}.wav"
        extract_audio(source, audio)
        payload = call_scribe(audio, api_key, language, num_speakers)
    binding = payload.get("_alano_cut")
    if not isinstance(binding, dict):
        binding = {}
        payload["_alano_cut"] = binding
    binding.update(
        {
            "schema_version": 0,
            "transcription_provider": "elevenlabs_scribe_compatibility",
            "source_sha256": sha256_file(source),
        }
    )
    return payload


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = "pt",
    num_speakers: int | None = None,
    verbose: bool = True,
    force: bool = False,
    *,
    provider: str = "whisperx",
    config: WhisperXConfig | None = None,
    runtime_python: Path | None = None,
) -> Path:
    """Transcribe a source and return its canonical transcript path."""
    source = video.resolve(strict=True)
    transcripts_dir = edit_dir.resolve() / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    output = transcripts_dir / f"{source.stem}.json"
    source_hash = sha256_file(source)

    if provider == "whisperx":
        effective_config = config or WhisperXConfig(
            language=language,
            num_speakers=num_speakers,
        )
        if output.exists() and not force and is_cache_valid(
            output, source_sha256=source_hash, config=effective_config
        ):
            if verbose:
                print(f"cached: {output.name} (source + WhisperX config match)")
                cached_payload = json.loads(output.read_text(encoding="utf-8"))
                pending = validate_provisional_normative_transcript(cached_payload)
                if pending:
                    print(
                        f"audit pending: {len(pending)} acoustic component(s) "
                        "must be outside the eventual EDL selection",
                        flush=True,
                    )
            return output
    elif provider == "elevenlabs":
        effective_config = None
        if output.exists() and not force:
            try:
                existing = json.loads(output.read_text(encoding="utf-8"))
                metadata = existing.get("_alano_cut", {})
                if (
                    metadata.get("transcription_provider")
                    == "elevenlabs_scribe_compatibility"
                    and metadata.get("source_sha256") == source_hash
                ):
                    if verbose:
                        print(f"cached: {output.name} (ElevenLabs compatibility)")
                    return output
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
    else:
        raise ValueError(f"unsupported transcription provider: {provider}")

    if verbose:
        replacement = " replacing stale/legacy cache" if output.exists() else ""
        print(f"transcribing {source.name} with {provider}{replacement}", flush=True)
    started = time.perf_counter()
    if provider == "whisperx":
        payload = WhisperXProvider(
            effective_config,
            runtime_python=runtime_python,
            analysis_dir=edit_dir.resolve() / "audio_analysis",
        ).transcribe(source)
    else:
        payload = _scribe_transcript(
            source,
            api_key=api_key or load_api_key(),
            language=language,
            num_speakers=num_speakers,
        )
    ensure_no_secret_fields(payload)
    write_json_atomic(output, payload)

    pending_acoustic: list[dict[str, Any]] = []
    if provider == "whisperx":
        try:
            pending_acoustic = validate_provisional_normative_transcript(payload)
        except TranscriptContractError as error:
            alignment = payload.get("_alano_cut", {}).get("alignment", {})
            acoustic = payload.get("_alano_cut", {}).get("acoustic_timing", {})
            count = (
                int(alignment.get("blocking_outlier_count") or 0)
                + int(acoustic.get("blocking_outlier_count") or 0)
            )
            raise TranscriptionReviewRequired(
                f"transcription timing gate requires review ({count} blocking outlier(s)): "
                f"{error}; audit transcript saved at {output}"
            ) from error

    if verbose:
        elapsed = time.perf_counter() - started
        word_count = len(payload.get("words", []))
        speakers = {
            word.get("speaker_id")
            for word in payload.get("words", [])
            if isinstance(word, dict) and word.get("speaker_id") is not None
        }
        print(
            f"saved: {output.name} ({word_count} words, {len(speakers)} speakers) "
            f"in {elapsed:.1f}s",
            flush=True,
        )
        if pending_acoustic:
            print(
                f"audit pending: {len(pending_acoustic)} acoustic component(s) "
                "must be outside the eventual EDL selection",
                flush=True,
            )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe audio/video with CUDA WhisperX and Community-1"
    )
    parser.add_argument("source", type=Path, help="Audio or video source")
    parser.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <source_parent>/edit)",
    )
    parser.add_argument(
        "--provider",
        choices=("whisperx", "elevenlabs"),
        default="whisperx",
        help="Normative local provider or explicit compatibility provider",
    )
    parser.add_argument("--language", default="pt", help="Language code (default: pt)")
    parser.add_argument("--model", default="large-v3", help="faster-whisper model")
    parser.add_argument(
        "--align-model",
        default=DEFAULT_PORTUGUESE_ALIGN_MODEL,
        help="Forced-alignment model (default: Portuguese XLSR-53)",
    )
    parser.add_argument(
        "--diarization-model",
        default=DEFAULT_DIARIZATION_MODEL,
        choices=(DEFAULT_DIARIZATION_MODEL,),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="WhisperX GPU batch size (default: 2 for 6 GB VRAM)",
    )
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--initial-prompt", default=DEFAULT_PORTUGUESE_INITIAL_PROMPT)
    parser.add_argument("--hotwords", default=DEFAULT_PORTUGUESE_HOTWORDS)
    parser.add_argument(
        "--compute-type", choices=("float16", "int8_float16"), default="float16"
    )
    speaker_group = parser.add_mutually_exclusive_group()
    speaker_group.add_argument("--num-speakers", type=int, default=None)
    speaker_group.add_argument("--speaker-range", nargs=2, type=int, metavar=("MIN", "MAX"))
    parser.add_argument("--runtime-python", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = args.source.resolve()
    if not source.is_file():
        print(f"source not found: {source}", file=sys.stderr)
        return 1
    language = None if str(args.language).lower() == "auto" else args.language
    min_speakers = args.speaker_range[0] if args.speaker_range else None
    max_speakers = args.speaker_range[1] if args.speaker_range else None
    try:
        config = WhisperXConfig(
            model=args.model,
            language=language,
            compute_type=args.compute_type,
            batch_size=args.batch_size,
            beam_size=args.beam_size,
            initial_prompt=args.initial_prompt if language == "pt" else None,
            hotwords=args.hotwords if language == "pt" else None,
            align_model=args.align_model if language == "pt" else None,
            diarization_model=args.diarization_model,
            num_speakers=args.num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        transcribe_one(
            source,
            (args.edit_dir or source.parent / "edit").resolve(),
            language=language,
            num_speakers=args.num_speakers,
            force=args.force,
            provider=args.provider,
            config=config,
            runtime_python=args.runtime_python,
        )
    except TranscriptionReviewRequired as error:
        print(f"transcription review required: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        # Provider errors are designed not to contain credentials.  Avoid a
        # traceback here so third-party request objects cannot dump headers.
        print(f"transcription failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
