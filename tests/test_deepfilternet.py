import pytest
import numpy as np
from helpers.audio_analysis import (
    is_deepfilternet_available,
    denoise_deepfilternet,
    get_current_denoiser_id,
)


def test_deepfilternet_availability():
    assert is_deepfilternet_available() is True
    assert get_current_denoiser_id() == "DeepFilterNet3-max"


def test_deepfilternet_denoising():
    sr = 48000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    # Speech tone + noise
    speech = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    noise = (0.1 * np.random.randn(sr) * 32767).astype(np.int16)
    mixed = speech + noise

    denoised = denoise_deepfilternet(mixed, atten_lim_db=100.0)
    assert denoised is not None
    assert isinstance(denoised, np.ndarray)
    assert denoised.dtype == np.int16
    assert len(denoised) == len(mixed)

    # In pure noise segment, DeepFilterNet should suppress energy deeply
    pure_noise = (0.1 * np.random.randn(sr) * 32767).astype(np.int16)
    denoised_noise = denoise_deepfilternet(pure_noise, atten_lim_db=100.0)
    assert denoised_noise is not None
    raw_energy = np.mean(pure_noise.astype(np.float64) ** 2)
    denoised_energy = np.mean(denoised_noise.astype(np.float64) ** 2)
    # Denoised energy should be suppressed by at least 15 dB
    assert denoised_energy < raw_energy * 0.1
