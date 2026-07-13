"""Lightweight tests for deterministic WhisperX worker boundaries."""

from __future__ import annotations

import warnings

import pytest

from helpers.whisperx_worker import (
    _best_speaker,
    _deduplicate_recording_cue_segments,
    _suppress_in_memory_torchcodec_warning,
    _window_start_samples,
    assign_speakers,
    build_aligned_word_hints,
    build_literal_coverage_segments,
    merge_missing_recording_cues,
    reconcile_aligned_recording_cues,
)


def _turn(start: float, end: float, speaker: str) -> dict[str, object]:
    return {"start": start, "end": end, "speaker": speaker}


def test_best_speaker_uses_largest_positive_overlap():
    assert _best_speaker(
        0.75,
        2.0,
        [
            _turn(0.0, 1.0, "speaker_0"),
            _turn(1.0, 2.5, "speaker_1"),
        ],
    ) == "speaker_1"


def test_best_speaker_returns_none_inside_diarization_gap():
    assert _best_speaker(
        1.1,
        1.4,
        [
            _turn(0.0, 1.0, "speaker_0"),
            _turn(1.5, 2.0, "speaker_1"),
        ],
    ) is None


def test_best_speaker_retains_first_exact_positive_tie():
    assert _best_speaker(
        0.5,
        1.5,
        [
            _turn(0.0, 1.0, "speaker_0"),
            _turn(1.0, 2.0, "speaker_1"),
        ],
    ) == "speaker_0"


def test_best_speaker_returns_none_without_turns():
    assert _best_speaker(0.0, 1.0, []) is None


def test_assign_speakers_fails_instead_of_inventing_nearest_speaker_in_gap():
    turns = [_turn(0.0, 1.0, "speaker_0"), _turn(2.0, 3.0, "speaker_1")]
    aligned = {
        "segments": [
            {
                "start": 0.0,
                "end": 0.5,
                "words": [{"word": "gap", "start": 1.25, "end": 1.5}],
            }
        ]
    }

    with pytest.raises(RuntimeError, match=r"no overlapping speaker turn.*word\[0\]"):
        assign_speakers(aligned, turns)

    # Validation is transactional: a later gap does not leave earlier speaker
    # assignments in the partially processed result.
    assert "speaker" not in aligned["segments"][0]


@pytest.mark.parametrize(
    "turns",
    [[], [_turn(0.0, 1.0, "speaker_0")]],
)
def test_assign_speakers_fails_when_segment_has_no_overlap(turns):
    aligned = {"segments": [{"start": 1.1, "end": 1.4, "words": []}]}

    with pytest.raises(RuntimeError, match=r"no overlapping speaker turn.*segment\[0\]"):
        assign_speakers(aligned, turns)


def test_multiline_torchcodec_warning_is_suppressed_without_hiding_others():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _suppress_in_memory_torchcodec_warning()
        warnings.warn(
            "\ntorchcodec is not installed correctly so built-in audio decoding "
            "will fail.\nThis worker does not use it.",
            UserWarning,
        )
        warnings.warn("independent warning", UserWarning)

    assert [str(item.message) for item in caught] == ["independent warning"]


def test_semantic_verifier_can_only_prepend_high_confidence_known_cue():
    primary = [
        {
            "start": 182.74,
            "end": 191.18,
            "text": " Manter o endereço atualizado é essencial.",
        }
    ]
    verifier = [
        {
            "start": 181.32,
            "end": 190.36,
            "text": "os campos e salve. Corta, manter o endereço atualizado",
            "consensus_count": 2,
            "words": [
                {"word": " Corta,", "start": 184.42, "end": 185.10, "probability": 0.82},
            ],
        }
    ]

    merged, recoveries = merge_missing_recording_cues(primary, verifier)

    assert merged[0]["text"].startswith(" Corta, Manter")
    assert merged[0]["start"] == pytest.approx(182.42)
    assert recoveries == [
        {
            "cue": "corta",
            "probability": 0.82,
            "verifier_start": 184.42,
            "verifier_end": 185.1,
            "primary_segment_index": 0,
            "placement": "before_anchor",
            "requires_component_recovery": True,
            "consensus_count": 2,
        }
    ]


