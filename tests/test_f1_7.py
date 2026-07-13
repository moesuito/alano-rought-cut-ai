"""F1.7 regression tests for range-aware preview transcript QC."""

from __future__ import annotations

import copy
import builtins
import sys
import types
from pathlib import Path

import pytest

from helpers.preview_transcript_qc import (
    DEFAULT_CUE_TERMS,
    ElevenLabsScribeProvider,
    build_report,
    whisper_cpp_json_to_transcript,
)
from helpers.repair_edl_from_preview import apply_repairs, find_safe_repairs
from helpers.timing import parse_fps_fraction
from helpers.verify_edit_ready import validate_transcript_report


def make_context(
    right_source_text: tuple[str, str] = ("agora", "seguimos"),
    right_preview_text: tuple[str, str] | None = None,
):
    source_words = [
        {"text": "termina", "start": 0.10, "end": 0.30, "type": "word"},
        {"text": "bem", "start": 0.40, "end": 0.60, "type": "word"},
        {"text": "corta", "start": 1.20, "end": 1.40, "type": "word"},
        {"text": right_source_text[0], "start": 2.10, "end": 2.30, "type": "word"},
        {"text": right_source_text[1], "start": 2.40, "end": 2.70, "type": "word"},
    ]
    right_preview_text = right_preview_text or right_source_text
    preview_words = [
        {"text": "termina", "start": 0.10, "end": 0.30, "type": "word"},
        {"text": "bem", "start": 0.40, "end": 0.60, "type": "word"},
        {"text": right_preview_text[0], "start": 1.10, "end": 1.30, "type": "word"},
        {"text": right_preview_text[1], "start": 1.40, "end": 1.70, "type": "word"},
    ]
    transcript = {
        "text": " ".join(word["text"] for word in preview_words),
        "words": preview_words,
    }
    edl = {
        "version": 1,
        "sources": {"source1": "source1.wav"},
        "ranges": [
            {
                "source": "source1",
                "source_in_frame": 0,
                "source_out_frame": 30,
                "lexical_anchors": {
                    "first": {"word_index": 0, **source_words[0]},
                    "last": {"word_index": 1, **source_words[1]},
                },
            },
            {
                "source": "source1",
                "source_in_frame": 60,
                "source_out_frame": 90,
                "lexical_anchors": {
                    "first": {"word_index": 3, **source_words[3]},
                    "last": {"word_index": 4, **source_words[4]},
                },
            },
        ],
        "metadata": {"sequence_fps": "30", "required_beats": []},
    }
    timeline_map = {
        "edl_hash": "edl-hash",
        "output_format": {
            "format": "PCM16",
            "sample_rate": 48000,
            "channels": 2,
            "sequence_fps": 30.0,
        },
        "ranges": [
            {
                "range_index": 0,
                "source": "source1",
                "source_frames": [0, 30],
                "source_sample_interval": [0, 48000],
                "output_cumulative_sample_interval": [0, 48000],
                "lexical_anchors": {
                    "first": {"word_index": 0, **source_words[0]},
                    "last": {"word_index": 1, **source_words[1]},
                },
                "beat_id": "left",
            },
            {
                "range_index": 1,
                "source": "source1",
                "source_frames": [60, 90],
                "source_sample_interval": [96000, 144000],
                "output_cumulative_sample_interval": [48000, 96000],
                "lexical_anchors": {
                    "first": {"word_index": 3, **source_words[3]},
                    "last": {"word_index": 4, **source_words[4]},
                },
                "beat_id": "right",
            },
        ],
    }
    return transcript, edl, timeline_map, {"source1": {"words": source_words}}


def make_report(transcript, edl, timeline_map, sources):
    return build_report(
        transcript,
        None,
        None,
        DEFAULT_CUE_TERMS,
        edl_data=edl,
        timeline_map=timeline_map,
        source_transcripts=sources,
        edl_hash="edl-hash",
        timeline_map_hash="map-hash",
        source_transcript_hashes={"source1": "source-hash"},
    )


def test_build_report_deduplicates_cue_terms_for_readiness_round_trip():
    transcript, edl, timeline_map, sources = make_context()
    report = build_report(
        transcript,
        None,
        None,
        DEFAULT_CUE_TERMS + ["corta"],
        edl_data=edl,
        timeline_map=timeline_map,
        source_transcripts=sources,
    )

    assert report["settings"]["cue_terms"] == DEFAULT_CUE_TERMS
    assert validate_transcript_report(
        report,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
    ) == []


