"""Rational timing and frame conversion utilities for video/audio frames."""

from __future__ import annotations

from fractions import Fraction
import math
from typing import Any


_FRAME_SNAP_ULPS = 4
_NTSC_FLOAT_EPSILON = 0.0005


def _time_fraction(value: float | Fraction) -> Fraction:
    """Convert a timestamp without retaining binary-float noise."""
    if isinstance(value, Fraction):
        return value
    if not math.isfinite(value):
        raise ValueError(f"Timestamp must be finite: {value}")
    return Fraction(str(value))


def parse_fps_fraction(fps: Any) -> Fraction:
    """Parse FPS as a Fraction. Handles int, float, string, and Fraction inputs.

    Correctly maps float approximations to NTSC standard fractions.
    """
    if isinstance(fps, Fraction):
        return fps
    if isinstance(fps, int):
        return Fraction(fps, 1)
    if isinstance(fps, float):
        # Match standard NTSC frame rates
        if abs(fps - 23.976) < _NTSC_FLOAT_EPSILON:
            return Fraction(24000, 1001)
        if abs(fps - 29.97) < _NTSC_FLOAT_EPSILON:
            return Fraction(30000, 1001)
        if abs(fps - 59.94) < _NTSC_FLOAT_EPSILON:
            return Fraction(60000, 1001)
        return Fraction(fps).limit_denominator(10000)
    if isinstance(fps, str):
        fps = fps.strip()
        if "/" in fps:
            try:
                return Fraction(fps)
            except ValueError:
                pass
        try:
            return parse_fps_fraction(float(fps))
        except ValueError:
            raise ValueError(f"Invalid frame rate string: {fps}")

    raise TypeError(f"Invalid frame rate type: {type(fps)}")


def format_fps_fraction(fps: Fraction) -> str:
    """Serialize an FPS authority without losing its rational identity."""
    value = parse_fps_fraction(fps)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def time_to_frame(t: float | Fraction, fps: Fraction, mode: str = "round") -> int:
    """Convert a time in seconds to a frame index using rational math.

    Supported modes: 'floor', 'ceil', 'round'.
    """
    # JSON timestamps arrive as floats. Fraction(float) preserves their
    # binary approximation, making ceil(0.4 * 30) incorrectly return 13.
    val = _time_fraction(t) * fps
    frame_value = float(val)
    nearest_frame = int(round(frame_value))
    # A time constructed from an exact frame can return a few floating-point
    # ULPs to either side of that integer (notably at NTSC rates). Snap only
    # within that representation error; real sub-frame decimal offsets remain.
    if abs(frame_value - nearest_frame) <= math.ulp(frame_value) * _FRAME_SNAP_ULPS:
        val = Fraction(nearest_frame, 1)
    if mode == "floor":
        return int(val.__floor__())
    elif mode == "ceil":
        return int(val.__ceil__())
    elif mode == "round":
        # Round half to even (Python default round)
        return int(round(float(val)))
    else:
        raise ValueError(f"Unknown rounding mode: {mode}")


def frame_to_time(frame: int, fps: Fraction) -> float:
    """Convert a frame index to seconds using rational math, returning a float."""
    return round(float(Fraction(frame) / fps), 6)


def frame_to_sample(frame: int, fps: Fraction, sample_rate: int = 48000) -> int:
    """Convert a frame boundary to its nearest deterministic PCM sample."""
    return int(round(Fraction(frame) * sample_rate / fps))
