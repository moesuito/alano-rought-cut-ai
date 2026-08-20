"""Synthetic regression tests for Task F1.8: Acoustic out-point snapping and ASR over-estimation correction."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import numpy as np
import pytest

from helpers.timing import time_to_frame, parse_fps_fraction
from helpers.refine_edl_boundaries import get_scored_refined_bound


def test_get_scored_refined_bound_handles_asr_overestimation():
    """Verify that a VAD component ending at 1.30s within an inflated ASR word (1.00s-2.20s)

    returns the true acoustic offset 1.30s instead of clamping to 2.20s.
    """
    # 5ms resolution activity signal for 3.0 seconds (600 bins)
    # Speech active from index 200 (1.00s) to index 260 (1.30s)
    activity = np.zeros(600, dtype=bool)
    activity[200:260] = True  # 1.00s to 1.30s

    w_start = 1.00
    w_end = 2.20  # Inflated ASR end (900ms of trailing silence)

    comp_start, comp_end, has_bound, candidates = get_scored_refined_bound(
        activity, w_start, w_end, prev_end=None, next_start=None
    )

    assert has_bound is True
    assert comp_start == pytest.approx(1.00, abs=0.01)
    assert comp_end == pytest.approx(1.30, abs=0.01)  # Acoustic offset matches true voice end!


def test_get_scored_refined_bound_handles_asr_underestimation():
    """Verify that speech continuing past ASR end (1.00s-1.30s ASR, actual speech to 1.50s)

    is captured up to 1.50s.
    """
    activity = np.zeros(600, dtype=bool)
    activity[200:300] = True  # 1.00s to 1.50s

    w_start = 1.00
    w_end = 1.30  # Truncated ASR end

    comp_start, comp_end, has_bound, candidates = get_scored_refined_bound(
        activity, w_start, w_end, prev_end=None, next_start=None
    )

    assert has_bound is True
    assert comp_start == pytest.approx(1.00, abs=0.01)
    assert comp_end == pytest.approx(1.50, abs=0.01)
