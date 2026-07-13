"""Apply narrowly-scoped, transcript-proven cue repairs to a refined EDL.

The first acoustic pass can be misled when source-ASR timestamps drift far
enough that a rejected recording cue overlaps the expected first word.  This
helper closes that loop using the *rendered* preview transcript:

    preview cue -> expected first word -> preview sample -> source sample/frame

Only an unexpected prefix made entirely of known direction cues is eligible.
Arbitrary extra speech, missing/deformed anchors, and ambiguous alignments stay
in review.  The EDL and boundary report are updated atomically and the original
EDL is preserved in the normal content-addressed backup directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.preview_transcript_qc import DEFAULT_CUE_TERMS, tokenize
from helpers.timing import frame_to_sample, parse_fps_fraction
from helpers.audio_analysis import (
    DEFAULT_VAD_PARAMS,
    get_combined_activity,
    run_vad_hysteresis,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object")
    return data


def _int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _known_cue_tokens() -> set[str]:
    result: set[str] = set()
    for term in DEFAULT_CUE_TERMS:
        result.update(tokenize(term))
    return result


def _first_component(
    activity: np.ndarray,
    *,
    cue_end_s: float,
    expected_onset_s: float,
    expected_end_s: float,
) -> tuple[int, int] | None:
    """Return the first separated activity run inside the preview bracket."""
    hop_s = float(DEFAULT_VAD_PARAMS["hop_ms"]) / 1000.0
    minimum_start = cue_end_s + 0.025
    maximum_start = expected_onset_s + 0.150
    minimum_end = expected_onset_s - 0.150
    maximum_end = expected_end_s + 0.150
    index = max(0, int(math.floor(minimum_start / hop_s)))
    while index < len(activity):
        if not activity[index]:
            index += 1
            continue
        start = index
        while index < len(activity) and activity[index]:
            index += 1
        end = index
        start_s = start * hop_s
        end_s = end * hop_s
        if start_s > maximum_start:
            return None
        if start_s >= minimum_start and end_s >= minimum_end and start_s <= maximum_end:
            return start, end
    return None


def make_acoustic_resolver(
    edit_dir: Path,
    transcripts_dir: Path,
    boundary_report: dict[str, Any],
    fps: Fraction,
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Build a lazy raw/RNNoise resolver for preview-proposed brackets."""
    fingerprints = boundary_report.get("source_fingerprints")
    if not isinstance(fingerprints, dict):
        raise ValueError("boundary report source_fingerprints is invalid")
    profiles: dict[str, dict[str, Any]] = {}

    def load_profile(source_id: str) -> dict[str, Any]:
        cached = profiles.get(source_id)
        if cached is not None:
            return cached
        fingerprint = fingerprints.get(source_id)
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError(f"boundary report lacks fingerprint for {source_id!r}")
        analysis_dir = edit_dir / "audio_analysis"
        raw_path = analysis_dir / f"{source_id}_{fingerprint}_raw.pcm"
        rnn_path = analysis_dir / f"{source_id}_{fingerprint}_rnn.pcm"
        transcript_path = transcripts_dir / f"{source_id}.json"
        if not raw_path.is_file() or not rnn_path.is_file():
            raise ValueError(f"cached raw/RNNoise analysis is missing for {source_id!r}")
        transcript = _read_object(transcript_path, f"source transcript {source_id!r}")
        words = [
            item for item in transcript.get("words", [])
            if isinstance(item, dict) and item.get("type") == "word"
        ]
        words.sort(key=lambda item: (float(item["start"]), float(item["end"])))
        if not words:
            raise ValueError(f"source transcript {source_id!r} has no timed words")
        activity = get_combined_activity(
            raw_path,
            rnn_path,
            DEFAULT_VAD_PARAMS,
            words,
        )
        cached = {
            "words": words,
            "activity_raw": activity[0],
            "activity_rnn": activity[1],
            "rms_raw": activity[3],
            "rms_rnn": activity[4],
            "nf_raw": activity[5],
            "nf_rnn": activity[6],
        }
        profiles[source_id] = cached
        return cached

    def resolve(proposal: dict[str, Any]) -> dict[str, Any] | None:
        source_id = str(proposal["source"])
        profile = load_profile(source_id)
        first_index = proposal.get("source_first_word_index")
        if not isinstance(first_index, int) or isinstance(first_index, bool) or first_index <= 0:
            return None
        words = profile["words"]
        if first_index >= len(words):
            return None
        previous_tokens = tokenize(str(words[first_index - 1].get("text") or ""))
        cue_tokens = [token for text in proposal["cue_words"] for token in tokenize(text)]
        if not previous_tokens or previous_tokens != cue_tokens:
            return None

        cue_end_s = float(Fraction(proposal["source_cue_end_sample"], 48000))
        expected_onset_s = float(Fraction(proposal["source_first_word_onset_sample"], 48000))
        expected_end_s = float(Fraction(proposal["source_first_word_end_sample"], 48000))
        rms_raw = profile["rms_raw"]
        rms_rnn = profile["rms_rnn"]
        nf_raw = profile["nf_raw"]
        nf_rnn = profile["nf_rnn"]
        gap_frames = int(DEFAULT_VAD_PARAMS["gap_fill_ms"] / DEFAULT_VAD_PARAMS["hop_ms"])
        transient_frames = int(
            DEFAULT_VAD_PARAMS["transient_protection_ms"] / DEFAULT_VAD_PARAMS["hop_ms"]
        )
        sweep_records = []
        raw_runs: list[tuple[int, int]] = []
        rnn_runs: list[tuple[int, int]] = []
        for high_delta, low_delta in ((-1.0, -0.5), (0.0, 0.0), (1.0, 0.5)):
            raw_activity = run_vad_hysteresis(
                rms_raw,
                nf_raw,
                DEFAULT_VAD_PARAMS["threshold_high_db"] + high_delta,
                DEFAULT_VAD_PARAMS["threshold_low_db"] + low_delta,
                gap_frames,
                transient_frames,
            )
            rnn_activity = run_vad_hysteresis(
                rms_rnn,
                nf_rnn,
                DEFAULT_VAD_PARAMS["threshold_high_db"] + high_delta,
                DEFAULT_VAD_PARAMS["threshold_low_db"] + low_delta,
                gap_frames,
                transient_frames,
            )
            raw_run = _first_component(
                raw_activity,
                cue_end_s=cue_end_s,
                expected_onset_s=expected_onset_s,
                expected_end_s=expected_end_s,
            )
            rnn_run = _first_component(
                rnn_activity,
                cue_end_s=cue_end_s,
                expected_onset_s=expected_onset_s,
                expected_end_s=expected_end_s,
            )
            if raw_run is None or rnn_run is None:
                return None
            raw_runs.append(raw_run)
            rnn_runs.append(rnn_run)
            sweep_records.append({
                "threshold_delta": [high_delta, low_delta],
                "raw_onset_seconds": raw_run[0] * 0.005,
                "rnn_onset_seconds": rnn_run[0] * 0.005,
            })

        onset_bins = [run[0] for run in raw_runs + rnn_runs]
        onset_frames = [
            (Fraction(index * int(DEFAULT_VAD_PARAMS["hop_ms"]) * 48, 48000) * fps)
            for index in onset_bins
        ]
        onset_frame_values = [float(value) for value in onset_frames]
        if max(onset_frame_values) - min(onset_frame_values) > 2.0:
            return None
        selected_bin = min(onset_bins)
        selected_end_bin = max(run[1] for run in raw_runs + rnn_runs)
        if selected_end_bin <= selected_bin:
            return None
        snr_db = float(np.mean(rms_raw[selected_bin:selected_end_bin] - nf_raw[selected_bin:selected_end_bin]))
        raw_slice = rms_raw[selected_bin:selected_end_bin]
        rnn_slice = rms_rnn[selected_bin:selected_end_bin]
        if len(raw_slice) < 3 or np.std(raw_slice) <= 1e-6 or np.std(rnn_slice) <= 1e-6:
            return None
        correlation = float(np.corrcoef(raw_slice, rnn_slice)[0, 1])
        if not np.isfinite(snr_db) or snr_db < 8.0:
            return None
        if not np.isfinite(correlation) or correlation < 0.8:
            return None

        hop_samples = int(DEFAULT_VAD_PARAMS["sample_rate"] * DEFAULT_VAD_PARAMS["hop_ms"] / 1000)
        selected_sample = selected_bin * hop_samples
        return {
            "raw_onset_seconds": min(run[0] for run in raw_runs) * 0.005,
            "rnn_onset_seconds": min(run[0] for run in rnn_runs) * 0.005,
            "selected_onset_seconds": float(Fraction(selected_sample, 48000)),
            "selected_onset_sample": selected_sample,
            "snr_db": snr_db,
            "correlation": correlation,
            "sweep_spread_frames": max(onset_frame_values) - min(onset_frame_values),
            "sweep_records": sweep_records,
        }

    return resolve


