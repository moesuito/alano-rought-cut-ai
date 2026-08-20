"""Transcribe one source with its explicit workspace provider profile.

WhisperX is CUDA-only and emits forced-aligned word timestamps, optionally
with Community-1 speakers. ElevenLabs Scribe is normalized from provider word
timestamps. The two providers never silently fall back into one another.
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
        DEFAULT_DIARIZATION_MODEL_REVISION,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PORTUGUESE_HOTWORDS,
        DEFAULT_PORTUGUESE_INITIAL_PROMPT,
        TranscriptContractError,
        WhisperXConfig,
        ElevenLabsConfig,
        AssemblyAIConfig,
        VulkanWhisperConfig,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
        convert_elevenlabs_result,
        convert_assemblyai_result,
        convert_vulkan_whisper_result,
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
    from helpers.transcription_settings import (
        PROVIDER_ASSEMBLYAI,
        PROVIDER_ELEVENLABS,
        PROVIDER_VULKAN,
        PROVIDER_WHISPERX,
        resolve_settings,
    )
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_contract import (  # type: ignore[no-redef]
        DEFAULT_DIARIZATION_MODEL,
        DEFAULT_DIARIZATION_MODEL_REVISION,
        DEFAULT_PORTUGUESE_ALIGN_MODEL,
        DEFAULT_PORTUGUESE_HOTWORDS,
        DEFAULT_PORTUGUESE_INITIAL_PROMPT,
        TranscriptContractError,
        WhisperXConfig,
        ElevenLabsConfig,
        AssemblyAIConfig,
        VulkanWhisperConfig,
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
        convert_elevenlabs_result,
        convert_assemblyai_result,
        convert_vulkan_whisper_result,
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
    from transcription_settings import (  # type: ignore[no-redef]
        PROVIDER_ASSEMBLYAI,
        PROVIDER_ELEVENLABS,
        PROVIDER_VULKAN,
        PROVIDER_WHISPERX,
        resolve_settings,
    )


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class TranscriptionReviewRequired(RuntimeError):
    """The transcript was persisted for audit but is unsafe for editing."""


def load_api_key() -> str:
    value = load_env_value("ELEVENLABS_API_KEY")
    if not value:
        raise RuntimeError("ELEVENLABS_API_KEY not found in .env or environment")
    return value


def extract_audio(video_path: Path, dest: Path, denoise: bool = True) -> None:
    """Extract 16kHz mono audio for transcription with optional DeepFilterNet 3 pre-denoising."""
    if denoise:
        try:
            from helpers.audio_analysis import denoise_deepfilternet, is_deepfilternet_available
        except ImportError:
            try:
                from audio_analysis import denoise_deepfilternet, is_deepfilternet_available
            except ImportError:
                is_deepfilternet_available = lambda: False
                denoise_deepfilternet = lambda s, **kw: None

        if is_deepfilternet_available():
            temp_48k = dest.with_suffix(".tmp48k.pcm")
            cmd_48k = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video_path),
                "-vn", "-ac", "1", "-ar", "48000",
                "-f", "s16le", str(temp_48k)
            ]
            completed = subprocess.run(cmd_48k, check=False, capture_output=True, text=True)
            if completed.returncode == 0 and temp_48k.exists():
                try:
                    import numpy as np
                    samples_48k = np.fromfile(temp_48k, dtype=np.int16)
                    denoised_48k = denoise_deepfilternet(samples_48k, atten_lim_db=100.0)
                    if denoised_48k is not None:
                        temp_denoised_48k = dest.with_suffix(".tmp_df48k.pcm")
                        denoised_48k.tofile(temp_denoised_48k)
                        cmd_resample = [
                            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "s16le", "-ac", "1", "-ar", "48000",
                            "-i", str(temp_denoised_48k),
                            "-ar", "16000",
                            "-c:a", "pcm_s16le",
                            str(dest)
                        ]
                        res = subprocess.run(cmd_resample, check=False, capture_output=True, text=True)
                        if res.returncode == 0 and dest.exists():
                            return
                finally:
                    for p in [temp_48k, dest.with_suffix(".tmp_df48k.pcm")]:
                        if p.exists():
                            p.unlink()

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
    """Call the explicitly selected ElevenLabs Scribe provider."""
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
    config: ElevenLabsConfig,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alano_cut_scribe_") as temp_dir:
        audio = Path(temp_dir) / f"{source.stem}.wav"
        extract_audio(source, audio)
        payload = call_scribe(audio, api_key, language, num_speakers)
    return convert_elevenlabs_result(
        payload,
        config=config,
        source_sha256=sha256_file(source),
    )


def call_assemblyai(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict[str, Any]:
    """Call the explicitly selected AssemblyAI provider."""
    headers = {"authorization": api_key}
    # 1. Upload audio
    with audio_path.open("rb") as handle:
        upload_resp = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=handle,
            timeout=600,
        )
    if upload_resp.status_code != 200:
        raise RuntimeError(f"AssemblyAI upload returned HTTP {upload_resp.status_code}")
    upload_url = upload_resp.json().get("upload_url")
    if not upload_url:
        raise RuntimeError("AssemblyAI upload did not return an upload_url")

    # 2. Submit job
    transcript_req: dict[str, Any] = {
        "audio_url": upload_url,
        "speaker_labels": True,
        "language_code": language or "pt",
        "speech_model": "best",
        "punctuate": True,
        "format_text": True,
    }
    if num_speakers is not None:
        transcript_req["speakers_expected"] = num_speakers

    resp = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=headers,
        json=transcript_req,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"AssemblyAI transcript submission returned HTTP {resp.status_code}")
    transcript_id = resp.json().get("id")
    if not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript id")

    # 3. Poll
    while True:
        poll_resp = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
            timeout=60,
        )
        if poll_resp.status_code != 200:
            raise RuntimeError(f"AssemblyAI polling returned HTTP {poll_resp.status_code}")
        data = poll_resp.json()
        status = data.get("status")
        if status == "completed":
            return data
        if status == "error":
            raise RuntimeError(f"AssemblyAI transcription failed: {data.get('error')}")
        time.sleep(2)


def _assemblyai_transcript(
    source: Path,
    *,
    api_key: str,
    language: str | None,
    num_speakers: int | None,
    config: AssemblyAIConfig,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alano_cut_assemblyai_") as temp_dir:
        audio = Path(temp_dir) / f"{source.stem}.wav"
        extract_audio(source, audio)
        payload = call_assemblyai(audio, api_key, language, num_speakers)
    return convert_assemblyai_result(
        payload,
        config=config,
        source_sha256=sha256_file(source),
    )


def _vulkan_transcript(
    source: Path,
    *,
    language: str | None,
    config: VulkanWhisperConfig,
) -> dict[str, Any]:
    try:
        from helpers.vulkan_runtime import transcribe_raw_audio
    except ModuleNotFoundError:
        try:
            from vulkan_runtime import transcribe_raw_audio
        except ModuleNotFoundError:
            raise RuntimeError("Vulkan runtime helper not found")

    diarization_turns = None
    if config.diarization_mode not in {DIARIZATION_NONE, "none"}:
        try:
            from helpers.directml_diarization import diarize_audio_file
        except ModuleNotFoundError:
            try:
                from directml_diarization import diarize_audio_file
            except ModuleNotFoundError:
                diarize_audio_file = None

        if diarize_audio_file is not None:
            diarization_turns = diarize_audio_file(source)

    with tempfile.TemporaryDirectory(prefix="alano_cut_vulkan_") as temp_dir:
        audio = Path(temp_dir) / f"{source.stem}.wav"
        extract_audio(source, audio, denoise=True)
        raw_json = transcribe_raw_audio(
            audio,
            language=language or "pt",
            model_name=config.model,
        )
    return convert_vulkan_whisper_result(
        raw_json,
        config=config,
        diarization=diarization_turns,
        source_sha256=sha256_file(source),
    )


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = "pt",
    num_speakers: int | None = None,
    verbose: bool = True,
    force: bool = False,
    *,
    provider: str = "configured",
    config: WhisperXConfig | ElevenLabsConfig | None = None,
    runtime_python: Path | None = None,
) -> Path:
    """Transcribe a source and return its canonical transcript path."""
    source = video.resolve(strict=True)
    transcripts_dir = edit_dir.resolve() / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    output = transcripts_dir / f"{source.stem}.json"
    source_hash = sha256_file(source)
    selected_settings = None
    if provider == "configured":
        selected_settings = resolve_settings(edit_dir)
        provider = selected_settings.provider
        language = (
            None
            if selected_settings.language.casefold() == "auto"
            else selected_settings.language
        )

    if provider == "whisperx":
        if config is None:
            diarization_mode = (
                selected_settings.diarization
                if selected_settings is not None
                else DIARIZATION_COMMUNITY_1
            )
            effective_config = WhisperXConfig(
                language=language,
                diarization_mode=diarization_mode,
                vad_method=(
                    "pyannote"
                    if diarization_mode == DIARIZATION_COMMUNITY_1
                    else "silero"
                ),
                diarization_model=(
                    DEFAULT_DIARIZATION_MODEL
                    if diarization_mode == DIARIZATION_COMMUNITY_1
                    else None
                ),
                diarization_model_revision=(
                    DEFAULT_DIARIZATION_MODEL_REVISION
                    if diarization_mode == DIARIZATION_COMMUNITY_1
                    else None
                ),
                num_speakers=num_speakers,
            )
        else:
            effective_config = config
        if not isinstance(effective_config, WhisperXConfig):
            raise ValueError("WhisperX provider requires WhisperXConfig")
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
        effective_config = config or ElevenLabsConfig(language=language)
        if not isinstance(effective_config, ElevenLabsConfig):
            raise ValueError("ElevenLabs provider requires ElevenLabsConfig")
        if output.exists() and not force and is_cache_valid(
            output, source_sha256=source_hash, config=effective_config
        ):
            if verbose:
                print(f"cached: {output.name} (source + ElevenLabs config match)")
            return output
    elif provider == "assemblyai":
        effective_config = config or AssemblyAIConfig(language_code=language or "pt")
        if not isinstance(effective_config, AssemblyAIConfig):
            raise ValueError("AssemblyAI provider requires AssemblyAIConfig")
        if output.exists() and not force and is_cache_valid(
            output, source_sha256=source_hash, config=effective_config
        ):
            if verbose:
                print(f"cached: {output.name} (source + AssemblyAI config match)")
            return output
    elif provider in {"whisper-vulkan", "vulkan"}:
        diarization_mode = (
            selected_settings.diarization
            if selected_settings is not None
            else DIARIZATION_NONE
        )
        effective_config = config or VulkanWhisperConfig(
            language=language or "pt",
            diarization_mode=diarization_mode,
        )
        if not isinstance(effective_config, VulkanWhisperConfig):
            raise ValueError("Vulkan Whisper provider requires VulkanWhisperConfig")
        if output.exists() and not force and is_cache_valid(
            output, source_sha256=source_hash, config=effective_config
        ):
            if verbose:
                print(f"cached: {output.name} (source + Vulkan config match)")
            return output
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
    elif provider == "elevenlabs":
        payload = _scribe_transcript(
            source,
            api_key=api_key or load_api_key(),
            language=language,
            num_speakers=num_speakers,
            config=effective_config,
        )
    elif provider == "assemblyai":
        assembly_key = api_key or load_env_value("ASSEMBLYAI_API_KEY")
        if not assembly_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY not found in .env or environment")
        payload = _assemblyai_transcript(
            source,
            api_key=assembly_key,
            language=language,
            num_speakers=num_speakers,
            config=effective_config,
        )
    elif provider in {"whisper-vulkan", "vulkan"}:
        payload = _vulkan_transcript(
            source,
            language=language,
            config=effective_config,
        )
    else:
        raise ValueError(f"unsupported transcription provider: {provider}")
    ensure_no_secret_fields(payload)
    write_json_atomic(output, payload)

    pending_acoustic: list[dict[str, Any]] = []
    if provider in {"whisperx", "elevenlabs", "assemblyai", "whisper-vulkan", "vulkan"}:
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
        description="Transcribe audio/video with the selected Alano Cut provider"
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
        choices=("configured", "whisperx", "elevenlabs", "assemblyai", "whisper-vulkan"),
        default="configured",
        help="Workspace provider by default, or an explicit audited override",
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
        "--diarization",
        choices=(DIARIZATION_COMMUNITY_1, DIARIZATION_NONE),
        default=None,
        help="WhisperX speaker mode (default: workspace setting)",
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
    try:
        resolved_settings = (
            resolve_settings(source.parent) if args.provider == "configured" else None
        )
        provider = resolved_settings.provider if resolved_settings else args.provider
        configured_language = resolved_settings.language if resolved_settings else args.language
        language = None if str(configured_language).lower() == "auto" else configured_language
        min_speakers = args.speaker_range[0] if args.speaker_range else None
        max_speakers = args.speaker_range[1] if args.speaker_range else None
        if provider == PROVIDER_WHISPERX:
            diarization_mode = (
                args.diarization
                or (resolved_settings.diarization if resolved_settings else DIARIZATION_COMMUNITY_1)
            )
            config: WhisperXConfig | ElevenLabsConfig | AssemblyAIConfig | VulkanWhisperConfig = WhisperXConfig(
                model=args.model,
                language=language,
                compute_type=args.compute_type,
                batch_size=args.batch_size,
                beam_size=args.beam_size,
                initial_prompt=args.initial_prompt if language == "pt" else None,
                hotwords=args.hotwords if language == "pt" else None,
                align_model=args.align_model if language == "pt" else None,
                vad_method=("pyannote" if diarization_mode == DIARIZATION_COMMUNITY_1 else "silero"),
                diarization_mode=diarization_mode,
                diarization_model=(
                    args.diarization_model
                    if diarization_mode == DIARIZATION_COMMUNITY_1
                    else None
                ),
                diarization_model_revision=(
                    DEFAULT_DIARIZATION_MODEL_REVISION
                    if diarization_mode == DIARIZATION_COMMUNITY_1
                    else None
                ),
                num_speakers=args.num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        elif provider == PROVIDER_ELEVENLABS:
            config = ElevenLabsConfig(language=language)
        elif provider == PROVIDER_ASSEMBLYAI:
            config = AssemblyAIConfig(language_code=language or "pt")
        elif provider in {PROVIDER_VULKAN, "vulkan"}:
            diarization_mode = (
                args.diarization
                or (resolved_settings.diarization if resolved_settings else DIARIZATION_NONE)
            )
            config = VulkanWhisperConfig(
                language=language or "pt",
                diarization_mode=diarization_mode,
            )
        else:
            raise ValueError(f"unsupported transcription provider: {provider}")
        transcribe_one(
            source,
            (args.edit_dir or source.parent / "edit").resolve(),
            language=language,
            num_speakers=args.num_speakers,
            force=args.force,
            provider=provider,
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
