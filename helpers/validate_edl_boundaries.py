"""Validate EDL cut boundaries against transcript words and source waveform.

This is a QA helper. It does not decide the edit; it highlights boundaries
that may be too close to speech so the agent can inspect or revise them.

The important distinction is:
  - transcript flags can be caused by ASR timestamp drift;
  - waveform flags show whether there is no low-energy point near the cut.

Treat a boundary as high risk when both methods disagree with the cut, or
when the media itself cannot be analyzed.

Usage:
    python helpers/validate_edl_boundaries.py raw_video/edit/edl.json --transcripts raw_video/edit/transcripts -o raw_video/edit/edl_boundary_qc.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64) / 32768.0))))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * math.log10(rms)


def resolve_path(maybe_path: str, base: Path) -> Path:
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def extract_pcm(path: Path, start: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.int16)


def load_words(transcripts_dir: Path | None, source_id: str) -> list[dict[str, Any]]:
    if transcripts_dir is None:
        return []
    path = transcripts_dir / f"{source_id}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    words = []
    for w in data.get("words", []):
        if w.get("type") != "word":
            continue
        if w.get("start") is None or w.get("end") is None:
            continue
        words.append(w)
    return words


def word_at(words: list[dict[str, Any]], t: float, tol: float = 0.025) -> dict[str, Any] | None:
    for w in words:
        start = float(w["start"])
        end = float(w["end"])
        if start + tol < t < end - tol:
            return w
    return None


def nearest_word_edges(words: list[dict[str, Any]], t: float) -> dict[str, Any]:
    prev_word = None
    next_word = None
    for w in words:
        end = float(w["end"])
        start = float(w["start"])
        if end <= t and (prev_word is None or end > float(prev_word["end"])):
            prev_word = w
        if start >= t and (next_word is None or start < float(next_word["start"])):
            next_word = w
    return {
        "prev_text": prev_word.get("text") if prev_word else None,
        "prev_gap_s": round(t - float(prev_word["end"]), 3) if prev_word else None,
        "next_text": next_word.get("text") if next_word else None,
        "next_gap_s": round(float(next_word["start"]) - t, 3) if next_word else None,
    }


def waveform_stats(path: Path, t: float, window: float, threshold_db: float) -> dict[str, Any]:
    sample_rate = 16000
    start = max(0.0, t - window)
    boundary_offset = t - start
    duration = window + boundary_offset

    try:
        samples = extract_pcm(path, start, duration, sample_rate)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {
            "pre_100ms_db": None,
            "post_100ms_db": None,
            "min_near_db": None,
            "min_near_offset_s": None,
            "quiet_near_boundary": False,
            "waveform_error": str(exc),
        }

    def slice_db(a: float, b: float) -> float:
        ia = max(0, int(a * sample_rate))
        ib = min(samples.size, int(b * sample_rate))
        return round(dbfs(samples[ia:ib]), 1)

    bin_s = 0.03
    near_s = 0.18
    bins = []
    n_bins = max(1, int(samples.size / (bin_s * sample_rate)))
    for i in range(n_bins):
        a = int(i * bin_s * sample_rate)
        b = int((i + 1) * bin_s * sample_rate)
        center = ((a + b) / 2.0) / sample_rate - boundary_offset
        if abs(center) <= near_s:
            bins.append((center, dbfs(samples[a:b])))

    min_center, min_near = min(bins, key=lambda x: x[1]) if bins else (None, -120.0)
    return {
        "pre_100ms_db": slice_db(max(0.0, boundary_offset - 0.10), boundary_offset),
        "post_100ms_db": slice_db(boundary_offset, boundary_offset + 0.10),
        "min_near_db": round(min_near, 1),
        "min_near_offset_s": round(min_center, 3) if min_center is not None else None,
        "quiet_near_boundary": bool(min_near <= threshold_db),
        "waveform_error": None,
    }


def validate_boundaries(
    edl_path: Path,
    transcripts_dir: Path | None,
    window: float,
    quiet_threshold_db: float,
) -> dict[str, Any]:
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    edit_dir = edl_path.parent
    sources = {
        source_id: resolve_path(path, edit_dir)
        for source_id, path in edl.get("sources", {}).items()
    }
    words_by_source = {
        source_id: load_words(transcripts_dir, source_id)
        for source_id in sources
    }

    results = []
    for idx, r in enumerate(edl.get("ranges", [])):
        source_id = r["source"]
        source_path = sources[source_id]
        words = words_by_source.get(source_id, [])
        for side in ("start", "end"):
            t = float(r[side])
            inside = word_at(words, t)
            edge_info = nearest_word_edges(words, t)
            risk_flags = []

            if inside:
                risk_flags.append("inside_word")

            if not source_path.exists():
                stats = {
                    "pre_100ms_db": None,
                    "post_100ms_db": None,
                    "min_near_db": None,
                    "min_near_offset_s": None,
                    "quiet_near_boundary": False,
                    "waveform_error": f"source missing: {source_path}",
                }
                risk_flags.append("missing_source")
            else:
                stats = waveform_stats(source_path, t, window, quiet_threshold_db)
                if stats["waveform_error"]:
                    risk_flags.append("waveform_error")
                elif not stats["quiet_near_boundary"]:
                    risk_flags.append("no_quiet_waveform_point_nearby")

            high_risk = (
                "missing_source" in risk_flags
                or "waveform_error" in risk_flags
                or (
                    "inside_word" in risk_flags
                    and "no_quiet_waveform_point_nearby" in risk_flags
                )
            )

            results.append(
                {
                    "range_index": idx,
                    "side": side,
                    "source": source_id,
                    "time_s": round(t, 3),
                    "beat": r.get("beat"),
                    "inside_word": inside.get("text") if inside else None,
                    **edge_info,
                    **stats,
                    "risk_flags": risk_flags,
                    "high_risk": high_risk,
                }
            )

    return {
        "edl": str(edl_path),
        "ranges": len(edl.get("ranges", [])),
        "boundaries": len(results),
        "inside_word_count": sum(1 for r in results if r["inside_word"]),
        "no_quiet_waveform_point_count": sum(
            1 for r in results if "no_quiet_waveform_point_nearby" in r["risk_flags"]
        ),
        "waveform_error_count": sum(1 for r in results if "waveform_error" in r["risk_flags"]),
        "missing_source_count": sum(1 for r in results if "missing_source" in r["risk_flags"]),
        "high_risk_count": sum(1 for r in results if r["high_risk"]),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate EDL boundaries with transcript words and waveform energy")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--transcripts", type=Path, default=None)
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--window", type=float, default=0.35)
    ap.add_argument("--quiet-threshold-db", type=float, default=-38.0)
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")
    transcripts = args.transcripts.resolve() if args.transcripts else None

    summary = validate_boundaries(edl_path, transcripts, args.window, args.quiet_threshold_db)
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote boundary QC -> {args.output}")

    print(
        f"ranges={summary['ranges']} boundaries={summary['boundaries']} "
        f"inside_word={summary['inside_word_count']} "
        f"no_quiet={summary['no_quiet_waveform_point_count']} "
        f"high_risk={summary['high_risk_count']}"
    )


if __name__ == "__main__":
    main()