def set_first_range_internal_gap(context, gap_seconds: float):
    transcript, edl, timeline_map, sources = context
    left = sources["source1"]["words"][0]
    right = sources["source1"]["words"][1]
    right["start"] = left["end"] + gap_seconds
    right["end"] = right["start"] + 0.20
    timeline_map["ranges"][0]["lexical_anchors"]["last"] = {
        "word_index": 1,
        **right,
    }
    edl["ranges"][0]["lexical_anchors"]["last"] = {
        "word_index": 1,
        **right,
    }
    return transcript, edl, timeline_map, sources


def make_long_right_context(expected: list[str], actual: list[str]):
    transcript, edl, timeline_map, sources = make_context()
    source_step = 0.75 / max(1, len(expected) - 1)
    source_duration = min(0.06, source_step * 0.70)
    source_prefix = sources["source1"]["words"][:3]
    source_right = [
        {
            "text": token,
            "start": 2.10 + index * source_step,
            "end": 2.10 + index * source_step + source_duration,
            "type": "word",
        }
        for index, token in enumerate(expected)
    ]
    sources["source1"]["words"] = source_prefix + source_right
    timeline_map["ranges"][1]["lexical_anchors"] = {
        "first": {"word_index": 3, **source_right[0]},
        "last": {
            "word_index": len(sources["source1"]["words"]) - 1,
            **source_right[-1],
        },
    }
    edl["ranges"][1]["lexical_anchors"] = copy.deepcopy(
        timeline_map["ranges"][1]["lexical_anchors"]
    )

    preview_step = 0.75 / max(1, len(actual) - 1)
    preview_duration = min(0.06, preview_step * 0.70)
    preview_prefix = transcript["words"][:2]
    preview_right = [
        {
            "text": token,
            "start": 1.10 + index * preview_step,
            "end": 1.10 + index * preview_step + preview_duration,
            "type": "word",
        }
        for index, token in enumerate(actual)
    ]
    transcript["words"] = preview_prefix + preview_right
    transcript["text"] = " ".join(word["text"] for word in transcript["words"])
    return transcript, edl, timeline_map, sources


def add_interword_residual(
    sources,
    *,
    start: float = 2.32,
    end: float = 2.36,
):
    sources["source1"]["_alano_cut"] = {
        "acoustic_timing": {
            "nonblocking_outliers": [
                {
                    "type": "nonblocking_interword_residual",
                    "component": {
                        "index": 7,
                        "start": start,
                        "end": end,
                        "bilateral_start": start,
                        "bilateral_end": end,
                    },
                    "previous_word": {"index": 3, "text": "tela", "end": 2.3},
                    "following_word": {"index": 4, "text": "preencha", "start": 2.4},
                }
            ]
        }
    }


def test_clean_resume_reports_every_join_and_passes():
    context = make_context()
    report = make_report(*context)

    assert report["status"] == "pass"
    assert report["mode"] == "range_aware"
    assert report["summary"]["range_count"] == 2
    assert report["summary"]["join_count"] == 1
    assert report["joins"][0]["status"] == "pass"
    assert "corta" not in report["joins"][0]["actual"]["right_prefix_window"]
    assert validate_transcript_report(
        report,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
        context[0],
        "30",
    ) == []


@pytest.mark.parametrize(
    ("gap_seconds", "expected_status", "expected_check_count"),
    [
        (0.300000, "pass", 0),
        (0.300001, "review", 1),
        (0.301000, "review", 1),
    ],
)
def test_internal_silence_gate_is_strictly_greater_than_300ms(
    gap_seconds, expected_status, expected_check_count
):
    context = set_first_range_internal_gap(make_context(), gap_seconds)

    report = make_report(*context)

    assert report["status"] == expected_status
    assert len(report["internal_silence_checks"]) == expected_check_count
    if expected_check_count:
        check = report["internal_silence_checks"][0]
        assert check["gap_ms"] == pytest.approx(gap_seconds * 1000)
        assert check["blocking_flags"] == ["uncut_internal_silence"]


