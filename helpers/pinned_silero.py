"""Pinned Silero VAD adapter for WhisperX's no-diarization profile."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Mapping


SILERO_REPOSITORY = "snakers4/silero-vad"
SILERO_REVISION = "b163605b3f44c3aadf28f97b125a2f7c461e9a7f"


def load_pinned_silero(cache_root: Path | str, *, threshold: float = 0.5):
    """Return a WhisperX-compatible VAD loaded from an immutable Git revision."""

    import torch
    # WhisperX imports Pyannote types even though the no-diarization profile
    # never decodes through Pyannote/TorchCodec. Suppress only that known,
    # irrelevant in-memory-decoder warning so setup does not look unhealthy.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            module=r"pyannote\.audio\.core\.io",
        )
        from whisperx.diarize import Segment as SegmentX
        from whisperx.vads.vad import Vad

    hub_dir = Path(cache_root).expanduser().resolve() / "torch-hub"
    hub_dir.mkdir(parents=True, exist_ok=True)
    torch.hub.set_dir(str(hub_dir))
    model, vad_utils = torch.hub.load(
        repo_or_dir=f"{SILERO_REPOSITORY}:{SILERO_REVISION}",
        model="silero_vad",
        force_reload=False,
        trust_repo=True,
    )
    get_speech_timestamps = vad_utils[0]

    class PinnedSilero(Vad):
        def __init__(self) -> None:
            super().__init__(threshold)

        def __call__(self, audio: Mapping[str, Any], **_kwargs: Any):
            sample_rate = int(audio["sample_rate"])
            if sample_rate != 16000:
                raise ValueError("Pinned Silero VAD requires 16 kHz audio")
            timestamps = get_speech_timestamps(
                audio["waveform"],
                model=model,
                sampling_rate=sample_rate,
                max_speech_duration_s=30,
                threshold=threshold,
            )
            return [
                SegmentX(item["start"] / sample_rate, item["end"] / sample_rate, "UNKNOWN")
                for item in timestamps
            ]

        @staticmethod
        def preprocess_audio(audio: Mapping[str, Any]):
            return audio

        @staticmethod
        def merge_chunks(segments_list, chunk_size, onset=0.5, offset=None):
            return Vad.merge_chunks(segments_list, chunk_size, onset, offset)

    return PinnedSilero()


__all__ = ["SILERO_REPOSITORY", "SILERO_REVISION", "load_pinned_silero"]
