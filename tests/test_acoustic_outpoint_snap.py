"""Synthetic regression tests for Task F1.8: Acoustic boundary snapping and attack preservation."""

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


def test_rnnoise_pre_speech_breath_isolation():
    """Verify that when raw audio contains pre-speech breath (0.80s-1.00s) but RNNoise isolates

    the voiced attack (1.00s-1.40s) before an ASR word (1.05s-1.40s), the attack is captured at 1.00s.
    """
    # Raw includes breath from 0.80s
    raw_act = np.zeros(600, dtype=bool)
    raw_act[160:280] = True  # 0.80s to 1.40s

    # RNN isolates clean speech from 1.00s
    rnn_act = np.zeros(600, dtype=bool)
    rnn_act[200:280] = True  # 1.00s to 1.40s

    w_start = 1.05
    w_end = 1.40

    _, raw_end, _, _ = get_scored_refined_bound(raw_act, w_start, w_end, prev_end=0.5, next_start=None)
    rnn_start, rnn_end, has_rnn, _ = get_scored_refined_bound(rnn_act, w_start, w_end, prev_end=0.5, next_start=None)

    assert has_rnn is True
    assert rnn_start == pytest.approx(1.00, abs=0.01)  # Captures true attack 50ms before ASR
    assert rnn_end == pytest.approx(1.40, abs=0.01)
