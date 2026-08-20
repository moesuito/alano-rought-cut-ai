"""Audio analysis, caching, and VAD logic for boundary refinement.

Provides FFmpeg-based PCM extraction, VFR checking, adaptive noise floor
hysteresis VAD, and raw/RNNoise signal alignment.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import sys
import numpy as np

_DF_MODEL = None
_DF_STATE = None


def get_current_denoiser_id() -> str:
    """Return identifier for the active neural denoiser."""
    if is_deepfilternet_available():
        return "DeepFilterNet3-max"
    return EXPECTED_MODEL_HASH


def is_deepfilternet_available() -> bool:
    """Check if DeepFilterNet is installed and functional in the environment."""
    try:
        import torch
        from df.enhance import enhance, init_df
        return True
    except Exception:
        return False


def denoise_deepfilternet(mono_samples: np.ndarray, atten_lim_db: float = 100.0) -> np.ndarray | None:
    """Denoise 48kHz int16 mono audio using DeepFilterNet 3 with maximum noise attenuation."""
    global _DF_MODEL, _DF_STATE
    try:
        import torch
        from df.enhance import enhance, init_df
    except Exception:
        return None

    try:
        if _DF_MODEL is None or _DF_STATE is None:
            _DF_MODEL, _DF_STATE, _ = init_df()

        audio_float = mono_samples.astype(np.float32) / 32768.0
        audio_tensor = torch.from_numpy(audio_float).unsqueeze(0)
        enhanced_tensor = enhance(_DF_MODEL, _DF_STATE, audio_tensor, atten_lim_db=atten_lim_db)
        enhanced_np = enhanced_tensor.squeeze(0).clamp(-1.0, 1.0).numpy()
        return (enhanced_np * 32767.0).astype(np.int16)
    except Exception as exc:
        print(f"DeepFilterNet enhancement error: {exc}", file=sys.stderr)
        return None

# Standard parameters for voice activity detection
DEFAULT_VAD_PARAMS = {
    "sample_rate": 48000,
    "window_ms": 10,
    "hop_ms": 5,
    "threshold_high_db": 12.0,
    "threshold_low_db": 3.0,
    "noise_floor_window_s": 10.0,
    "gap_fill_ms": 40,
    "transient_protection_ms": 60,
    "max_lag_ms": 30,
}

EXPECTED_MODEL_HASH = "51BB92B9450988F5DE3DAC32422C3551CA61760C1EF042751037178F4DFAB3F8"


def get_ffmpeg_version() -> str:
    """Run ffmpeg -version and return the first line as identifier."""
    try:
        out = subprocess.check_output(["ffmpeg", "-version"], text=True)
        return out.splitlines()[0]
    except Exception as e:
        raise RuntimeError(f"FFmpeg is not available: {e}")


def is_ffmpeg_arnndn_available() -> bool:
    """Check if the arnndn filter is available in FFmpeg."""
    try:
        out = subprocess.check_output(["ffmpeg", "-filters"], text=True)
        return " arnndn " in out
    except Exception:
        return False


def verify_model_hash(model_path: Path) -> None:
    """Verify that the model file exists and matches the expected SHA-256 hash."""
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    h = hashlib.sha256()
    with open(model_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    file_hash = h.hexdigest().upper()
    if file_hash != EXPECTED_MODEL_HASH:
        raise ValueError(
            f"Model SHA-256 mismatch!\nExpected: {EXPECTED_MODEL_HASH}\nActual:   {file_hash}"
        )


def check_vfr(source_path: Path) -> bool:
    """Return True if the video stream is Variable Frame Rate (VFR), False otherwise."""
    # Run ffprobe to check stream properties
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_type,r_frame_rate,avg_frame_rate",
        "-of", "json", str(source_path)
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            # Audio-only streams are not video VFR
            return False

        stream = streams[0]
        r_fps = stream.get("r_frame_rate")
        avg_fps = stream.get("avg_frame_rate")

        if r_fps and avg_fps and r_fps != "0/0" and avg_fps != "0/0":
            r_frac = Fraction(r_fps)
            avg_frac = Fraction(avg_fps)
            if r_frac != avg_frac:
                return True

        # Also run vfrdet filter to perform definitive statistical duration check
        cmd_vfr = [
            "ffmpeg", "-i", str(source_path), "-vf", "vfrdet", "-an", "-f", "null", "-"
        ]
        proc = subprocess.run(cmd_vfr, capture_output=True, text=True)
        m = re.search(r"VFR:([0-9.]+)\s*\((\d+)/(\d+)\)", proc.stderr)
        if m:
            vfr_num = int(m.group(2))
            if vfr_num > 0:
                return True
    except Exception as e:
        raise RuntimeError(f"Error checking VFR on {source_path}: {e}")

    return False


def get_source_fingerprint(
    source_path: Path,
    model_hash: str,
    params: dict[str, Any],
    ffmpeg_identity: str,
    transcript_path: Path | None = None
) -> str:
    """Generate a stable SHA-256 fingerprint for caching based on source details and parameters."""
    stat = source_path.stat()
    mtime = stat.st_mtime
    size = stat.st_size

    transcript_hash = ""
    if transcript_path and transcript_path.exists():
        h = hashlib.sha256()
        with open(transcript_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        transcript_hash = h.hexdigest()

    # Bundle input metadata to make hash sensitive to modifications
    meta = {
        "path": str(source_path.resolve()),
        "size": size,
        "mtime": mtime,
        "model_hash": model_hash,
        "params": sorted(params.items()),
        "ffmpeg": ffmpeg_identity,
        "transcript_hash": transcript_hash
    }
    serialized = json.dumps(meta, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_cache_metadata(
    meta_path: Path,
    fingerprint: str,
    version: str,
    params: dict[str, Any],
    model_hash: str,
    source_path: Path
) -> bool:
    """Validate that cache metadata matches setup and file status."""
    if not meta_path.exists():
        return False
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if data.get("fingerprint") != fingerprint:
            return False
        if data.get("version") != version:
            return False
        if data.get("model") != model_hash:
            return False
        if data.get("params") != params:
            return False
        source_data = data.get("source", {})
        stat = source_path.stat()
        if source_data.get("path") != str(source_path.resolve()):
            return False
        if source_data.get("size") != stat.st_size:
            return False
        if abs(source_data.get("mtime", 0) - stat.st_mtime) > 1e-3:
            return False
        return True
    except Exception:
        return False


def extract_analysis_audio(
    source_path: Path,
    output_dir: Path,
    source_id: str,
    fingerprint: str,
    model_path: Path,
    transcript_path: Path
) -> tuple[Path, Path]:
    """Extract raw and RNNoise mono 48kHz audio and cache them using channel energy selection rules."""
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = output_dir / f"{source_id}_{fingerprint}_raw.pcm"
    rnn_path = output_dir / f"{source_id}_{fingerprint}_rnn.pcm"
    meta_path = output_dir / f"{source_id}_{fingerprint}_meta.json"

    # Cache hit
    if raw_path.exists() and rnn_path.exists() and meta_path.exists():
        if validate_cache_metadata(meta_path, fingerprint, "1.1", DEFAULT_VAD_PARAMS, EXPECTED_MODEL_HASH, source_path):
            return raw_path, rnn_path

    # Check number of audio channels
    cmd_channels = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=channels",
        "-of", "json", str(source_path)
    ]
    try:
        out_c = subprocess.check_output(cmd_channels, text=True)
        data_c = json.loads(out_c)
        streams = data_c.get("streams", [])
        if not streams:
            raise RuntimeError(f"No audio stream found in {source_path}")
        C = int(streams[0].get("channels", 1))
    except Exception as e:
        raise RuntimeError(f"Failed to probe audio channels for {source_path}: {e}")

    # Extract all channels to a temporary PCM file
    temp_multi_pcm = output_dir / f"{source_id}_{fingerprint}_multi.pcm.tmp"
    cmd_extract = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(source_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "48000",
        "-f", "s16le", str(temp_multi_pcm)
    ]
    try:
        subprocess.run(cmd_extract, check=True)
    except subprocess.CalledProcessError as e:
        if temp_multi_pcm.exists():
            temp_multi_pcm.unlink()
        raise RuntimeError(f"FFmpeg multi-channel extraction failed for {source_path}: {e}")

    try:
        # Load the multi-channel samples
        raw_data = np.fromfile(temp_multi_pcm, dtype=np.int16)
        num_samples = len(raw_data) // C
        raw_multi = raw_data[:num_samples * C].reshape((num_samples, C))
    finally:
        if temp_multi_pcm.exists():
            temp_multi_pcm.unlink()

    # Calculate energy (sum of squares) for each channel to find the max-energy channel
    raw_energies = []
    for c in range(C):
        col = raw_multi[:, c].astype(np.float64)
        raw_energies.append(np.sum(col ** 2))
    raw_channel_idx = int(np.argmax(raw_energies))

    # Load transcript words for RNNoise anchor-dominant channel selection
    words = []
    if transcript_path.exists():
        try:
            data = json.loads(transcript_path.read_text(encoding="utf-8"))
            for w in data.get("words", []):
                if w.get("type") == "word" and w.get("start") is not None and w.get("end") is not None:
                    words.append(w)
        except Exception:
            pass

    # Create word mask for energy calculation
    mask = np.zeros(num_samples, dtype=bool)
    for w in words:
        start_t = float(w["start"])
        end_t = float(w["end"])
        start_idx = max(0, min(num_samples, int(start_t * 48000)))
        end_idx = max(0, min(num_samples, int(end_t * 48000)))
        mask[start_idx:end_idx] = True

    if np.any(mask):
        rnn_energies = []
        for c in range(C):
            col_masked = raw_multi[mask, c].astype(np.float64)
            rnn_energies.append(np.sum(col_masked ** 2))
        rnn_channel_idx = int(np.argmax(rnn_energies))
    else:
        rnn_channel_idx = raw_channel_idx

    # Write the raw mono channel to raw_path
    raw_mono = raw_multi[:, raw_channel_idx]
    temp_raw = raw_path.with_suffix(".raw.tmp")
    raw_mono.tofile(temp_raw)

    # Write the RNNoise mono channel to temp input
    rnn_mono = raw_multi[:, rnn_channel_idx]
    temp_rnn_in = rnn_path.with_suffix(".rnn_in.tmp")
    rnn_mono.tofile(temp_rnn_in)

    # Denoise mono channel: use DeepFilterNet 3 (attenuation limit 100 dB) if available, or RNNoise as fallback
    temp_rnn_out = rnn_path.with_suffix(".rnn_out.tmp")
    denoised_samples = denoise_deepfilternet(rnn_mono, atten_lim_db=100.0)
    if denoised_samples is not None:
        denoised_samples.tofile(temp_rnn_out)
        denoiser_used = "deepfilternet3"
    else:
        escaped_model = str(model_path.resolve()).replace("\\", "/")
        escaped_model = escaped_model.replace(":", "\\:")
        escaped_model = escaped_model.replace("'", "'\\\\''")
        cmd_rnn = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "s16le", "-ac", "1", "-ar", "48000",
            "-i", str(temp_rnn_in),
            "-af", f"arnndn=model='{escaped_model}'",
            "-f", "s16le", str(temp_rnn_out)
        ]
        subprocess.run(cmd_rnn, check=True)
        denoiser_used = "rnnoise"

    try:
        # Write metadata JSON
        meta = {
            "fingerprint": fingerprint,
            "version": "1.1",
            "model": EXPECTED_MODEL_HASH,
            "denoiser": denoiser_used,
            "atten_lim_db": 100.0 if denoiser_used == "deepfilternet3" else None,
            "params": DEFAULT_VAD_PARAMS,
            "source": {
                "path": str(source_path.resolve()),
                "size": source_path.stat().st_size,
                "mtime": source_path.stat().st_mtime
            },
            "channels": C,
            "raw_channel_idx": raw_channel_idx,
            "rnn_channel_idx": rnn_channel_idx,
            "raw_energy": raw_energies[raw_channel_idx],
            "rnn_energy": float(np.sum(raw_multi[mask, rnn_channel_idx].astype(np.float64) ** 2)) if np.any(mask) else raw_energies[raw_channel_idx]
        }
        temp_meta = meta_path.with_suffix(".meta.tmp")
        with open(temp_meta, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, indent=2))
            f.flush()
            os.fsync(f.fileno())

        # Atomically replace files (never unlink old ones first)
        os.replace(str(temp_raw), str(raw_path))
        os.replace(str(temp_rnn_out), str(rnn_path))
        os.replace(str(temp_meta), str(meta_path))
    except Exception as e:
        for p in [temp_raw, temp_rnn_in, temp_rnn_out]:
            if p.exists():
                p.unlink()
        raise e
    finally:
        if temp_rnn_in.exists():
            temp_rnn_in.unlink()

    return raw_path, rnn_path


def load_pcm_data(filepath: Path) -> np.ndarray:
    """Read a headerless PCM 16-bit signed integer file into a numpy array."""
    return np.fromfile(filepath, dtype=np.int16)


def compute_rms_db(samples: np.ndarray, window_size: int, hop_size: int) -> np.ndarray:
    """Calculate RMS dBFS of signal using specified window and hop sizes."""
    n = len(samples)
    if n < window_size:
        return np.array([-120.0])

    num_frames = (n - window_size) // hop_size + 1
    rms_db = np.zeros(num_frames, dtype=np.float64)

    # Scale int16 samples to range [-1.0, 1.0]
    samples_float = samples.astype(np.float64) / 32768.0

    for i in range(num_frames):
        start = i * hop_size
        end = start + window_size
        chunk = samples_float[start:end]

        rms = np.sqrt(np.mean(np.square(chunk)))
        if rms <= 1e-9:
            rms_db[i] = -120.0
        else:
            rms_db[i] = 20.0 * np.log10(rms)

    return rms_db


def compute_noise_floor_db(
    rms_db: np.ndarray,
    hop_size_ms: int,
    window_s: float = 10.0,
    word_mask: np.ndarray | None = None
) -> np.ndarray:
    """Estimate local noise floor using the 10th percentile in a ±5s sliding window, excluding words."""
    n = len(rms_db)
    noise_floor = np.zeros(n, dtype=np.float64)

    # 5 seconds in frames (e.g. 5.0s / 0.005s = 1000 frames)
    frames_half = int((window_s / 2.0) * (1000.0 / hop_size_ms))

    for i in range(n):
        start = max(0, i - frames_half)
        end = min(n, i + frames_half + 1)

        chunk_rms = rms_db[start:end]
        if word_mask is not None:
            chunk_mask = word_mask[start:end]
            non_word_rms = chunk_rms[~chunk_mask]
            if len(non_word_rms) > 0:
                noise_floor[i] = np.percentile(non_word_rms, 10)
                continue

        noise_floor[i] = np.percentile(chunk_rms, 10)

    return noise_floor


def run_vad_hysteresis(
    rms_db: np.ndarray,
    noise_floor_db: np.ndarray,
    threshold_high_db: float,
    threshold_low_db: float,
    gap_fill_frames: int,
    transient_protection_frames: int,
    min_speech_db: float = -55.0
) -> np.ndarray:
    """Run VAD using adaptive noise floor thresholds, gap filling, and transient protection."""
    n = len(rms_db)
    activity = np.zeros(n, dtype=bool)

    is_active = False
    for i in range(n):
        high_t = max(noise_floor_db[i] + threshold_high_db, min_speech_db)
        low_t = max(noise_floor_db[i] + threshold_low_db, min_speech_db - 8.0)

        if is_active:
            if rms_db[i] < low_t:
                is_active = False
        else:
            if rms_db[i] > high_t:
                is_active = True

        activity[i] = is_active

    # Apply gap fill (40ms -> 8 frames by default)
    activity = activity.copy()
    i = 0
    while i < n:
        if not activity[i]:
            start = i
            while i < n and not activity[i]:
                i += 1
            end = i
            # Fill only internal gaps
            if end - start <= gap_fill_frames and start > 0 and end < n:
                activity[start:end] = True
        else:
            i += 1

    # Apply transient protection (60ms -> 12 frames by default) with speech-following protection
    runs = []
    i = 0
    while i < n:
        if activity[i]:
            start = i
            while i < n and activity[i]:
                i += 1
            end = i
            runs.append({"start": start, "end": end, "is_speech": (end - start >= transient_protection_frames)})
        else:
            i += 1

    keep_run = [False] * len(runs)
    for idx, run in enumerate(runs):
        if run["is_speech"]:
            keep_run[idx] = True
        else:
            # It's a short transient. Check if it is followed by speech within transient_protection_frames (60ms)
            for next_idx in range(idx + 1, len(runs)):
                if runs[next_idx]["is_speech"]:
                    gap = runs[next_idx]["start"] - run["end"]
                    if gap <= transient_protection_frames:
                        keep_run[idx] = True
                    break

    new_activity = np.zeros(n, dtype=bool)
    for idx, run in enumerate(runs):
        if keep_run[idx]:
            new_activity[run["start"]:run["end"]] = True

    return new_activity


def align_signals(rms_raw: np.ndarray, rms_rnn: np.ndarray, max_lag_frames: int) -> int:
    """Find the optimal lag between raw and RNNoise by cross-correlating normalized RMS profiles."""
    n = min(len(rms_raw), len(rms_rnn))
    if n == 0:
        return 0

    raw_norm = rms_raw[:n] - np.mean(rms_raw[:n])
    rnn_norm = rms_rnn[:n] - np.mean(rms_rnn[:n])

    best_lag = 0
    max_corr = -float("inf")

    for lag in range(-max_lag_frames, max_lag_frames + 1):
        if lag > 0:
            shifted = np.concatenate([np.zeros(lag), rnn_norm[:-lag]])
        elif lag < 0:
            shifted = np.concatenate([rnn_norm[-lag:], np.zeros(-lag)])
        else:
            shifted = rnn_norm

        corr = np.dot(raw_norm, shifted)
        if corr > max_corr:
            max_corr = corr
            best_lag = lag

    return best_lag


def build_word_mask(num_frames: int, words: list[dict[str, Any]] | None, hop_ms: int) -> np.ndarray:
    """Construct a boolean mask indicating frame ranges overlapping with transcript words."""
    mask = np.zeros(num_frames, dtype=bool)
    if not words:
        return mask
    hop_s = hop_ms / 1000.0
    win_s = 10.0 / 1000.0
    for w in words:
        w_start = float(w["start"])
        w_end = float(w["end"])
        start_idx = max(0, int((w_start - win_s) / hop_s) + 1)
        end_idx = min(num_frames, int(w_end / hop_s) + 1)
        mask[start_idx:end_idx] = True
    return mask


def get_combined_activity(
    raw_pcm_path: Path,
    rnn_pcm_path: Path,
    params: dict[str, Any],
    words: list[dict[str, Any]] | None = None
) -> tuple[np.ndarray, np.ndarray, int, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load audio PCM files, run VAD, align them, and return raw and RNNoise activities and RMS profiles."""
    raw_samples = load_pcm_data(raw_pcm_path)
    rnn_samples = load_pcm_data(rnn_pcm_path)

    sr = params["sample_rate"]
    win_samples = int(sr * params["window_ms"] / 1000)
    hop_samples = int(sr * params["hop_ms"] / 1000)

    rms_raw = compute_rms_db(raw_samples, win_samples, hop_samples)
    rms_rnn = compute_rms_db(rnn_samples, win_samples, hop_samples)

    n_min = min(len(rms_raw), len(rms_rnn))
    rms_raw = rms_raw[:n_min]
    rms_rnn = rms_rnn[:n_min]

    # Compute noise floors using word-excluded sliding window
    word_mask = build_word_mask(n_min, words, params["hop_ms"])
    nf_raw = compute_noise_floor_db(rms_raw, params["hop_ms"], params["noise_floor_window_s"], word_mask)
    nf_rnn = compute_noise_floor_db(rms_rnn, params["hop_ms"], params["noise_floor_window_s"], word_mask)

    gap_frames = int(params["gap_fill_ms"] / params["hop_ms"])
    trans_frames = int(params["transient_protection_ms"] / params["hop_ms"])
    max_lag_frames = int(params["max_lag_ms"] / params["hop_ms"])

    activity_raw = run_vad_hysteresis(
        rms_raw, nf_raw,
        params["threshold_high_db"], params["threshold_low_db"],
        gap_frames, trans_frames
    )
    activity_rnn = run_vad_hysteresis(
        rms_rnn, nf_rnn,
        params["threshold_high_db"], params["threshold_low_db"],
        gap_frames, trans_frames
    )

    lag = align_signals(rms_raw, rms_rnn, max_lag_frames)

    # Align RNNoise activity and RMS to raw timeline using the optimal lag
    activity_rnn_aligned = np.zeros(n_min, dtype=bool)
    rms_rnn_aligned = np.zeros(n_min, dtype=np.float64)
    nf_rnn_aligned = np.zeros(n_min, dtype=np.float64)
    for i in range(n_min):
        idx = i - lag
        if 0 <= idx < len(activity_rnn):
            activity_rnn_aligned[i] = activity_rnn[idx]
        if 0 <= idx < len(rms_rnn):
            rms_rnn_aligned[i] = rms_rnn[idx]
        if 0 <= idx < len(nf_rnn):
            nf_rnn_aligned[i] = nf_rnn[idx]

    return activity_raw, activity_rnn_aligned, lag, rms_raw, rms_rnn_aligned, nf_raw, nf_rnn_aligned