def test_precise_reasoned_internal_silence_override_is_audited_and_passes():
    transcript, edl, timeline_map, sources = set_first_range_internal_gap(
        make_context(), 0.8
    )
    constraints = {
        "preserve_internal_silences": [{
            "left_word_index": 0,
            "left_word": "termina",
            "right_word_index": 1,
            "right_word": "bem",
            "reason": "pausa dramática intencional",
        }]
    }
    edl["ranges"][0]["boundary_constraints"] = copy.deepcopy(constraints)
    timeline_map["ranges"][0]["boundary_constraints"] = copy.deepcopy(constraints)

    report = make_report(transcript, edl, timeline_map, sources)

    assert report["status"] == "pass"
    assert report["internal_silence_checks"][0]["preserve_override"] is True
    assert report["internal_silence_checks"][0]["override_reason"]
    assert validate_transcript_report(
        report, edl["ranges"], timeline_map["ranges"], sources
    ) == []


def test_internal_silence_wildcard_override_is_rejected():
    transcript, edl, timeline_map, sources = set_first_range_internal_gap(
        make_context(), 0.8
    )
    edl["ranges"][0]["boundary_constraints"] = {
        "preserve_internal_silence": True
    }
    timeline_map["ranges"][0]["boundary_constraints"] = {
        "preserve_internal_silence": True
    }

    with pytest.raises(ValueError, match="unsafe wildcard"):
        make_report(transcript, edl, timeline_map, sources)


def test_readiness_recomputes_and_rejects_omitted_internal_silence_check():
    context = set_first_range_internal_gap(make_context(), 0.8)
    report = make_report(*context)
    report["internal_silence_checks"] = []
    report["summary"].update({
        "internal_silence_count": 0,
        "internal_silence_pass_count": 0,
        "internal_silence_review_count": 0,
    })

    errors = validate_transcript_report(
        report, context[1]["ranges"], context[2]["ranges"], context[3]
    )

    assert "internal silence checks do not match canonical source evidence" in errors


def test_readiness_rejects_timeline_map_anchors_narrower_than_edl():
    transcript, edl, timeline_map, sources = make_context()
    forged_map = copy.deepcopy(timeline_map)
    forged_map["ranges"][0]["lexical_anchors"] = {
        "first": {"word_index": 1, **sources["source1"]["words"][1]},
        "last": {"word_index": 1, **sources["source1"]["words"][1]},
    }
    forged_preview = copy.deepcopy(transcript)
    forged_preview["words"] = forged_preview["words"][1:]
    forged_preview["text"] = " ".join(
        word["text"] for word in forged_preview["words"]
    )
    forged_report = make_report(forged_preview, edl, forged_map, sources)
    assert forged_report["ranges"][0]["status"] == "pass"

    errors = validate_transcript_report(
        forged_report,
        edl["ranges"],
        forged_map["ranges"],
        sources,
        forged_preview,
    )

    assert any("lexical_anchors do not match the EDL" in error for error in errors)


def test_readiness_recomputes_range_words_and_metrics_from_preview_sidecar():
    transcript, edl, timeline_map, sources = make_context()
    report = make_report(transcript, edl, timeline_map, sources)
    forged = copy.deepcopy(report)
    forged["ranges"][0]["expected_words"] = []
    forged["ranges"][0]["actual_words"] = []

    errors = validate_transcript_report(
        forged,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        transcript,
    )

    assert any("expected_words does not match" in error for error in errors)
    assert any("actual_words does not match" in error for error in errors)

    missing_anchor_preview = copy.deepcopy(transcript)
    missing_anchor_preview["words"] = missing_anchor_preview["words"][1:]
    missing_anchor_preview["text"] = " ".join(
        word["text"] for word in missing_anchor_preview["words"]
    )
    errors = validate_transcript_report(
        report,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        missing_anchor_preview,
    )
    assert any("canonical sidecar evidence" in error for error in errors)


def test_readiness_rejects_anchor_identity_forged_in_edl_and_map():
    transcript, edl, timeline_map, sources = make_context()
    report = make_report(transcript, edl, timeline_map, sources)
    forged_edl = copy.deepcopy(edl)
    forged_map = copy.deepcopy(timeline_map)
    for container in (
        forged_edl["ranges"][0],
        forged_map["ranges"][0],
    ):
        container["lexical_anchors"]["first"]["text"] = "forjado"

    errors = validate_transcript_report(
        report,
        forged_edl["ranges"],
        forged_map["ranges"],
        sources,
        transcript,
    )

    assert any("first anchor text is stale" in error for error in errors)


