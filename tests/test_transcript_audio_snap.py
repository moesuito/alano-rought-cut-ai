from __future__ import annotations

import numpy as np
import pytest

from helpers.transcript_audio_snap import (
    ActivityComponent,
    WordSnapConfig,
    build_bilateral_components,
    refine_word_timestamps,
)


def component(index: int, start: float, end: float) -> ActivityComponent:
    return ActivityComponent(index, start, end, start, end)


def speech_turn(start: float = 0.0, end: float = 20.0) -> list[dict]:
    return [{"start": start, "end": end, "speaker": "speaker_0"}]


def test_bilateral_components_require_both_detectors_and_keep_raw_tail():
    raw = np.zeros(80, dtype=bool)
    rnn = np.zeros(80, dtype=bool)
    raw[10:31] = True
    rnn[12:29] = True
    raw[50:65] = True  # raw-only breath/noise

    components = build_bilateral_components(raw, rnn)

    assert components == [ActivityComponent(0, 0.05, 0.155, 0.06, 0.145)]


def test_ctc_blank_dwell_is_trimmed_and_real_tail_is_extended():
    words = [
        {"word": "Salvar", "start": 1.1, "end": 3.0},
        {"word": "Depois", "start": 3.5, "end": 3.8},
    ]
    components = [component(0, 1.0, 1.5), component(1, 3.45, 3.9)]

    report = refine_word_timestamps(
        words, components, diarization=speech_turn(), config=WordSnapConfig()
    )

    assert report["status"] == "pass"
    assert words[0]["start"] == 1.0
    assert words[0]["end"] == 1.5
    assert words[0]["forced_alignment_end"] == 3.0
    assert words[0]["timing_source"] == "forced_alignment_acoustic"


def test_short_forced_tail_extends_to_bilateral_component_end_before_silence():
    words = [
        {"word": "salvar", "start": 1.1, "end": 1.4},
        {"word": "novo", "start": 2.0, "end": 2.3},
    ]
    components = [component(0, 1.0, 1.5), component(1, 1.95, 2.4)]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "pass"
    assert words[0]["end"] == 1.5


def test_recovered_cue_and_following_word_shift_to_unassigned_components_in_order():
    words = [
        {"word": "salve", "start": 1.05, "end": 1.45},
        {"word": "Corta", "start": 3.05, "end": 3.35},
        {"word": "manter", "start": 3.60, "end": 4.10},
        {"word": "o", "start": 4.15, "end": 4.30},
    ]
    components = [
        component(0, 1.0, 1.5),
        component(1, 2.0, 2.5),
        component(2, 3.0, 3.5),
        component(3, 3.55, 5.0),
    ]
    recovery = [{"cue": "corta", "verifier_start": 2.3, "verifier_end": 2.7}]

    report = refine_word_timestamps(
        words,
        components,
        diarization=speech_turn(),
        semantic_recoveries=recovery,
    )

    assert report["status"] == "pass"
    assert (words[1]["start"], words[1]["end"]) == (2.0, 2.5)
    assert (words[2]["start"], words[2]["end"]) == (3.0, 3.5)
    assert [item["reason"] for item in report["semantic_recovery_evidence"]] == [
        "semantic_cue_component_shift",
        "semantic_sequence_component_shift",
    ]


def test_recovered_cue_can_shift_forward_to_later_component():
    words = [
        {"word": "mais", "start": 1.0, "end": 1.5},
        {"word": "Corta", "start": 1.45, "end": 3.6},
        {"word": "finalização", "start": 4.1, "end": 4.5},
    ]
    components = [
        component(0, 0.95, 1.55),
        component(1, 3.5, 4.0),
        component(2, 4.05, 4.55),
    ]
    recovery = [{"cue": "corta", "verifier_start": 3.2, "verifier_end": 3.8}]

    report = refine_word_timestamps(
        words,
        components,
        diarization=speech_turn(),
        semantic_recoveries=recovery,
    )

    assert report["status"] == "pass"
    assert (words[1]["start"], words[1]["end"]) == (3.5, 4.0)
    assert report["semantic_recovery_evidence"][0]["from_component"] == 0
    assert report["semantic_recovery_evidence"][0]["to_component"] == 1


def test_recovered_cue_prefers_component_overlapping_verifier_over_nearer_midpoint():
    """A long speech run containing the hint must beat an earlier orphan."""
    words = [
        {"word": "salve", "start": 2.35, "end": 3.77},
        {"word": "Corta", "start": 4.827, "end": 5.247},
        {"word": "Manter", "start": 5.427, "end": 5.687},
        {"word": "endereco", "start": 5.807, "end": 6.287},
    ]
    components = [
        component(0, 1.59, 2.90),
        component(1, 3.41, 4.14),
        component(2, 4.83, 9.375),
    ]
    recovery = [{
        "cue": "corta",
        "verifier_start": 4.64,
        "verifier_end": 5.24,
    }]

    report = refine_word_timestamps(
        words,
        components,
        diarization=speech_turn(),
        semantic_recoveries=recovery,
    )

    assert words[1]["start"] >= 4.8
    assert not any(
        item["reason"] == "semantic_cue_component_shift"
        for item in report["semantic_recovery_evidence"]
    )
    assert any(
        item["type"] == "unattributed_bilateral_activity"
        and item["component"]["index"] == 1
        for item in report["blocking_outliers"]
    )