def test_missing_cue_requires_two_window_consensus_votes():
    primary = [{"start": 20.0, "end": 22.0, "text": "Manter o endereço."}]
    verifier = [
        {
            "start": 19.0,
            "end": 22.0,
            "text": "Corta, manter o endereço",
            "consensus_count": 1,
            "words": [
                {"word": "Corta", "start": 19.5, "end": 20.0, "probability": 0.99}
            ],
        }
    ]

    merged, recoveries = merge_missing_recording_cues(primary, verifier)

    assert merged == primary
    assert recoveries == []


def test_windowed_cue_candidates_keep_consensus_and_best_view():
    candidates = [
        {
            "start": 180.0,
            "end": 190.0,
            "text": "Corta, manter",
            "window_start": 165.0,
            "words": [
                {"word": "Corta", "start": 184.64, "end": 185.24, "probability": 0.96}
            ],
        },
        {
            "start": 181.0,
            "end": 191.0,
            "text": "Corta, manter",
            "window_start": 178.0,
            "words": [
                {"word": "Corta", "start": 184.38, "end": 185.08, "probability": 0.70}
            ],
        },
    ]

    result = _deduplicate_recording_cue_segments(candidates)

    assert len(result) == 1
    assert result[0]["consensus_count"] == 2
    assert result[0]["window_start"] == 165.0


def test_windowed_consensus_counts_distinct_windows_not_duplicate_tokens():
    candidates = [
        {
            "start": 10.0,
            "end": 11.0,
            "text": "corta corta",
            "window_start": 0.0,
            "words": [{"word": "corta", "start": 10.0, "end": 10.2, "probability": 0.9}],
        },
        {
            "start": 10.0,
            "end": 11.0,
            "text": "corta corta",
            "window_start": 0.0,
            "words": [{"word": "corta", "start": 10.1, "end": 10.3, "probability": 0.8}],
        },
    ]

    result = _deduplicate_recording_cue_segments(candidates)

    assert result[0]["consensus_count"] == 1


def test_window_starts_add_sample_exact_tail_window():
    starts = _window_start_samples(
        207 * 16_000,
        window_seconds=30.0,
        overlap_seconds=15.0,
    )

    assert starts[0] == 0
    assert starts[-1] == 177 * 16_000
    assert all(right > left for left, right in zip(starts, starts[1:]))


def test_aligned_cue_reconciliation_inserts_consensus_at_chronological_boundary():
    aligned = [
        {
            "start": 181.0,
            "end": 185.0,
            "text": "campos e salve.",
            "words": [
                {"word": "campos", "start": 181.5, "end": 182.0},
                {"word": "salve.", "start": 182.3, "end": 182.8},
            ],
        },
        {
            "start": 185.4,
            "end": 190.0,
            "text": "Manter o endereço.",
            "words": [
                {"word": "Manter", "start": 185.4, "end": 185.7},
                {"word": "o", "start": 185.7, "end": 185.8},
                {"word": "endereço.", "start": 185.8, "end": 186.3},
            ],
        },
    ]
    verifier = [
        {
            "consensus_count": 2,
            "words": [
                {"word": "Corta", "start": 184.4, "end": 185.1, "probability": 0.96}
            ],
        }
    ]

    merged, recoveries = reconcile_aligned_recording_cues(aligned, verifier)

    assert merged[1]["text"].startswith("Corta, Manter")
    assert recoveries[0]["placement"] == "before_aligned_word"
    assert recoveries[0]["consensus_count"] == 2


def test_aligned_cue_reconciliation_matches_existing_occurrences_one_to_one():
    aligned = [
        {
            "start": 10.0,
            "end": 14.0,
            "text": "Corta. Corta.",
            "words": [
                {"word": "Corta.", "start": 10.0, "end": 10.5},
                {"word": "Corta.", "start": 13.0, "end": 13.5},
            ],
        }
    ]
    verifier = [
        {"consensus_count": 2, "words": [{"word": "Corta", "start": 10.1, "end": 10.6, "probability": 0.9}]},
        {"consensus_count": 2, "words": [{"word": "Corta", "start": 13.1, "end": 13.6, "probability": 0.9}]},
    ]

    merged, recoveries = reconcile_aligned_recording_cues(aligned, verifier)

    assert merged[0]["text"] == "Corta. Corta."
    assert recoveries == []