def test_one_token_orphan_corta_blocks_even_when_rest_matches():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"].insert(
        2,
        {"text": "corta", "start": 1.01, "end": 1.07, "type": "word"},
    )
    transcript["text"] = "termina bem corta agora seguimos"

    report = make_report(transcript, edl, timeline_map, sources)

    flags = report["joins"][0]["blocking_flags"]
    assert "unexpected_right_prefix" in flags
    assert "direction_cue" in flags


def test_cue_gate_uses_timed_words_when_top_level_text_omits_cue():
    transcript, edl, timeline_map, sources = make_context(
        ("corta", "seguimos"), ("corta", "seguimos")
    )
    transcript["text"] = "termina bem seguimos"

    report = make_report(transcript, edl, timeline_map, sources)

    assert any(hit["term"] == "corta" for hit in report["cue_hits"])
    assert "possible_leftover_direction_or_audio_event" in report["summary"][
        "blocking_flags"
    ]
    assert report["status"] == "review"

    forged = copy.deepcopy(report)
    forged["settings"]["cue_terms"] = ["zzz"]
    errors = validate_transcript_report(
        forged,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        transcript,
        "30",
    )
    assert "settings.cue_terms omits mandatory recording cues" in errors


def test_duplicate_gate_uses_timed_words_when_top_level_text_omits_repeat():
    base_tokens = [
        "um",
        "dois",
        "tres",
        "quatro",
        "cinco",
        "seis",
        "sete",
        "oito",
        "nove",
        "dez",
        "onze",
        "doze",
        "treze",
        "quatorze",
        "quinze",
        "dezesseis",
    ]
    word_tokens = base_tokens + base_tokens[:4]
    transcript = {
        "text": " ".join(base_tokens),
        "words": [
            {
                "text": token,
                "type": "word",
                "start": index * 0.1,
                "end": (index + 1) * 0.1,
            }
            for index, token in enumerate(word_tokens)
        ],
    }

    report = build_report(
        transcript,
        None,
        None,
        DEFAULT_CUE_TERMS,
    )

    assert any(
        finding["phrase"] == "um dois tres quatro"
        for finding in report["repeated_ngrams"]
    )
    assert "possible_duplicate_content" in report["summary"]["blocking_flags"]
    assert report["status"] == "review"

@pytest.mark.parametrize(
    ("expected", "actual"),
    [("Informe", "Forma"), ("Informe", "Forme"), ("manter", "manteiro")],
)
def test_deformed_first_word_blocks(expected: str, actual: str):
    report = make_report(*make_context((expected, "o endereço"), (actual, "o endereço")))

    assert "deformed_right_first_word" in report["joins"][0]["blocking_flags"]
    assert report["joins"][0]["status"] == "review"


def test_missing_right_first_word_blocks():
    transcript, edl, timeline_map, sources = make_context()
    del transcript["words"][2]
    transcript["text"] = "termina bem seguimos"

    report = make_report(transcript, edl, timeline_map, sources)

    assert "missing_right_first_word" in report["joins"][0]["blocking_flags"]


def test_single_short_function_word_omission_keeps_fluent_join_passable():
    expected = [
        "para", "a", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
    ]
    actual = [token for token in expected if token != "a"]
    report = make_report(*make_long_right_context(expected, actual))

    assert report["ranges"][1]["token_recall"] == pytest.approx(0.9)
    assert report["joins"][0]["right_prefix_similarity"] >= 0.85
    assert report["joins"][0]["checks"]["right_first_word_ok"] is True
    assert report["joins"][0]["checks"]["right_prefix_phrase_ok"] is True
    assert report["joins"][0]["tolerated_prefix_omission"]["normalized"] == "a"
    assert report["status"] == "pass"


def test_content_word_omission_at_join_remains_blocking():
    expected = [
        "para", "nova", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
    ]
    actual = [token for token in expected if token != "nova"]

    report = make_report(*make_long_right_context(expected, actual))

    assert report["ranges"][1]["token_recall"] == pytest.approx(0.9)
    assert report["joins"][0]["right_prefix_similarity"] >= 0.85
    assert "right_prefix_phrase_mismatch" in report["joins"][0]["blocking_flags"]
    assert report["status"] == "review"