def test_repeated_semantic_recoveries_bind_to_distinct_cue_words():
    words = [
        {"word": "Corta", "start": 1.0, "end": 1.4},
        {"word": "fala", "start": 2.0, "end": 2.4},
        {"word": "Corta", "start": 5.0, "end": 5.4},
    ]
    components = [
        component(0, 0.95, 1.45),
        component(1, 1.95, 2.45),
        component(2, 4.95, 5.45),
    ]
    recoveries = [
        {"cue": "corta", "verifier_start": 1.0, "verifier_end": 1.4},
        {"cue": "corta", "verifier_start": 5.0, "verifier_end": 5.4},
    ]

    report = refine_word_timestamps(
        words,
        components,
        diarization=speech_turn(),
        semantic_recoveries=recoveries,
    )

    assert report["status"] == "pass"
    assert words[0]["start"] < words[2]["start"]


def test_word_envelope_keeps_multiple_unclaimed_components_before_next_word():
    words = [
        {"word": "pouquinho", "start": 1.0, "end": 3.7},
        {"word": "aí", "start": 4.1, "end": 4.4},
    ]
    components = [
        component(0, 1.0, 2.0),
        component(1, 2.1, 3.0),
        component(2, 3.1, 3.4),
        component(3, 4.0, 4.5),
    ]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "pass"
    assert (words[0]["start"], words[0]["end"]) == (1.0, 3.4)
    evidence = next(item for item in report["evidence"] if item["word_index"] == 0)
    assert evidence["component_indices"] == [0, 1, 2]


def test_word_envelope_does_not_rewind_last_word_sharing_anchor_component():
    words = [
        {"word": "conta", "start": 1.0, "end": 1.2},
        {"word": "cadastro", "start": 1.3, "end": 2.5},
        {"word": "como", "start": 3.0, "end": 3.3},
    ]
    components = [
        component(0, 0.9, 1.8),
        component(1, 1.9, 2.6),
        component(2, 2.95, 3.35),
    ]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "pass"
    assert words[1]["start"] == 1.3
    assert words[1]["end"] == 2.6
    assert words[0]["start"] <= words[1]["start"] <= words[2]["start"]
    evidence = next(item for item in report["evidence"] if item["word_index"] == 1)
    assert evidence["component_indices"] == [0, 1]


def test_word_envelope_does_not_absorb_speech_across_robust_gap():
    words = [
        {"word": "salve", "start": 1.0, "end": 3.0},
        {"word": "manter", "start": 4.0, "end": 4.4},
    ]
    components = [
        component(0, 0.95, 1.5),
        component(1, 2.2, 2.7),
        component(2, 3.95, 4.45),
    ]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "review"
    assert words[0]["end"] == 1.5
    assert any(
        item["type"] == "unattributed_bilateral_activity"
        and item["component"]["index"] == 1
        for item in report["blocking_outliers"]
    )


def test_unattributed_bilateral_speech_is_blocking_but_raw_only_is_absent():
    words = [
        {"word": "um", "start": 1.0, "end": 1.4},
        {"word": "dois", "start": 3.0, "end": 3.4},
    ]
    components = [
        component(0, 0.95, 1.45),
        component(1, 2.0, 2.3),
        component(2, 2.95, 3.45),
    ]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "review"
    assert report["blocking_outlier_count"] == 1
    assert report["blocking_outliers"][0]["type"] == "unattributed_bilateral_activity"


def test_short_interword_residual_inside_one_speaker_turn_is_nonblocking():
    words = [
        {"word": "cadastro", "start": 1.0, "end": 1.4},
        {"word": "entre", "start": 1.9, "end": 2.3},
    ]
    components = [
        component(0, 0.95, 1.45),
        component(1, 1.55, 1.65),
        component(2, 1.85, 2.35),
    ]

    report = refine_word_timestamps(
        words,
        components,
        diarization=speech_turn(0.8, 2.5),
    )

    assert report["status"] == "pass"
    assert report["blocking_outlier_count"] == 0
    assert report["nonblocking_outlier_count"] == 1
    residual = report["nonblocking_outliers"][0]
    assert residual["type"] == "nonblocking_interword_residual"
    assert residual["component"]["index"] == 1
    assert residual["previous_word"]["text"] == "cadastro"
    assert residual["following_word"]["text"] == "entre"


def test_short_residual_across_speaker_turn_boundary_remains_blocking():
    words = [
        {"word": "um", "start": 1.0, "end": 1.4},
        {"word": "dois", "start": 1.9, "end": 2.3},
    ]
    components = [
        component(0, 0.95, 1.45),
        component(1, 1.55, 1.65),
        component(2, 1.85, 2.35),
    ]
    turns = [
        {"start": 0.8, "end": 1.7, "speaker": "speaker_0"},
        {"start": 1.8, "end": 2.5, "speaker": "speaker_0"},
    ]

    report = refine_word_timestamps(words, components, diarization=turns)

    assert report["status"] == "review"
    assert report["blocking_outlier_count"] == 1
    assert report["blocking_outliers"][0]["component"]["index"] == 1


def test_component_without_speaker_support_is_not_reported_as_orphan():
    words = [{"word": "fala", "start": 1.0, "end": 1.4}]
    components = [component(0, 0.95, 1.45), component(1, 5.0, 5.5)]

    report = refine_word_timestamps(
        words, components, diarization=speech_turn(0.8, 1.6)
    )

    assert report["status"] == "pass"


def test_component_selection_uses_interval_gap_not_only_word_start():
    words = [
        {"word": "três", "start": 0.0, "end": 0.242},
        {"word": "dois", "start": 0.30, "end": 0.42},
    ]
    components = [component(0, 0.27, 0.60)]

    report = refine_word_timestamps(words, components, diarization=speech_turn())

    assert report["status"] == "pass"
    assert not any(
        item["type"] == "word_without_bilateral_anchor"
        for item in report["blocking_outliers"]
    )
