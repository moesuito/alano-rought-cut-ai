"""Transcription providers shared by source and preview workflows.

WhisperX is the normative provider.  Its heavy ML stack runs in a dedicated
CUDA runtime and receives the Hugging Face token only through its environment.
The lightweight caller never places credentials in argv, JSON, reports, or
exceptions.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

try:
    from helpers.transcription_contract import (
        TranscriptContractError,
        WhisperXConfig,
        analyze_alignment_quality,
        convert_whisperx_result,
        sha256_file,
        validate_transcript,
        validate_normative_transcript,
    )
    from helpers.transcript_audio_snap import refine_aligned_result
    from helpers.whisperx_runtime import locate_runtime_python
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_contract import (  # type: ignore[no-redef]
        TranscriptContractError,
        WhisperXConfig,
        analyze_alignment_quality,
        convert_whisperx_result,
        sha256_file,
        validate_transcript,
        validate_normative_transcript,
    )
    from transcript_audio_snap import refine_aligned_result  # type: ignore[no-redef]
    from whisperx_runtime import locate_runtime_python  # type: ignore[no-redef]


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


def whisperx_worker_config(config: WhisperXConfig) -> dict[str, Any]:
    return {
        "asr_model": config.model,
        "asr_model_revision": config.model_revision,
        "semantic_verifier_model": config.semantic_verifier_model,
        "semantic_verifier_revision": config.semantic_verifier_revision,
        "semantic_fusion_mode": config.semantic_fusion_mode,
        "semantic_fusion_revision": config.semantic_fusion_revision,
        "recording_cues": config.recording_cues,
        "language": config.language,
        "device": config.device,
        "compute_type": config.compute_type,
        "batch_size": config.batch_size,
        "beam_size": config.beam_size,
        "initial_prompt": config.initial_prompt,
        "hotwords": config.hotwords,
        "vad_method": config.vad_method,
        "align_model": config.align_model,
        "align_model_revision": config.align_model_revision,
        "diarization_model": config.diarization_model,
        "diarization_model_revision": config.diarization_model_revision,
        "num_speakers": config.num_speakers,
        "min_speakers": config.min_speakers,
        "max_speakers": config.max_speakers,
        "model_cache_dir": os.environ.get("ALANOCUT_MODEL_CACHE") or None,
    }


class WhisperXProvider:
    """CUDA WhisperX + forced alignment + Community-1 diarization provider."""

    def __init__(
        self,
        config: WhisperXConfig | None = None,
        *,
        runtime_python: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        analysis_dir: Path | None = None,
        acoustic_refiner: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = refine_aligned_result,
    ) -> None:
        self.config = config or WhisperXConfig()
        if self.config.device != "cuda":
            raise TranscriptContractError("WhisperXProvider requires CUDA")
        self.runtime_python = runtime_python
        self.runner = runner
        self.analysis_dir = analysis_dir
        self.acoustic_refiner = acoustic_refiner

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        source = audio_path.resolve(strict=True)
        token = load_env_value("HF_TOKEN", start=source.parent) or load_env_value(
            "HUGGING_FACE_HUB_TOKEN", start=source.parent
        )
        if not token:
            raise TranscriptionProviderError(
                "HF_TOKEN is not configured; Community-1 diarization is mandatory"
            )
        try:
            runtime_python = self.runtime_python or locate_runtime_python()
        except Exception as error:
            raise TranscriptionProviderError(
                "the shared WhisperX CUDA runtime is missing or unhealthy; "
                "run `alanocut setup-transcription`"
            ) from error

        worker = Path(__file__).resolve().with_name("whisperx_worker.py")
        if not worker.is_file():
            raise TranscriptionProviderError("WhisperX worker is missing from the installation")

        with tempfile.TemporaryDirectory(prefix="alano_cut_whisperx_") as temp_dir:
            temp_root = Path(temp_dir)
            config_path = temp_root / "config.json"
            result_path = temp_root / "result.json"
            config_path.write_text(
                json.dumps(whisperx_worker_config(self.config), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["HF_TOKEN"] = token
            environment.pop("HUGGING_FACE_HUB_TOKEN", None)
            try:
                completed = self.runner(
                    [
                        str(runtime_python),
                        str(worker),
                        str(source),
                        "--config",
                        str(config_path),
                        "--output",
                        str(result_path),
                    ],
                    check=False,
                    env=environment,
                    timeout=7200,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise TranscriptionProviderError(
                    "the WhisperX CUDA worker could not complete"
                ) from error
            if completed.returncode != 0 or not result_path.is_file():
                raise TranscriptionProviderError(
                    "the WhisperX CUDA worker failed; see its redacted diagnostic above"
                )
            try:
                raw = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise TranscriptionProviderError(
                    "the WhisperX CUDA worker returned invalid JSON"
                ) from error

        ensure_no_secret_fields(raw)
        if raw.get("worker_schema_version") != 2:
            raise TranscriptionProviderError("unsupported WhisperX worker schema")
        expected_models = {
            "asr": self.config.model,
            "semantic_verifier": self.config.semantic_verifier_model,
            "alignment": self.config.align_model,
            "diarization": self.config.diarization_model,
        }
        expected_revisions = {
            "asr": self.config.model_revision,
            "semantic_verifier": self.config.semantic_verifier_revision,
            "alignment": self.config.align_model_revision,
            "diarization": self.config.diarization_model_revision,
        }
        if raw.get("models") != expected_models:
            raise TranscriptionProviderError("WhisperX worker model binding mismatch")
        if raw.get("model_revisions") != expected_revisions:
            raise TranscriptionProviderError("WhisperX worker model revision mismatch")
        runtime = raw.get("runtime")
        if not isinstance(runtime, Mapping) or (
            runtime.get("compute_type") != self.config.compute_type
            or runtime.get("batch_size") != self.config.batch_size
        ):
            raise TranscriptionProviderError(
                "WhisperX worker changed compute_type or batch_size; rerun with the "
                "effective values explicitly configured"
            )
        semantic_verification = raw.get("semantic_verification")
        if not isinstance(semantic_verification, Mapping) or (
            semantic_verification.get("status") != "pass"
            or semantic_verification.get("mode") != self.config.semantic_fusion_mode
            or semantic_verification.get("revision")
            != self.config.semantic_fusion_revision
            or semantic_verification.get("asr_mode") != self.config.vad_method
            or semantic_verification.get("coverage_asr_mode") != "windowed_no_vad"
            or semantic_verification.get("semantic_source") != "semantic_verifier"
        ):
            raise TranscriptionProviderError("semantic cue verification is missing")

        source_hash = sha256_file(source)
        if raw.get("source_sha256") != source_hash:
            raise TranscriptionProviderError("WhisperX result source hash mismatch")
        default_analysis_dir = (
            source.parent / "audio_analysis"
            if source.parent.name.casefold() == "edit"
            else source.parent / "edit" / "audio_analysis"
        )
        try:
            raw, acoustic_timing = self.acoustic_refiner(
                source,
                raw,
                diarization=raw.get("diarization") or [],
                semantic_verification=raw.get("semantic_verification"),
                analysis_dir=(self.analysis_dir or default_analysis_dir).resolve(),
            )
        except Exception as error:
            raise TranscriptionProviderError(
                f"acoustic word validation failed: {type(error).__name__}: {error}"
            ) from error
        transcript = convert_whisperx_result(
            raw,
            raw.get("diarization"),
            config=self.config,
            source_sha256=source_hash,
        )
        metadata = transcript["_alano_cut"]
        alignment_quality = analyze_alignment_quality(transcript)
        metadata.update(
            {
                "models": raw.get("models"),
                "model_revisions": raw.get("model_revisions"),
                "runtime": raw.get("runtime"),
                "semantic_verification": raw.get("semantic_verification"),
                "acoustic_timing": acoustic_timing,
                "alignment": {
                    "model": (raw.get("models") or {}).get("alignment"),
                    "timed_word_coverage": 1.0,
                    **alignment_quality,
                },
                "diarization_status": {
                    "status": "pass",
                    "model": (raw.get("models") or {}).get("diarization"),
                    "exclusive": raw.get("diarization_exclusive") is True,
                    "turn_count": len(transcript["diarization"]),
                },
                "performance": {
                    "phase_seconds": raw.get("phase_seconds"),
                    "peak_vram_bytes": raw.get("peak_vram_bytes"),
                },
            }
        )
        if metadata["diarization_status"]["exclusive"] is not True:
            raise TranscriptionProviderError(
                "Community-1 did not expose exclusive speaker diarization"
            )
        if (
            alignment_quality["status"] == "pass"
            and acoustic_timing.get("status") == "pass"
        ):
            validate_normative_transcript(transcript)
        else:
            validate_transcript(transcript)
        return transcript


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
    "WhisperXProvider",
    "ensure_no_secret_fields",
    "load_env_value",
    "whisperx_worker_config",
]