def test_short_adjacent_duplicate_inside_range_is_blocking():
    expected = [
        "para", "a", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
    ]
    actual = [*expected[:7], "conta", *expected[7:]]

    context = make_long_right_context(expected, actual)
    report = make_report(*context)

    assert report["ranges"][1]["phrase_similarity"] >= 0.85
    assert report["ranges"][1]["token_recall"] == 1.0
    assert report["ranges"][1]["duplicate_insertions"]
    assert "range_duplicate_content" in report["ranges"][1]["blocking_flags"]
    assert report["status"] == "review"
    assert validate_transcript_report(
        report,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
        context[0],
        "30",
    ) == []


def test_duplicate_block_is_independent_of_dp_alignment_with_repeated_expected_tokens():
    expected = [
        "início", "agora", "seguimos", "bem", "que", "que", "que",
        "agora", "depois", "bem", "que", "bem",
    ]
    actual = [
        "início", "agora", "seguimos", "bem", "que", "que", "que",
        "agora", "que", "que", "agora", "depois", "bem", "que", "bem",
    ]
    context = make_long_right_context(expected, actual)

    report = make_report(*context)

    range_result = report["ranges"][1]
    assert range_result["phrase_similarity"] >= 0.85
    assert range_result["token_recall"] == 1.0
    assert range_result["duplicate_token_excess"] == [
        {"token": "agora", "expected_count": 2, "actual_count": 3, "extra_count": 1},
        {"token": "que", "expected_count": 4, "actual_count": 6, "extra_count": 2},
    ]
    assert "range_duplicate_content" in range_result["blocking_flags"]
    assert report["status"] == "review"
    assert validate_transcript_report(
        report,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
        context[0],
        "30",
    ) == []


def test_reordered_article_at_join_remains_blocking():
    expected = [
        "para", "a", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
        "sem", "alterar", "outros", "dados", "da",
        "sua", "conta", "neste", "momento", "também",
    ]
    actual = ["para", "atualização", "a", *expected[3:]]

    report = make_report(*make_long_right_context(expected, actual))

    assert report["ranges"][1]["token_recall"] == pytest.approx(0.9)
    assert report["joins"][0]["right_prefix_similarity"] >= 0.85
    assert "right_prefix_phrase_mismatch" in report["joins"][0]["blocking_flags"]
    assert report["status"] == "review"


def test_article_moved_past_join_prefix_remains_blocking():
    expected = [
        "para", "a", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
        "sem", "alterar", "outros", "dados", "da",
        "sua", "conta", "neste", "momento", "também",
    ]
    actual = ["para", "atualização", "de", "a", *expected[4:]]

    report = make_report(*make_long_right_context(expected, actual))

    assert report["ranges"][1]["token_recall"] >= 0.9
    assert report["joins"][0]["right_prefix_similarity"] >= 0.85
    assert "right_prefix_phrase_mismatch" in report["joins"][0]["blocking_flags"]
    assert report["joins"][0]["tolerated_prefix_omission"] is None
    assert report["status"] == "review"


def test_phrase_valid_nonfirst_asr_variation_keeps_join_passable():
    expected = [
        "para", "a", "atualização", "de", "endereço",
        "da", "conta", "do", "usuário", "agora",
    ]
    actual = [*expected]
    actual[2] = "atualizações"

    report = make_report(*make_long_right_context(expected, actual))

    assert report["ranges"][1]["phrase_similarity"] >= 0.85
    assert report["ranges"][1]["token_recall"] >= 0.9
    assert report["joins"][0]["right_prefix_similarity"] >= 0.85
    assert report["joins"][0]["right_prefix_reordered"] is False
    assert report["joins"][0]["checks"]["right_prefix_phrase_ok"] is True
    assert report["status"] == "pass"


def test_selected_interword_residual_requires_clean_second_asr_gap():
    context = make_context(("tela", "preencha"), ("tela", "preencha"))
    add_interword_residual(context[3])

    report = make_report(*context)

    check = report["interword_residual_checks"][0]
    assert check["selection_status"] == "selected"
    assert all(check["checks"].values())
    assert check["status"] == "pass"
    assert report["status"] == "pass"
    assert validate_transcript_report(
        report,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
    ) == []


