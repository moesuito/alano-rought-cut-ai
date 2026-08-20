"""DirectML / ONNX Runtime speaker diarization module for Alano Cut.

Provides hardware-accelerated speaker segmentation, embedding extraction,
and clustering on AMD Radeon, Intel Arc, and non-CUDA GPUs using DirectML,
with automatic CPU multithreaded fallback.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    hf_hub_download = None

try:
    from sklearn.cluster import AgglomerativeClustering
except ImportError:
    AgglomerativeClustering = None


DEFAULT_SEGMENTATION_REPO = "onnx-community/pyannote-segmentation-3.0"
DEFAULT_SEGMENTATION_FILE = "onnx/model.onnx"
DEFAULT_EMBEDDING_REPO = "deepghs/pyannote-embedding-onnx"
DEFAULT_EMBEDDING_FILE = "model.onnx"


class DiarizationError(RuntimeError):
    """Raised when ONNX diarization fails or models are missing."""


def get_models_cache_dir() -> Path:
    """Return the shared directory for Pyannote ONNX models."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata) / "AlanoCut" / "models" / "pyannote-onnx"
    else:
        base = Path.home() / ".cache" / "alanocut" / "models" / "pyannote-onnx"
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_diarization_models() -> tuple[Path, Path]:
    """Download and return paths to segmentation and embedding ONNX models."""
    if hf_hub_download is None:
        raise DiarizationError("huggingface_hub is required to download ONNX diarization models")

    cache_dir = get_models_cache_dir()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")

    seg_path = cache_dir / "segmentation-3.0.onnx"
    if not seg_path.exists():
        downloaded = hf_hub_download(
            repo_id=DEFAULT_SEGMENTATION_REPO,
            filename=DEFAULT_SEGMENTATION_FILE,
            token=token,
        )
        # Copy or symlink to clean cache location
        import shutil
        shutil.copyfile(downloaded, seg_path)

    emb_path = cache_dir / "embedding-512.onnx"
    if not emb_path.exists():
        downloaded = hf_hub_download(
            repo_id=DEFAULT_EMBEDDING_REPO,
            filename=DEFAULT_EMBEDDING_FILE,
            token=token,
        )
        import shutil
        shutil.copyfile(downloaded, emb_path)

    return seg_path, emb_path


def get_available_execution_providers() -> list[str]:
    """Return configured providers in order of priority (DirectML then CPU)."""
    if ort is None:
        return []
    available = ort.get_available_providers()
    providers = []
    if "DmlExecutionProvider" in available:
        providers.append("DmlExecutionProvider")
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    return providers or available


