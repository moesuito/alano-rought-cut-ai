"""F1.7 regression tests for range-aware preview transcript QC."""

from __future__ import annotations

import copy
import builtins
import sys
import types
from pathlib import Path

import pytest

from helpers.preview_transcript_qc import (
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
            {"source": "source1", "source_in_frame": 0, "source_out_frame": 30},
            {"source": "source1", "source_in_frame": 60, "source_out_frame": 90},
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
        ["corta", "gravando", "som de estalo"],
        edl_data=edl,
        timeline_map=timeline_map,
        source_transcripts=sources,
        edl_hash="edl-hash",
        timeline_map_hash="map-hash",
        source_transcript_hashes={"source1": "source-hash"},
    )


def test_clean_resume_reports_every_join_and_passes():
    report = make_report(*make_context())

    assert report["status"] == "pass"
    assert report["mode"] == "range_aware"
    assert report["summary"]["range_count"] == 2
    assert report["summary"]["join_count"] == 1
    assert report["joins"][0]["status"] == "pass"
    assert "corta" not in report["joins"][0]["actual"]["right_prefix_window"]


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


def test_word_materially_spanning_join_blocks():
    transcript, edl, timeline_map, sources = make_context()
    transcript["words"][1]["end"] = 1.20

    report = make_report(transcript, edl, timeline_map, sources)

    assert "preview_word_spans_join" in report["joins"][0]["blocking_flags"]


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
    assert validate_transcript_report(report, edl["ranges"], timeline_map["ranges"]) == []

    forged = copy.deepcopy(report)
    forged["joins"][0]["blocking_flags"] = ["deformed_right_first_word"]
    assert any(
        "status contradicts" in error
        for error in validate_transcript_report(forged, edl["ranges"], timeline_map["ranges"])
    )

    missing_join = copy.deepcopy(report)
    missing_join["joins"] = []
    assert any(
        "range count minus one" in error
        for error in validate_transcript_report(missing_join, edl["ranges"], timeline_map["ranges"])
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