def test_selected_interword_residual_blocks_inserted_short_word():
    transcript, edl, timeline_map, sources = make_context(
        ("tela", "preencha"),
        ("tela", "preencha"),
    )
    add_interword_residual(sources)
    transcript["words"].insert(
        3,
        {"text": "não", "start": 1.32, "end": 1.36, "type": "word"},
    )
    transcript["text"] = "termina bem tela não preencha"

    report = make_report(transcript, edl, timeline_map, sources)

    check = report["interword_residual_checks"][0]
    assert check["selection_status"] == "selected"
    assert check["checks"]["neighbors_consecutive"] is False
    assert check["checks"]["no_preview_word_overlap"] is False
    assert "selected_interword_residual_unresolved" in check["blocking_flags"]
    assert report["status"] == "review"


def test_interword_residual_outside_selection_needs_no_preview_waiver():
    context = make_context(("tela", "preencha"), ("tela", "preencha"))
    add_interword_residual(context[3], start=1.20, end=1.30)

    report = make_report(*context)

    check = report["interword_residual_checks"][0]
    assert check["selection_status"] == "outside_selection"
    assert check["status"] == "pass"
    assert report["status"] == "pass"


def test_readiness_rejects_forged_interword_residual_pass():
    context = make_context(("tela", "preencha"), ("tela", "preencha"))
    add_interword_residual(context[3])
    report = make_report(*context)
    report["interword_residual_checks"][0]["checks"]["neighbors_faithful"] = False

    errors = validate_transcript_report(
        report,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
    )

    assert any("omits unresolved evidence" in error for error in errors)

    omitted = make_report(*context)
    omitted["interword_residual_checks"] = []
    omitted["summary"].update({
        "interword_residual_count": 0,
        "interword_residual_pass_count": 0,
        "interword_residual_review_count": 0,
    })
    omitted_errors = validate_transcript_report(
        omitted,
        context[1]["ranges"],
        context[2]["ranges"],
        context[3],
    )
    assert any("do not match source transcript evidence" in error for error in omitted_errors)


def test_word_materially_spanning_join_blocks():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"][1]["end"] = 1.20

    report = make_report(transcript, edl, timeline_map, sources)

    assert "preview_word_spans_join" in report["joins"][0]["blocking_flags"]


def test_readiness_recomputes_spanning_and_out_of_bounds_preview_words():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"][1]["end"] = 1.20
    spanning_report = make_report(transcript, edl, timeline_map, sources)
    forged_spanning = copy.deepcopy(spanning_report)
    forged_spanning["joins"][0]["actual"]["spanning_words"] = []
    forged_spanning["joins"][0]["blocking_flags"] = []
    forged_spanning["joins"][0]["status"] = "pass"
    forged_spanning["summary"]["blocking_flags"] = []
    forged_spanning["summary"]["status"] = "pass"
    forged_spanning["status"] = "pass"

    spanning_errors = validate_transcript_report(
        forged_spanning,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        transcript,
        "30",
    )
    assert any("spanning words do not match" in error for error in spanning_errors)
    assert any("spanning-word blocker" in error for error in spanning_errors)

    out_of_bounds_transcript, edl, timeline_map, sources = make_context()
    out_of_bounds_transcript["words"].append({
        "text": "extra",
        "start": 3.1,
        "end": 3.2,
        "type": "word",
    })
    out_of_bounds_transcript["text"] += " extra"
    out_of_bounds_report = make_report(
        out_of_bounds_transcript, edl, timeline_map, sources
    )
    forged_out_of_bounds = copy.deepcopy(out_of_bounds_report)
    forged_out_of_bounds["timing_validation"]["out_of_bounds_words"] = []
    forged_out_of_bounds["summary"]["blocking_flags"] = []
    forged_out_of_bounds["summary"]["status"] = "pass"
    forged_out_of_bounds["status"] = "pass"

    timing_errors = validate_transcript_report(
        forged_out_of_bounds,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        out_of_bounds_transcript,
        "30",
    )
    assert any("timing_validation does not match" in error for error in timing_errors)