def extract_audio_16k_mono(media_path: Path | str) -> tuple[np.ndarray, int]:
    """Extract 16kHz mono float32 PCM samples from video/audio file."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(media_path),
        "-ar", "16000", "-ac", "1",
        "-f", "f32le", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    return samples, 16000


class DirectMLDiarizer:
    """Speaker diarizer utilizing Pyannote ONNX models via DirectML GPU acceleration."""

    def __init__(self, *, device: str = "directml"):
        if ort is None:
            raise DiarizationError("onnxruntime-directml is not installed")
        if AgglomerativeClustering is None:
            raise DiarizationError("scikit-learn is not installed")

        self.seg_path, self.emb_path = ensure_diarization_models()
        self.providers = (
            get_available_execution_providers()
            if device != "cpu"
            else ["CPUExecutionProvider"]
        )

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.seg_sess = ort.InferenceSession(
            str(self.seg_path),
            sess_options=sess_options,
            providers=self.providers,
        )
        self.emb_sess = ort.InferenceSession(
            str(self.emb_path),
            sess_options=sess_options,
            providers=self.providers,
        )
        self.active_providers = self.seg_sess.get_providers()

    def diarize_samples(
        self,
        samples: np.ndarray,
        sr: int = 16000,
        *,
        min_speech_duration: float = 0.4,
        distance_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Perform speaker diarization on 16kHz mono audio float32 samples."""
        duration = len(samples) / sr
        if duration < min_speech_duration:
            return []

        window_samples = 10 * sr
        step_samples = int(2.5 * sr)
        segments: list[dict[str, float]] = []

        # 1. Sliding window segmentation
        for start_idx in range(0, len(samples), step_samples):
            chunk = samples[start_idx : start_idx + window_samples]
            if len(chunk) < window_samples:
                chunk = np.pad(chunk, (0, window_samples - len(chunk)))

            chunk_input = chunk.reshape(1, 1, -1)
            seg_out = self.seg_sess.run(None, {"input_values": chunk_input})[0][0]

            frame_dur = 10.0 / seg_out.shape[0]
            window_start_sec = start_idx / sr

            # Model outputs log-probabilities
            probs = np.exp(seg_out)

            for spk_idx in range(probs.shape[1]):
                spk_probs = probs[:, spk_idx]
                active = spk_probs > 0.5
                if not np.any(active):
                    continue

                in_seg = False
                seg_start = 0
                for f_idx, is_act in enumerate(active):
                    if is_act and not in_seg:
                        in_seg = True
                        seg_start = f_idx
                    elif not is_act and in_seg:
                        in_seg = False
                        t_start = window_start_sec + seg_start * frame_dur
                        t_end = window_start_sec + f_idx * frame_dur
                        if t_end - t_start >= min_speech_duration:
                            segments.append({
                                "start": min(t_start, duration),
                                "end": min(t_end, duration),
                            })
                if in_seg:
                    t_start = window_start_sec + seg_start * frame_dur
                    t_end = window_start_sec + len(active) * frame_dur
                    if t_end - t_start >= min_speech_duration:
                        segments.append({
                            "start": min(t_start, duration),
                            "end": min(t_end, duration),
                        })

        if not segments:
            # Fallback: treat non-silent duration as single speaker segment
            return [{
                "start": 0.0,
                "end": round(duration, 3),
                "speaker_id": "speaker_0",
            }]

        # 2. Merge overlapping intervals
        segments.sort(key=lambda x: x["start"])
        merged: list[dict[str, float]] = []
        for s in segments:
            if not merged:
                merged.append(s)
            elif s["start"] <= merged[-1]["end"] + 0.15:
                merged[-1]["end"] = max(merged[-1]["end"], s["end"])
            else:
                merged.append(s)

        # 3. Extract speaker embeddings
        embeddings: list[np.ndarray] = []
        valid_segments: list[dict[str, float]] = []
        for s in merged:
            s_start = int(s["start"] * sr)
            s_end = int(s["end"] * sr)
            seg_audio = samples[s_start:s_end]
            if len(seg_audio) < int(0.4 * sr):
                continue
            if len(seg_audio) < int(1.0 * sr):
                seg_audio = np.pad(seg_audio, (0, int(1.0 * sr) - len(seg_audio)))

            emb = self.emb_sess.run(None, {"waveform": seg_audio.reshape(1, -1)})[0][0]
            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = emb / norm
            embeddings.append(emb)
            valid_segments.append(s)

        if not embeddings:
            return [{
                "start": 0.0,
                "end": round(duration, 3),
                "speaker_id": "speaker_0",
            }]

        if len(embeddings) == 1:
            return [{
                "start": round(valid_segments[0]["start"], 3),
                "end": round(valid_segments[0]["end"], 3),
                "speaker_id": "speaker_0",
            }]

        # 4. Cluster embeddings
        emb_matrix = np.array(embeddings)
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=distance_threshold,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(emb_matrix)

        # Map cluster labels deterministically in order of first appearance
        label_map: dict[int, str] = {}
        results: list[dict[str, Any]] = []
        for seg, raw_label in zip(valid_segments, labels):
            if raw_label not in label_map:
                label_map[raw_label] = f"speaker_{len(label_map)}"
            canonical_speaker = label_map[raw_label]
            results.append({
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "speaker_id": canonical_speaker,
            })

        return results

    def diarize_file(
        self,
        audio_path: Path | str,
        *,
        min_speech_duration: float = 0.4,
        distance_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Extract audio and return list of speaker turns with start/end/speaker_id."""
        samples, sr = extract_audio_16k_mono(Path(audio_path))
        return self.diarize_samples(
            samples,
            sr=sr,
            min_speech_duration=min_speech_duration,
            distance_threshold=distance_threshold,
        )


def diarize_audio_file(
    audio_path: Path | str,
    *,
    device: str = "directml",
    min_speech_duration: float = 0.4,
    distance_threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """Convenience helper to diarize an audio file."""
    diarizer = DirectMLDiarizer(device=device)
    return diarizer.diarize_file(
        audio_path,
        min_speech_duration=min_speech_duration,
        distance_threshold=distance_threshold,
    )


def doctor() -> dict[str, Any]:
    """Perform health and readiness checks for DirectML ONNX diarization."""
    checks: dict[str, Any] = {
        "onnxruntime_installed": ort is not None,
        "huggingface_hub_installed": hf_hub_download is not None,
        "scikit_learn_installed": AgglomerativeClustering is not None,
        "directml_available": False,
        "models_cached": False,
    }

    if ort is not None:
        providers = ort.get_available_providers()
        checks["available_providers"] = providers
        checks["directml_available"] = "DmlExecutionProvider" in providers

    cache_dir = get_models_cache_dir()
    seg_path = cache_dir / "segmentation-3.0.onnx"
    emb_path = cache_dir / "embedding-512.onnx"
    checks["models_cached"] = seg_path.exists() and emb_path.exists()
    checks["segmentation_model_exists"] = seg_path.exists()
    checks["embedding_model_exists"] = emb_path.exists()

    status = (
        "pass"
        if checks["onnxruntime_installed"]
        and checks["scikit_learn_installed"]
        and checks["directml_available"]
        else "unhealthy"
    )

    return {
        "status": status,
        "runtime": "directml_onnx",
        "checks": checks,
    }