def test_cue_realign_padding_never_reorders_later_lexical_segment():
    aligned = [
        {
            "start": 201.3,
            "end": 203.5,
            "text": "Até mais.",
            "words": [
                {"word": "Até", "start": 201.3, "end": 201.6},
                {"word": "mais.", "start": 203.2, "end": 203.5},
            ],
        },
        {
            "start": 203.6,
            "end": 204.0,
            "text": "Corta.",
            "words": [{"word": "Corta.", "start": 203.6, "end": 204.0}],
        },
    ]
    verifier = [
        {"consensus_count": 1, "words": [{"word": "Corta", "start": 203.1, "end": 203.8, "probability": 0.9}]}
    ]

    merged, recoveries = reconcile_aligned_recording_cues(aligned, verifier)

    assert [segment["text"] for segment in merged] == ["Até mais.", "Corta."]
    assert merged[1]["start"] == merged[0]["start"]
    assert recoveries[0]["placement"] == "existing_cue_realign"


def test_semantic_verifier_does_not_duplicate_or_replace_ordinary_words():
    primary = [
        {"start": 10.0, "end": 15.0, "text": "Corta, mantenha o endereço."}
    ]
    verifier = [
        {
            "start": 10.0,
            "end": 15.0,
            "text": "Corta, apague o endereço",
            "words": [
                {"word": "Corta", "start": 10.1, "end": 10.6, "probability": 0.99},
                {"word": "apague", "start": 10.7, "end": 11.2, "probability": 0.99},
            ],
        }
    ]

    merged, recoveries = merge_missing_recording_cues(primary, verifier)

    assert merged == primary
    assert recoveries == []


def test_semantic_verifier_rejects_low_probability_cue():
    primary = [{"start": 20.0, "end": 22.0, "text": "Manter o endereço."}]
    verifier = [
        {
            "start": 19.0,
            "end": 22.0,
            "text": "Corta, manter o endereço",
            "words": [
                {"word": "Corta", "start": 19.5, "end": 20.0, "probability": 0.49}
            ],
        }
    ]

    merged, recoveries = merge_missing_recording_cues(primary, verifier)

    assert merged == primary
    assert recoveries == []


def test_existing_cue_gets_more_alignment_context_when_verifier_places_it_earlier():
    primary = [{"start": 185.4, "end": 191.0, "text": "Corta, manter o endereço."}]
    verifier = [
        {
            "start": 184.4,
            "end": 190.0,
            "text": "Corta, manter o endereço",
            "words": [
                {"word": "Corta", "start": 184.42, "end": 185.10, "probability": 0.80}
            ],
        }
    ]

    merged, recoveries = merge_missing_recording_cues(primary, verifier)

    assert merged[0]["start"] == pytest.approx(182.42)
    assert recoveries[0]["placement"] == "existing_cue_context_expanded"
    assert recoveries[0]["requires_component_recovery"] is True


def test_literal_coverage_uses_small_segments_and_marks_cues_for_acoustic_recovery():
    verifier = [
        {
            "start": 10.0,
            "end": 12.0,
            "text": " Corta, manter.",
            "words": [
                {"word": "Corta", "start": 10.2, "end": 10.7, "probability": 0.91}
            ],
        }
    ]

    segments, recoveries = build_literal_coverage_segments(
        verifier, audio_duration=20.0
    )

    assert segments == [{"start": 8.5, "end": 13.5, "text": " Corta, manter."}]
    assert recoveries[0]["placement"] == "coverage_model_cue"
    assert recoveries[0]["requires_component_recovery"] is True


def test_literal_coverage_partitions_padding_before_alignment():
    verifier = [
        {
            "start": 11.66,
            "end": 22.68,
            "text": "Conta cadastro.",
            "words": [
                {"word": "Conta", "start": 11.66, "end": 12.10},
                {"word": "cadastro", "start": 22.30, "end": 22.68},
            ],
        },
        {
            "start": 23.22,
            "end": 24.50,
            "text": "Roteiro de fala.",
            "words": [
                {"word": "Roteiro", "start": 23.22, "end": 23.70},
                {"word": "fala", "start": 24.10, "end": 24.50},
            ],
        },
    ]

    segments, _ = build_literal_coverage_segments(verifier, audio_duration=30.0)

    assert segments[0]["end"] == pytest.approx(22.78)
    assert segments[1]["start"] == pytest.approx(22.78)
    assert segments[0]["end"] <= segments[1]["start"]