def test_readiness_recomputes_lexical_join_flow_from_sidecar():
    expected = [
        "para", "nova", "atualizacao", "da", "conta",
        "siga", "os", "passos", "na", "tela",
    ]
    actual = [word for word in expected if word != "nova"]
    transcript, edl, timeline_map, sources = make_long_right_context(
        expected, actual
    )
    report = make_report(transcript, edl, timeline_map, sources)
    assert report["ranges"][1]["status"] == "pass"
    assert report["joins"][0]["status"] == "review"

    forged = copy.deepcopy(report)
    forged["joins"][0]["blocking_flags"] = []
    forged["joins"][0]["status"] = "pass"
    forged["summary"]["join_pass_count"] = 1
    forged["summary"]["join_review_count"] = 0
    forged["summary"]["blocking_flags"] = []
    forged["summary"]["status"] = "pass"
    forged["status"] = "pass"

    errors = validate_transcript_report(
        forged,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
        transcript,
        "30",
    )
    assert any("joins does not match canonical" in error for error in errors)


def test_untimed_preview_word_blocks():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"].append({"text": "extra", "type": "word"})
    transcript["text"] += " extra"

    report = make_report(transcript, edl, timeline_map, sources)

    assert "untimed_preview_words" in report["joins"][0]["blocking_flags"]
    assert "incomplete_word_timestamps" in report["summary"]["blocking_flags"]


def test_repeated_same_word_across_join_is_not_an_orphan():
    transcript, edl, timeline_map, sources = make_context(("para", "atualizar"), ("para", "atualizar"))
    sources["source1"]["words"][1]["text"] = "para"
    timeline_map["ranges"][0]["lexical_anchors"]["last"]["text"] = "para"
    edl["ranges"][0]["lexical_anchors"]["last"]["text"] = "para"
    transcript["words"][1]["text"] = "para"
    transcript["text"] = "termina para para atualizar"

    report = make_report(transcript, edl, timeline_map, sources)

    assert report["joins"][0]["status"] == "pass"


def test_stale_lexical_anchor_is_structural():
    transcript, edl, timeline_map, sources = make_context()
    broken_map = copy.deepcopy(timeline_map)
    broken_map["ranges"][1]["lexical_anchors"]["first"]["text"] = "outro"

    with pytest.raises(ValueError, match="anchor identity is stale"):
        make_report(transcript, edl, broken_map, sources)


def test_text_without_word_timestamps_cannot_pass():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"] = []

    report = make_report(transcript, edl, timeline_map, sources)

    assert report["status"] == "review"
    assert report["summary"]["timed_word_count"] == 0
    assert report["summary"]["join_count"] == len(edl["ranges"]) - 1


def test_readiness_recomputes_join_count_and_status():
    transcript, edl, timeline_map, sources = make_context()
    report = make_report(transcript, edl, timeline_map, sources)
    assert validate_transcript_report(
        report,
        edl["ranges"],
        timeline_map["ranges"],
        sources,
    ) == []

    forged = copy.deepcopy(report)
    forged["joins"][0]["blocking_flags"] = ["deformed_right_first_word"]
    assert any(
        "status contradicts" in error
        for error in validate_transcript_report(
            forged,
            edl["ranges"],
            timeline_map["ranges"],
            sources,
        )
    )

    missing_join = copy.deepcopy(report)
    missing_join["joins"] = []
    assert any(
        "range count minus one" in error
        for error in validate_transcript_report(
            missing_join,
            edl["ranges"],
            timeline_map["ranges"],
            sources,
        )
    )


def test_direct_script_provider_imports_sibling_transcribe(monkeypatch):
    """Direct ``python helpers/...py`` execution has no helpers package."""
    fake_backend = types.ModuleType("transcribe")
    fake_backend.load_api_key = lambda: "test-key"
    fake_backend.call_scribe = lambda path, key: {
        "text": path.name,
        "api_key": key,
    }
    monkeypatch.setitem(sys.modules, "transcribe", fake_backend)

    real_import = builtins.__import__

    def direct_script_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "helpers.transcribe":
            exc = ModuleNotFoundError("No module named 'helpers'")
            exc.name = "helpers"
            raise exc
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", direct_script_import)

    assert ElevenLabsScribeProvider().transcribe(Path("preview.wav")) == {
        "text": "preview.wav",
        "api_key": "test-key",
    }


