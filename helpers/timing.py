"""Rational timing and frame conversion utilities for video/audio frames."""

from __future__ import annotations

from fractions import Fraction
from typing import Any


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
        if abs(fps - 23.976) < 0.01:
            return Fraction(24000, 1001)
        if abs(fps - 29.97) < 0.01:
            return Fraction(30000, 1001)
        if abs(fps - 59.94) < 0.01:
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


def time_to_frame(t: float | Fraction, fps: Fraction, mode: str = "round") -> int:
    """Convert a time in seconds to a frame index using rational math.

    Supported modes: 'floor', 'ceil', 'round'.
    """
    val = Fraction(t) * fps
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