def test_literal_coverage_preserves_full_lookback_for_early_cue_golden():
    verifier = [
        {
            "start": 181.32,
            "end": 182.74,
            "text": "os campos e salve.",
            "words": [
                {"word": "os", "start": 181.32, "end": 181.48},
                {"word": "salve", "start": 182.26, "end": 182.74},
            ],
        },
        {
            "start": 184.42,
            "end": 190.36,
            "text": "Corta, manter o endereço.",
            "words": [
                {"word": "Corta", "start": 184.42, "end": 185.10, "probability": 0.8},
                {"word": "manter", "start": 185.40, "end": 185.66},
            ],
        },
    ]

    segments, recoveries = build_literal_coverage_segments(
        verifier, audio_duration=210.0
    )

    assert segments[0]["end"] == pytest.approx(182.92)
    assert segments[1]["start"] == pytest.approx(182.92)
    assert segments[1]["start"] < 183.47
    assert recoveries[0]["primary_segment_index"] == 1


def test_literal_coverage_fuses_native_overlap_and_remaps_recovery_index():
    verifier = [
        {
            "start": 10.0,
            "end": 12.0,
            "text": "Primeira fala.",
            "words": [{"word": "fala", "start": 11.6, "end": 12.2}],
        },
        {
            "start": 12.1,
            "end": 14.0,
            "text": "Corta, próxima.",
            "words": [
                {"word": "Corta", "start": 12.1, "end": 12.6, "probability": 0.9}
            ],
        },
    ]

    segments, recoveries = build_literal_coverage_segments(
        verifier, audio_duration=20.0
    )

    assert segments == [
        {
            "start": 8.5,
            "end": 15.5,
            "text": "Primeira fala. Corta, próxima.",
        }
    ]
    assert recoveries[0]["primary_segment_index"] == 0


def test_literal_coverage_rejects_reverse_native_segments():
    verifier = [
        {"start": 5.0, "end": 6.0, "text": "Depois."},
        {"start": 4.0, "end": 4.5, "text": "Antes."},
    ]

    with pytest.raises(RuntimeError, match="reverse time order"):
        build_literal_coverage_segments(verifier, audio_duration=10.0)


def test_aligned_word_hints_reconcile_numeric_and_hyphen_tokenization():
    aligned = [
        {"word": "4.1,"},
        {"word": "e-mail"},
        {"word": "salvar"},
    ]
    verifier = [
        {
            "words": [
                {"word": "4", "start": 1.0, "end": 1.4, "probability": 0.9},
                {"word": ".1,", "start": 1.4, "end": 2.0, "probability": 0.8},
                {"word": "e", "start": 2.2, "end": 2.3, "probability": 0.7},
                {"word": "-mail", "start": 2.3, "end": 2.7, "probability": 0.6},
                {"word": "salvar", "start": 3.0, "end": 3.5, "probability": 0.95},
            ]
        }
    ]

    hints = build_aligned_word_hints(aligned, verifier)

    assert hints[0] == {
        "aligned_word_index": 0,
        "text": "4.1,",
        "start": 1.0,
        "end": 2.0,
        "probability": 0.8,
        "source_word_count": 2,
    }
    assert hints[1]["start"] == 2.2
    assert hints[1]["end"] == 2.7
    assert hints[1]["source_word_count"] == 2
    assert hints[2]["source_word_count"] == 1


def test_aligned_word_hints_fail_closed_on_text_mismatch():
    with pytest.raises(RuntimeError, match="do not match"):
        build_aligned_word_hints(
            [{"word": "manter"}],
            [
                {
                    "words": [
                        {
                            "word": "cortar",
                            "start": 1.0,
                            "end": 1.5,
                            "probability": 0.9,
                        }
                    ]
                }
            ],
        )