def test_whisper_cpp_subwords_become_timed_words():
    full_json = {
        "result": {"language": "pt"},
        "transcription": [
            {
                "text": " Corta, manter o endereço.",
                "tokens": [
                    {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}},
                    {"text": " C", "offsets": {"from": 10, "to": 70}},
                    {"text": "orta", "offsets": {"from": 70, "to": 380}},
                    {"text": ",", "offsets": {"from": 380, "to": 530}},
                    {"text": " manter", "offsets": {"from": 850, "to": 1000}},
                    {"text": " o", "offsets": {"from": 1000, "to": 1000}},
                    {"text": " end", "offsets": {"from": 1120, "to": 1300}},
                    {"text": "ere", "offsets": {"from": 1300, "to": 1530}},
                    {"text": "ço", "offsets": {"from": 1530, "to": 1750}},
                    {"text": ".", "offsets": {"from": 1750, "to": 1800}},
                ],
            }
        ],
    }

    transcript = whisper_cpp_json_to_transcript(full_json)

    assert [word["text"] for word in transcript["words"]] == [
        "Corta", "manter", "o", "endereço"
    ]
    assert transcript["words"][0]["start"] == pytest.approx(0.01)
    assert transcript["words"][0]["end"] == pytest.approx(0.38)
    assert transcript["words"][1]["start"] == pytest.approx(0.85)
    assert transcript["words"][2]["end"] == pytest.approx(1.01)
    assert transcript["_alano_cut"]["transcription_provider"] == "local_whisper_cpp"


def test_preview_direction_cue_maps_back_to_exact_source_frame():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"].insert(
        2,
        {"text": "corta", "start": 1.01, "end": 1.07, "type": "word"},
    )
    transcript["text"] = "termina bem corta agora seguimos"
    report = make_report(transcript, edl, timeline_map, sources)
    edl["ranges"][1].update({
        "start": 2.0,
        "end": 3.0,
        "lexical_anchors": {
            "first": {"word_index": 3, "text": "agora", "start": 2.1, "end": 2.3},
            "last": {"word_index": 4, "text": "seguimos", "start": 2.4, "end": 2.7},
        },
    })
    boundary_report = {
        "status": "pass",
        "boundary_evidence": [
            {
                "final_frames": {"in": 0, "out": 30},
                "final_times": {"start": 0.0, "end": 1.0},
                "confidence": {"start": "medium", "end": "medium"},
                "start_side": {"notes": []},
            },
            {
                "final_frames": {"in": 60, "out": 90},
                "final_times": {"start": 2.0, "end": 3.0},
                "confidence": {"start": "medium", "end": "medium"},
                "start_side": {"notes": []},
            },
        ],
    }

    repairs = find_safe_repairs(
        edl,
        timeline_map,
        report,
        acoustic_resolver=lambda proposal: {
            "selected_onset_sample": proposal["source_first_word_onset_sample"],
            "selected_onset_seconds": 2.1,
            "raw_onset_seconds": 2.1,
            "rnn_onset_seconds": 2.1,
            "snr_db": 20.0,
            "correlation": 0.99,
            "sweep_spread_frames": 0.0,
            "sweep_records": [],
        },
    )

    assert len(repairs) == 1
    assert repairs[0]["old_source_in_frame"] == 60
    assert repairs[0]["new_source_in_frame"] == 63
    assert repairs[0]["pre_roll_samples"] == 0
    repaired_edl, repaired_report = apply_repairs(
        edl, boundary_report, repairs, parse_fps_fraction(30)
    )
    assert repaired_edl["ranges"][1]["source_in_frame"] == 63
    assert repaired_edl["ranges"][1]["start"] == pytest.approx(2.1)
    assert repaired_report["boundary_evidence"][1]["final_frames"]["in"] == 63
    assert repaired_report["boundary_evidence"][1]["confidence"]["start"] == "medium"


def test_preview_repair_never_removes_arbitrary_prefix():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"].insert(
        2,
        {"text": "importante", "start": 1.01, "end": 1.07, "type": "word"},
    )
    transcript["text"] = "termina bem importante agora seguimos"
    report = make_report(transcript, edl, timeline_map, sources)
    # Simulate a forged generic cue flag; lexical allowlisting still wins.
    report["joins"][0]["blocking_flags"].append("direction_cue")

    assert find_safe_repairs(edl, timeline_map, report) == []