def find_safe_repairs(
    edl: dict[str, Any],
    timeline_map: dict[str, Any],
    preview_report: dict[str, Any],
    acoustic_resolver: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    ranges = edl.get("ranges")
    map_ranges = timeline_map.get("ranges")
    report_ranges = preview_report.get("ranges")
    joins = preview_report.get("joins")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("EDL ranges must be a non-empty list")
    if not isinstance(map_ranges, list) or len(map_ranges) != len(ranges):
        raise ValueError("timeline map range count does not match EDL")
    if not isinstance(report_ranges, list) or len(report_ranges) != len(ranges):
        raise ValueError("preview report range count does not match EDL")
    if not isinstance(joins, list) or len(joins) != max(0, len(ranges) - 1):
        raise ValueError("preview report must contain exactly range_count - 1 joins")

    output_format = timeline_map.get("output_format")
    if not isinstance(output_format, dict):
        raise ValueError("timeline map output_format is missing")
    sample_rate = _int(output_format.get("sample_rate"), "sample_rate")
    if sample_rate != 48000:
        raise ValueError("preview repair requires a 48 kHz timeline map")
    fps = parse_fps_fraction(output_format.get("sequence_fps"))
    cue_tokens = _known_cue_tokens()
    repairs: list[dict[str, Any]] = []

    for join_position, join in enumerate(joins):
        if not isinstance(join, dict):
            raise ValueError(f"join {join_position} must be an object")
        flags = join.get("blocking_flags")
        if not isinstance(flags, list):
            raise ValueError(f"join {join_position} blocking_flags must be a list")
        if "unexpected_right_prefix" not in flags or "direction_cue" not in flags:
            continue

        right_index = _int(join.get("right_range_index"), "right_range_index")
        if right_index != join_position + 1 or not 0 <= right_index < len(ranges):
            raise ValueError(f"join {join_position} has an invalid right range identity")
        unexpected = join.get("unexpected_prefix")
        if not isinstance(unexpected, list) or not unexpected:
            raise ValueError(f"join {join_position} lacks unexpected-prefix evidence")
        unexpected_tokens = [
            token
            for record in unexpected
            if isinstance(record, dict)
            for token in tokenize(str(record.get("text") or ""))
        ]
        if not unexpected_tokens or any(token not in cue_tokens for token in unexpected_tokens):
            # Never auto-remove arbitrary lexical content.
            continue

        range_report = report_ranges[right_index]
        if not isinstance(range_report, dict) or range_report.get("range_index") != right_index:
            raise ValueError(f"preview range {right_index} has an invalid identity")
        alignment = range_report.get("alignment")
        expected_words = range_report.get("expected_words")
        if not isinstance(alignment, list) or not isinstance(expected_words, list) or not expected_words:
            raise ValueError(f"preview range {right_index} lacks lexical alignment evidence")
        expected_first = expected_words[0]
        if not isinstance(expected_first, dict):
            raise ValueError(f"preview range {right_index} has an invalid first expected word")

        first_match = None
        for operation in alignment:
            if not isinstance(operation, dict) or operation.get("expected_index") != 0:
                continue
            if operation.get("op") not in {"equal", "fuzzy"}:
                continue
            if float(operation.get("similarity", 0.0)) < 0.90:
                continue
            actual = operation.get("actual")
            if isinstance(actual, dict):
                first_match = actual
                break
        if first_match is None:
            continue

        actual_start = _int(first_match.get("start_sample"), "first-word start_sample")
        actual_end = _int(first_match.get("end_sample"), "first-word end_sample")
        if actual_end <= actual_start:
            raise ValueError(f"preview range {right_index} first word has invalid samples")
        cue_end = max(_int(record.get("end_sample"), "cue end_sample") for record in unexpected)
        if cue_end > actual_start:
            # No clean acoustic separation: human review is required.
            continue

        map_range = map_ranges[right_index]
        edl_range = ranges[right_index]
        if not isinstance(map_range, dict) or not isinstance(edl_range, dict):
            raise ValueError(f"range {right_index} must be an object")
        if map_range.get("range_index") != right_index:
            raise ValueError(f"timeline map range {right_index} has an invalid identity")
        output_interval = map_range.get("output_cumulative_sample_interval")
        source_interval = map_range.get("source_sample_interval")
        source_frames = map_range.get("source_frames")
        if not (
            isinstance(output_interval, list)
            and len(output_interval) == 2
            and isinstance(source_interval, list)
            and len(source_interval) == 2
            and isinstance(source_frames, list)
            and len(source_frames) == 2
        ):
            raise ValueError(f"timeline map range {right_index} has invalid intervals")
        output_start, output_end = (_int(v, "output sample") for v in output_interval)
        source_start_sample, source_end_sample = (_int(v, "source sample") for v in source_interval)
        old_in_frame, out_frame = (_int(v, "source frame") for v in source_frames)
        if edl_range.get("source_in_frame") != old_in_frame or edl_range.get("source_out_frame") != out_frame:
            raise ValueError(f"timeline map range {right_index} is stale for the EDL")
        if edl_range.get("boundary_adjustments"):
            # A range gets at most one automatic trim attempt.
            continue
        if not output_start <= actual_start < actual_end <= output_end:
            raise ValueError(f"preview range {right_index} first word is outside its mapped audio")

        preview_onset_source_sample = source_start_sample + (actual_start - output_start)
        preview_end_source_sample = source_start_sample + (actual_end - output_start)
        cue_end_source_sample = source_start_sample + (cue_end - output_start)
        proposal = {
            "range_index": right_index,
            "source": edl_range.get("source"),
            "cue_words": [str(record.get("text") or "") for record in unexpected],
            "expected_first_word": str(expected_first.get("text") or ""),
            "actual_first_word": str(first_match.get("text") or ""),
            "source_first_word_index": expected_first.get("source_word_index"),
            "source_first_word_onset_sample": preview_onset_source_sample,
            "source_first_word_end_sample": preview_end_source_sample,
            "source_cue_end_sample": cue_end_source_sample,
        }
        if acoustic_resolver is None:
            continue
        acoustic = acoustic_resolver(proposal)
        if not isinstance(acoustic, dict):
            continue
        onset_source_sample = _int(acoustic.get("selected_onset_sample"), "acoustic onset sample")
        if onset_source_sample <= cue_end_source_sample:
            continue
        exact_frame = Fraction(onset_source_sample, sample_rate) * fps
        new_in_frame = exact_frame.numerator // exact_frame.denominator
        if new_in_frame <= old_in_frame:
            continue
        if float(Fraction(new_in_frame - old_in_frame, 1) / fps) > 2.0:
            continue
        if new_in_frame >= out_frame:
            raise ValueError(f"preview repair would empty range {right_index}")
        new_in_sample = frame_to_sample(new_in_frame, fps)
        if new_in_sample < cue_end_source_sample:
            # Flooring is allowed only when it remains after the rejected cue.
            new_in_frame += 1
            new_in_sample = frame_to_sample(new_in_frame, fps)
        if new_in_frame >= out_frame or new_in_sample < cue_end_source_sample:
            continue
        next_frame_sample = frame_to_sample(new_in_frame + 1, fps)
        if not new_in_sample <= onset_source_sample < next_frame_sample:
            raise ValueError(f"preview repair lost first-word frame containment for range {right_index}")

        repairs.append({
            **proposal,
            "old_source_in_frame": old_in_frame,
            "new_source_in_frame": new_in_frame,
            "source_out_frame": out_frame,
            "preview_first_word_start_sample": actual_start,
            "preview_first_word_end_sample": actual_end,
            "preview_cue_end_sample": cue_end,
            "preview_mapped_first_word_onset_sample": preview_onset_source_sample,
            "preview_mapped_first_word_end_sample": preview_end_source_sample,
            "source_first_word_onset_sample": onset_source_sample,
            "source_first_word_onset_seconds": float(Fraction(onset_source_sample, sample_rate)),
            "pre_roll_samples": onset_source_sample - new_in_sample,
            "acoustic_evidence": acoustic,
            "reason": "preview_direction_cue_before_expected_first_word",
        })

    return repairs


def apply_repairs(
    edl: dict[str, Any],
    boundary_report: dict[str, Any],
    repairs: list[dict[str, Any]],
    fps: Fraction,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated_edl = json.loads(json.dumps(edl))
    updated_boundary = json.loads(json.dumps(boundary_report))
    boundary_evidence = updated_boundary.get("boundary_evidence")
    if not isinstance(boundary_evidence, list):
        raise ValueError("boundary report boundary_evidence must be a list")

    for repair in repairs:
        index = repair["range_index"]
        edl_range = updated_edl["ranges"][index]
        if not isinstance(edl_range, dict):
            raise ValueError(f"range {index} repair target is invalid")
        if not 0 <= index < len(boundary_evidence):
            raise ValueError(f"range {index} boundary evidence is missing")
        evidence = boundary_evidence[index]
        if not isinstance(evidence, dict):
            raise ValueError(f"range {index} boundary evidence is invalid")
        final_frames = evidence.get("final_frames")
        final_times = evidence.get("final_times")
        if not isinstance(final_frames, dict) or not isinstance(final_times, dict):
            raise ValueError(f"range {index} boundary evidence lacks final boundaries")
        new_frame = repair["new_source_in_frame"]
        new_time = float(Fraction(new_frame, 1) / fps)
        edl_range["source_in_frame"] = new_frame
        edl_range["start"] = round(new_time, 6)
        final_frames["in"] = new_frame
        final_times["start"] = round(new_time, 6)
        anchors = edl_range.get("lexical_anchors")
        if not isinstance(anchors, dict) or not isinstance(anchors.get("first"), dict):
            raise ValueError(f"range {index} is missing its first lexical anchor")
        anchors["first"]["acoustic_onset"] = repair["source_first_word_onset_seconds"]
        adjustments = edl_range.setdefault("boundary_adjustments", [])
        if not isinstance(adjustments, list):
            raise ValueError(f"range {index} boundary_adjustments must be a list")
        adjustments.append(dict(repair))

    updated_edl["total_duration_s"] = round(
        sum(
            float(Fraction(item["source_out_frame"] - item["source_in_frame"], 1) / fps)
            for item in updated_edl["ranges"]
        ),
        6,
    )
    metadata = updated_edl.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("EDL metadata must be an object")
    metadata["preview_boundary_repair"] = {
        "schema_version": 1,
        "report": "preview_boundary_repair.json",
    }
    return updated_edl, updated_boundary


def _create_exclusive_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"immutable backup collision at {path}")
        return
    try:
        with open(path, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError(f"immutable backup collision at {path}")


def _write_pair_atomic(
    edl_path: Path,
    edl_text: str,
    report_path: Path,
    report_text: str,
) -> None:
    staged = [
        (edl_path, edl_text, edl_path.with_suffix(edl_path.suffix + f".{os.getpid()}.tmp")),
        (report_path, report_text, report_path.with_suffix(report_path.suffix + f".{os.getpid()}.tmp")),
    ]
    originals = {path: path.read_bytes() if path.exists() else None for path, _, _ in staged}
    replaced: list[Path] = []
    try:
        for path, content, temp in staged:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for path, _, temp in staged:
            os.replace(str(temp), str(path))
            replaced.append(path)
    except Exception:
        for _, _, temp in staged:
            if temp.exists():
                temp.unlink()
        for path in replaced:
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                rollback = path.with_suffix(path.suffix + f".{os.getpid()}.rollback")
                with open(rollback, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(str(rollback), str(path))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair direction-cue leakage from timed preview transcript evidence"
    )
    parser.add_argument("edl", type=Path, help="Refined edit/edl.json")
    parser.add_argument("--timeline-map", type=Path, required=True)
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--boundary-report", type=Path, required=True)
    parser.add_argument("--transcripts", type=Path, default=None)
    args = parser.parse_args()

    edl_path = args.edl.resolve()
    map_path = args.timeline_map.resolve()
    preview_path = args.preview_report.resolve()
    boundary_path = args.boundary_report.resolve()
    transcripts_dir = (
        args.transcripts.resolve() if args.transcripts else edl_path.parent / "transcripts"
    )
    try:
        edl_raw_bytes = edl_path.read_bytes()
        edl_raw = edl_raw_bytes.decode("utf-8")
        edl = json.loads(edl_raw)
        if not isinstance(edl, dict):
            raise ValueError("EDL must be a JSON object")
        timeline_map = _read_object(map_path, "timeline map")
        preview_report = _read_object(preview_path, "preview transcript report")
        boundary_report = _read_object(boundary_path, "boundary report")
        edl_hash = file_sha256(edl_path)
        map_hash = file_sha256(map_path)
        if timeline_map.get("edl_hash") != edl_hash:
            raise ValueError("timeline map is stale for the EDL")
        if preview_report.get("edl_hash") != edl_hash:
            raise ValueError("preview transcript report is stale for the EDL")
        if preview_report.get("timeline_map_hash") != map_hash:
            raise ValueError("preview transcript report is stale for the timeline map")
        if boundary_report.get("output_edl_hash") != edl_hash:
            raise ValueError("boundary report is stale for the EDL")

        fps = parse_fps_fraction(timeline_map.get("output_format", {}).get("sequence_fps"))
        acoustic_resolver = make_acoustic_resolver(
            edl_path.parent,
            transcripts_dir,
            boundary_report,
            fps,
        )
        repairs = find_safe_repairs(edl, timeline_map, preview_report, acoustic_resolver)
        if not repairs:
            print("No safe preview cue repair is available; review is still required.", file=sys.stderr)
            sys.exit(2)
        updated_edl, updated_boundary = apply_repairs(edl, boundary_report, repairs, fps)
        updated_edl_text = json.dumps(updated_edl, indent=2, ensure_ascii=False)
        updated_hash = json_sha256(updated_edl_text)
        updated_boundary["previous_output_edl_hash"] = edl_hash
        updated_boundary["output_edl_hash"] = updated_hash
        updated_boundary["preview_repair"] = {
            "schema_version": 1,
            "input_edl_hash": edl_hash,
            "timeline_map_hash": map_hash,
            "preview_transcript_report_hash": file_sha256(preview_path),
            "preview_wav_hash": preview_report.get("preview_wav_hash"),
            "repairs": repairs,
        }
        updated_boundary_text = json.dumps(updated_boundary, indent=2, ensure_ascii=False)

        backup_dir = edl_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"edl.{edl_hash}.json"
        if not backup_path.exists():
            try:
                with open(backup_path, "xb") as handle:
                    handle.write(edl_raw_bytes)
            except FileExistsError:
                pass
        _write_pair_atomic(edl_path, updated_edl_text, boundary_path, updated_boundary_text)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Fatal Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Applied {len(repairs)} preview-proven boundary repair(s): {edl_path}")


if __name__ == "__main__":
    main()
