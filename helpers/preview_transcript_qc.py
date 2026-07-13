"""QC the mandatory transcript of the rendered preview WAV.

This helper catches content problems that boundary checks cannot see:
duplicated lines, leftover direction words like "corta", clipped lexical
anchors, and words that crossed an edit join.

The production path rebuilds the expected text from the exact words selected
by the refined EDL.  ``quote`` fields are intentionally never evidence.

Usage:
    python helpers/preview_transcript_qc.py edit/preview.wav --edl edit/edl.json
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CUE_TERMS = [
    "corta",
    "gravando",
    "volta",
    "refaz",
    "desculpa",
    "pigarro",
    "estalo",
    "som de estalo",
    "okey",
]

def strip_accents(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize_text(text: str) -> str:
    text = strip_accents(text).lower()
    text = text.replace("-", "")
    text = re.sub(r"[\[\](){}]", " ", text)
    text = re.sub(r"[^a-z0-9\s.,!?;:-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize_text(text))


def transcript_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("text"), str) and data["text"].strip():
        return data["text"].strip()

    parts: list[str] = []
    for w in data.get("words", []):
        raw = (w.get("text") or "").strip()
        if not raw:
            continue
        if w.get("type") == "audio_event" and not raw.startswith("("):
            raw = f"({raw})"
        parts.append(raw)
    text = " ".join(parts)
    return (
        text.replace(" ,", ",")
        .replace(" .", ".")
        .replace(" ?", "?")
        .replace(" !", "!")
        .strip()
    )


def read_transcript(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return transcript_text(data)


def split_sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    return [c.strip() for c in chunks if c.strip()]


def token_ratio(a: str, b: str) -> float:
    at = " ".join(tokenize(a))
    bt = " ".join(tokenize(b))
    if not at or not bt:
        return 0.0
    return difflib.SequenceMatcher(None, at, bt).ratio()


def adjacent_repeats(sentences: list[str], threshold: float = 0.76) -> list[dict[str, Any]]:
    repeats = []
    for idx in range(len(sentences) - 1):
        ratio = token_ratio(sentences[idx], sentences[idx + 1])
        if ratio >= threshold:
            repeats.append(
                {
                    "sentence_index": idx,
                    "similarity": round(ratio, 3),
                    "first": sentences[idx],
                    "second": sentences[idx + 1],
                }
            )
    return repeats


def repeated_ngrams(tokens: list[str], min_n: int = 4, max_n: int = 8) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for n in range(max_n, min_n - 1, -1):
        seen: dict[tuple[str, ...], list[int]] = {}
        for i in range(0, max(0, len(tokens) - n + 1)):
            gram = tuple(tokens[i : i + n])
            seen.setdefault(gram, []).append(i)
        for gram, positions in seen.items():
            if len(positions) < 2:
                continue
            if any(abs(a - b) <= 20 for a in positions for b in positions if a != b):
                found.append(
                    {
                        "phrase": " ".join(gram),
                        "n": n,
                        "positions": positions[:5],
                        "count": len(positions),
                    }
                )
        if found:
            break
    return found[:20]


def cue_hits(normalized_text: str, terms: list[str]) -> list[dict[str, Any]]:
    hits = []
    for term in terms:
        normalized_term = normalize_text(term)
        if not normalized_term:
            continue
        pattern = r"\b" + re.escape(normalized_term) + r"\b"
        matches = list(re.finditer(pattern, normalized_text))
        if matches:
            hits.append(
                {
                    "term": term,
                    "count": len(matches),
                    "positions": [m.start() for m in matches[:10]],
                }
            )
    return hits


def load_expected(args: argparse.Namespace) -> str | None:
    if args.expected_text:
        return args.expected_text.read_text(encoding="utf-8")
    if args.expected_transcript:
        return read_transcript(args.expected_transcript)
    return None


def compare_expected(actual: str, expected: str | None) -> dict[str, Any] | None:
    if not expected:
        return None

    actual_norm = " ".join(tokenize(actual))
    expected_norm = " ".join(tokenize(expected))
    ratio = difflib.SequenceMatcher(None, expected_norm, actual_norm).ratio()

    expected_tokens = tokenize(expected)
    actual_tokens = tokenize(actual)
    expected_counts = Counter(expected_tokens)
    actual_counts = Counter(actual_tokens)
    missing = []
    extra = []
    for token, count in expected_counts.items():
        if actual_counts[token] < count:
            missing.append({"token": token, "missing_count": count - actual_counts[token]})
    for token, count in actual_counts.items():
        if expected_counts[token] < count:
            extra.append({"token": token, "extra_count": count - expected_counts[token]})

    # Calculate expected-token recall
    matched_tokens = 0
    for token, count in expected_counts.items():
        matched_tokens += min(count, actual_counts.get(token, 0))
    recall = matched_tokens / len(expected_tokens) if expected_tokens else 1.0

    # Find missing spans of >= 3 tokens
    matcher = difflib.SequenceMatcher(None, expected_tokens, actual_tokens)
    matching_blocks = matcher.get_matching_blocks()
    missing_spans = []
    last_expected_idx = 0
    for block in matching_blocks:
        expected_idx = block.a
        gap = expected_idx - last_expected_idx
        if gap >= 3:
            missing_spans.append(" ".join(expected_tokens[last_expected_idx:expected_idx]))
        last_expected_idx = expected_idx + block.size
    tail_gap = len(expected_tokens) - last_expected_idx
    if tail_gap >= 3:
        missing_spans.append(" ".join(expected_tokens[last_expected_idx:]))

    return {
        "similarity": round(ratio, 3),
        "recall": round(recall, 3),
        "missing_tokens": missing[:40],
        "extra_tokens": extra[:40],
        "missing_spans": missing_spans,
    }


class TranscriptProvider:
    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        raise NotImplementedError()


class ElevenLabsScribeProvider(TranscriptProvider):
    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        # Keep both invocation modes working: ``python -m helpers...`` imports
        # through the package, while ``python helpers\preview_transcript_qc.py``
        # places the helpers directory itself on sys.path.
        try:
            from helpers.transcribe import call_scribe, load_api_key
        except ModuleNotFoundError as exc:
            if exc.name != "helpers":
                raise
            from transcribe import call_scribe, load_api_key
        api_key = load_api_key()
        return call_scribe(audio_path, api_key)


class WhisperXTranscriptProvider(TranscriptProvider):
    """Normative local CUDA provider with forced alignment and diarization."""

    def __init__(self, config: Any = None):
        self.config = config

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        try:
            from helpers.transcription_providers import WhisperXProvider
        except ModuleNotFoundError as exc:
            if exc.name != "helpers":
                raise
            from transcription_providers import WhisperXProvider
        return WhisperXProvider(self.config).transcribe(audio_path)


class MockTranscriptProvider(TranscriptProvider):
    def __init__(self, response_data: dict[str, Any]):
        self.response_data = response_data

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        return self.response_data


def whisper_cpp_json_to_transcript(data: dict[str, Any]) -> dict[str, Any]:
    """Convert whisper.cpp full JSON tokens into timed lexical words.

    whisper.cpp exposes BPE pieces rather than ready-made words. A leading
    space starts a word and following pieces complete it (``" C"`` +
    ``"orta"`` -> ``"Corta"``). Punctuation is not promoted to a lexical
    word, so it cannot accidentally span an edit join.
    """
    words: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and tokenize(str(current.get("text") or "")):
            if float(current["end"]) <= float(current["start"]):
                # whisper.cpp timestamps are quantized to 10 ms and can
                # collapse short function words to a point.
                current["end"] = float(current["start"]) + 0.01
            words.append(current)
        current = None

    transcription = data.get("transcription")
    if not isinstance(transcription, list):
        raise ValueError("whisper.cpp JSON is missing transcription segments")

    for segment in transcription:
        if not isinstance(segment, dict):
            continue
        tokens_data = segment.get("tokens")
        if not isinstance(tokens_data, list):
            continue
        for token in tokens_data:
            if not isinstance(token, dict):
                continue
            raw = str(token.get("text") or "")
            stripped = raw.strip()
            if not stripped or (stripped.startswith("[") and stripped.endswith("]")):
                continue
            offsets = token.get("offsets")
            if not isinstance(offsets, dict):
                continue
            start_ms = offsets.get("from")
            end_ms = offsets.get("to")
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (start_ms, end_ms)
            ):
                continue

            lexical_piece = bool(tokenize(stripped))
            if not lexical_piece:
                flush()
                continue
            if raw[:1].isspace() or current is None:
                flush()
                current = {
                    "text": stripped,
                    "start": float(start_ms) / 1000.0,
                    "end": float(end_ms) / 1000.0,
                    "type": "word",
                }
            else:
                current["text"] = str(current["text"]) + stripped
                current["end"] = float(end_ms) / 1000.0
    flush()

    if not words:
        raise ValueError("whisper.cpp produced no timed lexical words")
    text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in transcription
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    )
    return {
        "text": text,
        "words": words,
        "language_code": data.get("result", {}).get("language", "pt")
        if isinstance(data.get("result"), dict)
        else "pt",
        "_alano_cut": {"transcription_provider": "local_whisper_cpp"},
    }


class LocalWhisperCppProvider(TranscriptProvider):
    """Offline preview transcription using the installed whisper.cpp CLI."""

    def __init__(self, language: str = "pt"):
        self.language = language

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        whisper_cli = shutil.which("whisper-cli") or str(
            Path(os.environ.get("APPDATA", "")) / "whisper-cli" / "whisper-cli.exe"
        )
        model = Path(
            os.environ.get(
                "ALANOCUT_WHISPER_MODEL",
                str(Path(os.environ.get("APPDATA", "")) / "whisper-cli" / "models" / "ggml-small.bin"),
            )
        )
        if not Path(whisper_cli).is_file():
            raise RuntimeError("local whisper-cli executable was not found")
        if not model.is_file():
            raise RuntimeError(f"local Whisper model was not found: {model}")
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required for local Whisper transcription")

        with tempfile.TemporaryDirectory(prefix="alano_cut_whisper_") as temp_dir:
            temp_root = Path(temp_dir)
            input_wav = temp_root / "preview_16k_mono.wav"
            output_base = temp_root / "preview_transcript"
            conversion = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(audio_path), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(input_wav),
                ],
                capture_output=True,
                text=True,
            )
            if conversion.returncode != 0:
                raise RuntimeError(f"local Whisper preprocessing failed: {conversion.stderr.strip()}")

            inference = subprocess.run(
                [
                    whisper_cli,
                    "-m", str(model),
                    "-f", str(input_wav),
                    "-l", self.language,
                    "-t", str(min(8, max(1, os.cpu_count() or 4))),
                    "-ojf", "-of", str(output_base), "-np", "-pp",
                ],
                capture_output=True,
                text=True,
            )
            if inference.returncode != 0:
                details = (inference.stderr or inference.stdout).strip()
                raise RuntimeError(f"local Whisper transcription failed: {details}")
            output_json = output_base.with_suffix(".json")
            if not output_json.exists():
                raise RuntimeError("local Whisper did not create its full JSON output")
            full_data = json.loads(output_json.read_text(encoding="utf-8"))
        return whisper_cpp_json_to_transcript(full_data)


# Global normative provider that can still be patched by public tests.
PROVIDER: TranscriptProvider = WhisperXTranscriptProvider()


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    """Persist JSON with fsync + atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def align_token_records(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    fuzzy_min: float = 0.90,
) -> list[dict[str, Any]]:
    """Deterministically align token records using a small edit-distance DP."""
    n = len(expected)
    m = len(actual)
    costs = [[0] * (m + 1) for _ in range(n + 1)]
    choices: list[list[tuple[str, float] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    for i in range(1, n + 1):
        costs[i][0] = i * 100
        choices[i][0] = ("delete", 0.0)
    for j in range(1, m + 1):
        costs[0][j] = j * 100
        choices[0][j] = ("insert", 0.0)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            similarity = token_similarity(expected[i - 1]["normalized"], actual[j - 1]["normalized"])
            if similarity == 1.0:
                diagonal_kind = "equal"
                diagonal_cost = 0
            elif similarity >= fuzzy_min:
                diagonal_kind = "fuzzy"
                diagonal_cost = 25
            else:
                diagonal_kind = "replace"
                diagonal_cost = 100
            candidates = [
                (costs[i - 1][j - 1] + diagonal_cost, 0, diagonal_kind, similarity),
                (costs[i - 1][j] + 100, 1, "delete", 0.0),
                (costs[i][j - 1] + 100, 2, "insert", 0.0),
            ]
            best = min(candidates, key=lambda item: (item[0], item[1]))
            costs[i][j] = best[0]
            choices[i][j] = (best[2], best[3])

    operations = []
    i, j = n, m
    while i > 0 or j > 0:
        choice = choices[i][j]
        if choice is None:
            break
        kind, similarity = choice
        if kind in {"equal", "fuzzy", "replace"}:
            operations.append({
                "op": kind,
                "expected_index": i - 1,
                "actual_index": j - 1,
                "expected": expected[i - 1],
                "actual": actual[j - 1],
                "similarity": round(similarity, 3),
            })
            i -= 1
            j -= 1
        elif kind == "delete":
            operations.append({
                "op": kind,
                "expected_index": i - 1,
                "actual_index": None,
                "expected": expected[i - 1],
                "actual": None,
                "similarity": 0.0,
            })
            i -= 1
        else:
            operations.append({
                "op": kind,
                "expected_index": None,
                "actual_index": j - 1,
                "expected": None,
                "actual": actual[j - 1],
                "similarity": 0.0,
            })
            j -= 1
    operations.reverse()
    return operations


def _source_words(data: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    words = []
    for item in data.get("words", []):
        if item.get("type") != "word":
            continue
        start = item.get("start")
        end = item.get("end")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0.0
            # Some ASR tokenizers legitimately collapse very short source
            # words to a zero-duration timestamp. They remain usable as
            # lexical expectations; only reversed intervals are structural.
            or float(end) < float(start)
        ):
            raise ValueError(f"source transcript {source_id!r} has an invalid timed word")
        words.append(dict(item))
    words.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    if not words:
        raise ValueError(f"source transcript {source_id!r} has no valid timed words")
    return words


def _build_interword_residual_checks(
    source_transcripts: dict[str, dict[str, Any]],
    map_ranges: list[dict[str, Any]],
    source_intervals: list[list[int]],
    range_results: list[dict[str, Any]],
    actual_by_range: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Resolve deferred short residuals against the mandatory preview ASR.

    Source timing alone cannot distinguish a mouth click from an ASR-missed
    short word such as ``não``.  A selected residual is therefore accepted
    only when the independently rendered/retranscribed preview preserves both
    neighboring words consecutively and contains no timed word over the mapped
    residual window.
    """
    checks: list[dict[str, Any]] = []
    hop_samples = 240  # 5 ms at the mandatory 48 kHz preview rate.

    for source_id, source_transcript in source_transcripts.items():
        metadata = source_transcript.get("_alano_cut")
        acoustic = metadata.get("acoustic_timing") if isinstance(metadata, dict) else None
        residuals = acoustic.get("nonblocking_outliers", []) if isinstance(acoustic, dict) else []
        if not isinstance(residuals, list):
            checks.append({
                "source": source_id,
                "residual_index": None,
                "selection_status": "invalid_source_evidence",
                "blocking_flags": ["selected_interword_residual_unresolved"],
                "status": "review",
            })
            continue

        for residual_index, residual in enumerate(residuals):
            if not isinstance(residual, dict) or residual.get("type") != "nonblocking_interword_residual":
                continue
            component = residual.get("component")
            previous = residual.get("previous_word")
            following = residual.get("following_word")
            base = {
                "source": source_id,
                "residual_index": residual_index,
                "source_component": component,
                "previous_word": previous,
                "following_word": following,
            }
            try:
                component_start = int(round(float(component["start"]) * 48000))
                component_end = int(round(float(component["end"]) * 48000))
                previous_index = int(previous["index"])
                following_index = int(following["index"])
                if component_end <= component_start or previous_index >= following_index:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                checks.append(base | {
                    "selection_status": "invalid_source_evidence",
                    "blocking_flags": ["selected_interword_residual_unresolved"],
                    "status": "review",
                })
                continue

            selected_ranges = [
                range_index
                for range_index, (map_range, source_interval) in enumerate(
                    zip(map_ranges, source_intervals)
                )
                if map_range.get("source") == source_id
                and component_end > source_interval[0]
                and component_start < source_interval[1]
            ]
            if not selected_ranges:
                checks.append(base | {
                    "selection_status": "outside_selection",
                    "blocking_flags": [],
                    "status": "pass",
                })
                continue

            flags: list[str] = []
            if len(selected_ranges) != 1:
                flags.append("selected_interword_residual_unresolved")
                range_index = selected_ranges[0]
            else:
                range_index = selected_ranges[0]
            source_interval = source_intervals[range_index]
            output_interval = map_ranges[range_index]["output_cumulative_sample_interval"]
            if component_start < source_interval[0] or component_end > source_interval[1]:
                flags.append("selected_interword_residual_unresolved")

            mapped_start = output_interval[0] + component_start - source_interval[0]
            mapped_end = output_interval[0] + component_end - source_interval[0]
            alignment = range_results[range_index]["alignment"]
            previous_ops = [
                operation
                for operation in alignment
                if operation["expected"] is not None
                and operation["expected"].get("source_word_index") == previous_index
            ]
            following_ops = [
                operation
                for operation in alignment
                if operation["expected"] is not None
                and operation["expected"].get("source_word_index") == following_index
            ]

            def faithful(operations: list[dict[str, Any]]) -> bool:
                return bool(operations) and all(
                    operation["op"] in {"equal", "fuzzy"}
                    and float(operation.get("similarity") or 0.0) >= 0.90
                    and isinstance(operation.get("actual_index"), int)
                    for operation in operations
                )

            neighbors_faithful = faithful(previous_ops) and faithful(following_ops)
            previous_actual = [operation["actual_index"] for operation in previous_ops if isinstance(operation.get("actual_index"), int)]
            following_actual = [operation["actual_index"] for operation in following_ops if isinstance(operation.get("actual_index"), int)]
            neighbors_consecutive = bool(
                neighbors_faithful
                and previous_actual
                and following_actual
                and max(previous_actual) + 1 == min(following_actual)
            )
            overlapping_words: list[dict[str, Any]] = []
            seen_preview_words: set[int] = set()
            for record in actual_by_range[range_index]:
                preview_word_index = record["preview_word_index"]
                if preview_word_index in seen_preview_words:
                    continue
                if (
                    record["end_sample"] > mapped_start + hop_samples
                    and record["start_sample"] < mapped_end - hop_samples
                ):
                    seen_preview_words.add(preview_word_index)
                    overlapping_words.append({
                        "preview_word_index": preview_word_index,
                        "text": record.get("text"),
                        "start_sample": record["start_sample"],
                        "end_sample": record["end_sample"],
                    })
            no_preview_word_overlap = not overlapping_words
            if not neighbors_faithful or not neighbors_consecutive or not no_preview_word_overlap:
                flags.append("selected_interword_residual_unresolved")
            flags = list(dict.fromkeys(flags))
            checks.append(base | {
                "selection_status": "selected",
                "range_index": range_index,
                "mapped_preview_sample_interval": [mapped_start, mapped_end],
                "checks": {
                    "neighbors_faithful": neighbors_faithful,
                    "neighbors_consecutive": neighbors_consecutive,
                    "no_preview_word_overlap": no_preview_word_overlap,
                },
                "overlapping_preview_words": overlapping_words,
                "blocking_flags": flags,
                "status": "review" if flags else "pass",
            })
    return checks


def build_join_analysis(
    transcript_data: dict[str, Any],
    edl_data: dict[str, Any],
    timeline_map: dict[str, Any],
    source_transcripts: dict[str, dict[str, Any]],
    cue_terms: list[str],
) -> dict[str, Any]:
    """Build range-aware lexical evidence and an exact report for every join."""
    edl_ranges = edl_data.get("ranges")
    map_ranges = timeline_map.get("ranges")
    if not isinstance(edl_ranges, list) or not edl_ranges:
        raise ValueError("EDL ranges must be a non-empty list")
    if not isinstance(map_ranges, list) or len(map_ranges) != len(edl_ranges):
        raise ValueError("timeline map range count does not match EDL")
    output_format = timeline_map.get("output_format")
    if not isinstance(output_format, dict) or output_format.get("sample_rate") != 48000:
        raise ValueError("timeline map must declare 48 kHz output")
    fps_value = output_format.get("sequence_fps")
    try:
        try:
            from helpers.timing import frame_to_sample, parse_fps_fraction
        except ModuleNotFoundError as exc:
            if exc.name != "helpers":
                raise
            from timing import frame_to_sample, parse_fps_fraction
        fps = parse_fps_fraction(fps_value)
    except Exception as exc:
        raise ValueError(f"timeline map has invalid sequence_fps: {exc}") from exc

    words_by_source = {
        source_id: _source_words(data, source_id)
        for source_id, data in source_transcripts.items()
    }
    range_results: list[dict[str, Any]] = []
    expected_by_range: list[list[dict[str, Any]]] = []
    source_intervals: list[list[int]] = []

    for position, (edl_range, map_range) in enumerate(zip(edl_ranges, map_ranges)):
        if map_range.get("range_index") != position:
            raise ValueError(f"timeline map range {position} has invalid range_index")
        source_id = edl_range.get("source")
        if map_range.get("source") != source_id or source_id not in words_by_source:
            raise ValueError(f"timeline map range {position} source is invalid")
        anchors = map_range.get("lexical_anchors")
        if not isinstance(anchors, dict):
            raise ValueError(f"timeline map range {position} is missing lexical_anchors")
        first_anchor = anchors.get("first")
        last_anchor = anchors.get("last")
        if not isinstance(first_anchor, dict) or not isinstance(last_anchor, dict):
            raise ValueError(f"timeline map range {position} has invalid lexical anchors")
        first_index = first_anchor.get("word_index")
        last_index = last_anchor.get("word_index")
        source_words = words_by_source[source_id]
        if (
            not isinstance(first_index, int)
            or isinstance(first_index, bool)
            or not isinstance(last_index, int)
            or isinstance(last_index, bool)
            or first_index < 0
            or last_index < first_index
            or last_index >= len(source_words)
        ):
            raise ValueError(f"timeline map range {position} anchor indices are invalid")
        for anchor, expected_word, label in (
            (first_anchor, source_words[first_index], "first"),
            (last_anchor, source_words[last_index], "last"),
        ):
            if normalize_text(str(anchor.get("text") or "")) != normalize_text(str(expected_word.get("text") or "")):
                raise ValueError(f"timeline map range {position} {label} anchor identity is stale")
            if abs(float(anchor.get("start", -1.0)) - float(expected_word["start"])) > 1e-3:
                raise ValueError(f"timeline map range {position} {label} anchor timestamp is stale")

        source_interval = map_range.get("source_sample_interval")
        output_interval = map_range.get("output_cumulative_sample_interval")
        source_frames = map_range.get("source_frames")
        if (
            not isinstance(output_interval, list)
            or len(output_interval) != 2
            or not isinstance(source_frames, list)
            or len(source_frames) != 2
        ):
            raise ValueError(f"timeline map range {position} has invalid intervals")
        if not isinstance(source_interval, list) or len(source_interval) != 2:
            source_interval = [
                frame_to_sample(source_frames[0], fps),
                frame_to_sample(source_frames[1], fps),
            ]
        source_interval = [int(source_interval[0]), int(source_interval[1])]
        source_intervals.append(source_interval)

        expected_records = []
        for word_index in range(first_index, last_index + 1):
            source_word = source_words[word_index]
            projected_start = output_interval[0] + int(round(float(source_word["start"]) * 48000)) - source_interval[0]
            projected_end = output_interval[0] + int(round(float(source_word["end"]) * 48000)) - source_interval[0]
            for token_index, normalized_token in enumerate(tokenize(str(source_word.get("text") or ""))):
                expected_records.append({
                    "text": source_word.get("text"),
                    "normalized": normalized_token,
                    "source_word_index": word_index,
                    "token_index": token_index,
                    "source_start": float(source_word["start"]),
                    "source_end": float(source_word["end"]),
                    "timeline_start_sample": projected_start,
                    "timeline_end_sample": projected_end,
                })
        expected_by_range.append(expected_records)
        range_results.append({
            "range_index": position,
            "source": source_id,
            "beat_id": map_range.get("beat_id"),
            "expected_words": expected_records,
            "actual_words": [],
            "alignment": [],
            "phrase_similarity": 0.0,
            "token_recall": 0.0,
            "status": "review",
            "blocking_flags": [],
        })

    untimed_words = []
    invalid_words = []
    out_of_bounds_words = []
    actual_by_range: list[list[dict[str, Any]]] = [[] for _ in map_ranges]
    spanning_by_join: list[list[dict[str, Any]]] = [[] for _ in range(max(0, len(map_ranges) - 1))]
    preview_words = [item for item in transcript_data.get("words", []) if item.get("type") == "word"]
    for preview_word_index, item in enumerate(preview_words):
        normalized_tokens = tokenize(str(item.get("text") or ""))
        start = item.get("start")
        end = item.get("end")
        if start is None or end is None:
            untimed_words.append({"word_index": preview_word_index, "text": item.get("text")})
            continue
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0.0
            or float(end) <= float(start)
        ):
            invalid_words.append({"word_index": preview_word_index, "text": item.get("text"), "start": start, "end": end})
            continue
        start_sample = int(round(float(start) * 48000))
        end_sample = int(round(float(end) * 48000))
        midpoint = (start_sample + end_sample) // 2
        assigned_range = None
        for range_index, map_range in enumerate(map_ranges):
            interval = map_range["output_cumulative_sample_interval"]
            if interval[0] <= midpoint < interval[1] or (
                range_index == len(map_ranges) - 1 and midpoint == interval[1]
            ):
                assigned_range = range_index
                break
        if assigned_range is None:
            out_of_bounds_words.append({"word_index": preview_word_index, "text": item.get("text"), "start": start, "end": end})
            continue
        for token_index, normalized_token in enumerate(normalized_tokens):
            actual_by_range[assigned_range].append({
                "text": item.get("text"),
                "normalized": normalized_token,
                "preview_word_index": preview_word_index,
                "token_index": token_index,
                "start": float(start),
                "end": float(end),
                "start_sample": start_sample,
                "end_sample": end_sample,
                "assigned_range": assigned_range,
            })

        tolerance_samples = frame_to_sample(2, fps)
        for join_index in range(len(map_ranges) - 1):
            join_sample = map_ranges[join_index]["output_cumulative_sample_interval"][1]
            if start_sample < join_sample - tolerance_samples and end_sample > join_sample + tolerance_samples:
                spanning_by_join[join_index].append({
                    "word_index": preview_word_index,
                    "text": item.get("text"),
                    "start": float(start),
                    "end": float(end),
                })

    for range_index, result in enumerate(range_results):
        expected_records = expected_by_range[range_index]
        actual_records = actual_by_range[range_index]
        alignment = align_token_records(expected_records, actual_records)
        matched = sum(1 for operation in alignment if operation["op"] in {"equal", "fuzzy"})
        phrase_similarity = token_ratio(
            " ".join(record["normalized"] for record in expected_records),
            " ".join(record["normalized"] for record in actual_records),
        )
        recall = matched / len(expected_records) if expected_records else 1.0
        flags = []
        if phrase_similarity < 0.85:
            flags.append("range_phrase_mismatch")
        if recall < 0.90:
            flags.append("range_token_recall_low")
        result.update({
            "actual_words": actual_records,
            "alignment": alignment,
            "phrase_similarity": round(phrase_similarity, 3),
            "token_recall": round(recall, 3),
            "status": "review" if flags else "pass",
            "blocking_flags": flags,
        })

    residual_checks = _build_interword_residual_checks(
        source_transcripts,
        map_ranges,
        source_intervals,
        range_results,
        actual_by_range,
    )

    join_results = []
    for join_index in range(len(map_ranges) - 1):
        left_result = range_results[join_index]
        right_result = range_results[join_index + 1]
        left_expected = expected_by_range[join_index]
        right_expected = expected_by_range[join_index + 1]
        left_actual = actual_by_range[join_index]
        right_actual = actual_by_range[join_index + 1]
        left_ops = left_result["alignment"]
        right_ops = right_result["alignment"]
        expected_left_suffix = left_expected[-3:]
        expected_right_prefix = right_expected[:3]
        flags = []

        left_last_index = len(left_expected) - 1
        left_last_op = next((op for op in left_ops if op["expected_index"] == left_last_index), None)
        left_suffix_ok = bool(
            left_last_op
            and left_last_op["op"] in {"equal", "fuzzy"}
            and left_last_op["similarity"] >= 0.90
        )
        if not left_suffix_ok:
            flags.append("left_suffix_mismatch")

        first_op = next((op for op in right_ops if op["expected_index"] == 0), None)
        if first_op is None or first_op["op"] == "delete":
            flags.append("missing_right_first_word")
            right_first_ok = False
        elif first_op["op"] not in {"equal", "fuzzy"} or first_op["similarity"] < 0.90:
            flags.append("deformed_right_first_word")
            right_first_ok = False
        else:
            right_first_ok = True

        prefix_expected_indices = set(range(len(expected_right_prefix)))
        prefix_positions = [
            position
            for position, operation in enumerate(right_ops)
            if operation["expected_index"] in prefix_expected_indices
        ]
        prefix_completion_position = max(prefix_positions, default=-1)
        unexpected_prefix_ops = [
            operation
            for position, operation in enumerate(right_ops)
            if operation["op"] == "insert" and position <= prefix_completion_position
        ]
        unexpected_prefix = [operation["actual"] for operation in unexpected_prefix_ops]
        if unexpected_prefix:
            flags.append("unexpected_right_prefix" if len(unexpected_prefix) <= 2 else "unexpected_right_prefix_span")

        matched_prefix = [
            operation["actual"]
            for operation in right_ops
            if operation["expected_index"] in prefix_expected_indices and operation["actual"] is not None
        ]
        prefix_similarity = token_ratio(
            " ".join(record["normalized"] for record in expected_right_prefix),
            " ".join(record["normalized"] for record in matched_prefix),
        )
        # Preview ASR can legitimately omit one extremely short article at a
        # clean join (for example ``"para a atualização"`` ->
        # ``"para atualização"``).  Keep this exception deliberately narrow:
        # a content-word omission, replacement, or reordered article must not
        # turn into a false pass merely because the whole phrase is similar.
        prefix_ops = [
            operation
            for operation in right_ops
            if operation["expected_index"] in prefix_expected_indices
        ]
        prefix_deletions = [operation for operation in prefix_ops if operation["op"] == "delete"]
        early_insertions = [
            operation
            for operation in right_ops
            if operation["op"] == "insert"
            and operation["actual_index"] is not None
            and operation["actual_index"] < len(expected_right_prefix)
        ]
        omitted_article = prefix_deletions[0] if len(prefix_deletions) == 1 else None
        reinserted_omitted_article = bool(
            omitted_article
            and any(
                operation["op"] == "insert"
                and operation["actual"] is not None
                and operation["actual"]["normalized"]
                == omitted_article["expected"]["normalized"]
                for operation in right_ops
            )
        )
        tolerated_article_omission = bool(
            omitted_article
            and omitted_article["expected_index"] not in {None, 0}
            and omitted_article["expected"]["normalized"] in {"a", "o", "as", "os"}
            and all(
                operation["op"] in {"equal", "fuzzy", "delete"}
                for operation in prefix_ops
            )
            and all(
                operation["op"] in {"equal", "fuzzy"}
                for operation in prefix_ops
                if operation is not omitted_article
            )
            and not early_insertions
            and not reinserted_omitted_article
            and "range_token_recall_low" not in right_result["blocking_flags"]
        )
        expected_prefix_tokens = [
            record["normalized"] for record in expected_right_prefix
        ]
        aligned_prefix_tokens = [
            operation["actual"]["normalized"]
            for operation in prefix_ops
            if operation["actual"] is not None
        ]
        prefix_reordered = bool(
            len(aligned_prefix_tokens) == len(expected_prefix_tokens)
            and Counter(aligned_prefix_tokens) == Counter(expected_prefix_tokens)
            and aligned_prefix_tokens != expected_prefix_tokens
        )
        full_prefix_coverage = bool(
            len(matched_prefix) == len(expected_right_prefix)
            and not early_insertions
            and not prefix_reordered
        )
        right_prefix_ok = bool(
            prefix_similarity >= 0.85
            and (full_prefix_coverage or tolerated_article_omission)
        )
        if not right_prefix_ok:
            flags.append("right_prefix_phrase_mismatch")

        left_insertions = [
            operation["actual"] for operation in left_ops[-6:]
            if operation["op"] == "insert" and operation["actual"] is not None
        ]
        right_prefix_tokens = [record["normalized"] for record in expected_right_prefix]
        right_content_before = [
            record for record in left_insertions
            if any(token_similarity(record["normalized"], token) >= 0.90 for token in right_prefix_tokens)
        ]
        if right_content_before:
            flags.append("right_content_before_join")

        left_suffix_tokens = [record["normalized"] for record in expected_left_suffix]
        left_content_after = [
            record for record in unexpected_prefix
            if any(token_similarity(record["normalized"], token) >= 0.90 for token in left_suffix_tokens)
        ]
        if left_content_after:
            flags.append("left_content_after_join")

        local_actual = left_actual[-3:] + right_actual[: max(5, len(expected_right_prefix) + 2)]
        local_cue_hits = cue_hits(
            " ".join(record["normalized"] for record in local_actual),
            cue_terms,
        )
        if local_cue_hits:
            flags.append("direction_cue")
        spanning_words = spanning_by_join[join_index]
        if spanning_words:
            flags.append("preview_word_spans_join")
        if untimed_words or invalid_words:
            flags.append("untimed_preview_words")

        flags = list(dict.fromkeys(flags))
        join_sample = map_ranges[join_index]["output_cumulative_sample_interval"][1]
        join_results.append({
            "join_index": join_index,
            "left_range_index": join_index,
            "right_range_index": join_index + 1,
            "timeline_sample": join_sample,
            "timeline_seconds": round(join_sample / 48000.0, 6),
            "expected": {
                "left_suffix": expected_left_suffix,
                "right_prefix": expected_right_prefix,
            },
            "actual": {
                "left_suffix": left_actual[-3:],
                "right_prefix_window": right_actual[: max(5, len(expected_right_prefix) + 2)],
                "spanning_words": spanning_words,
            },
            "unexpected_prefix": unexpected_prefix,
            "crossed_anchor_evidence": {
                "right_before": right_content_before,
                "left_after": left_content_after,
            },
            "right_prefix_similarity": round(prefix_similarity, 3),
            "right_prefix_reordered": prefix_reordered,
            "tolerated_prefix_omission": (
                omitted_article["expected"] if tolerated_article_omission else None
            ),
            "checks": {
                "left_suffix_ok": left_suffix_ok,
                "right_first_word_ok": right_first_ok,
                "right_prefix_phrase_ok": right_prefix_ok,
                "no_unexpected_prefix": not unexpected_prefix,
                "no_crossed_content": not right_content_before and not left_content_after,
                "cue_free": not local_cue_hits,
            },
            "cue_hits": local_cue_hits,
            "blocking_flags": flags,
            "status": "review" if flags else "pass",
        })

    expected_text = " ".join(
        record["normalized"] for range_records in expected_by_range for record in range_records
    )
    return {
        "ranges": range_results,
        "joins": join_results,
        "interword_residual_checks": residual_checks,
        "expected_text": expected_text,
        "timing_validation": {
            "untimed_words": untimed_words,
            "invalid_words": invalid_words,
            "out_of_bounds_words": out_of_bounds_words,
        },
    }


def build_report(
    transcript_data: dict[str, Any],
    transcript_path: Path | None,
    expected: str | None,
    cue_terms: list[str],
    wav_path: Path | None = None,
    *,
    edl_data: dict[str, Any] | None = None,
    timeline_map: dict[str, Any] | None = None,
    source_transcripts: dict[str, dict[str, Any]] | None = None,
    edl_hash: str = "",
    timeline_map_hash: str = "",
    source_transcript_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    text = transcript_text(transcript_data)
    normalized = normalize_text(text)
    tokens = tokenize(text)
    sentences = split_sentences(text)

    repeats = adjacent_repeats(sentences)
    ngrams = repeated_ngrams(tokens)
    cues = cue_hits(normalized, cue_terms)
    join_analysis = None
    if edl_data is not None or timeline_map is not None or source_transcripts is not None:
        if edl_data is None or timeline_map is None or source_transcripts is None:
            raise ValueError("EDL, timeline map, and source transcripts must be supplied together")
        join_analysis = build_join_analysis(
            transcript_data,
            edl_data,
            timeline_map,
            source_transcripts,
            cue_terms,
        )

    operational_expected = join_analysis["expected_text"] if join_analysis else expected
    expected_diff = compare_expected(text, operational_expected)
    external_expected_diff = (
        compare_expected(text, expected)
        if join_analysis and expected
        else None
    )

    raw_words = transcript_data.get("words", [])
    if not isinstance(raw_words, list):
        raise ValueError("preview transcript words must be a list")
    words_list = [w for w in raw_words if isinstance(w, dict) and w.get("type") == "word"]
    word_count = len(words_list) if "words" in transcript_data else len(tokens)
    timed_word_count = sum(
        1
        for word in words_list
        if word.get("start") is not None and word.get("end") is not None
    )
    timing_coverage = (timed_word_count / word_count) if word_count > 0 else 0.0

    words_evidence = [
        {
            "text": w.get("text"),
            "type": w.get("type"),
            "start": w.get("start"),
            "end": w.get("end")
        }
        for w in raw_words
        if isinstance(w, dict)
    ]

    blocking_flags: list[str] = []
    if repeats or ngrams:
        blocking_flags.append("possible_duplicate_content")
    if cues:
        blocking_flags.append("possible_leftover_direction_or_audio_event")
    if timed_word_count == 0:
        blocking_flags.append("missing_timed_words")
    elif timed_word_count != word_count:
        blocking_flags.append("incomplete_word_timestamps")

    if expected_diff:
        if expected_diff["similarity"] < 0.85:
            blocking_flags.append("expected_text_mismatch")
        if expected_diff["recall"] < 0.90:
            blocking_flags.append("expected_token_recall_low")
        if expected_diff["missing_spans"]:
            blocking_flags.append("missing_span_detected")

    range_results = join_analysis["ranges"] if join_analysis else []
    join_results = join_analysis["joins"] if join_analysis else []
    residual_checks = join_analysis["interword_residual_checks"] if join_analysis else []
    timing_validation = join_analysis["timing_validation"] if join_analysis else {
        "untimed_words": [],
        "invalid_words": [],
        "out_of_bounds_words": [],
    }
    if join_analysis:
        word_text = " ".join(str(word.get("text") or "") for word in words_list)
        words_text_similarity = token_ratio(text, word_text)
        if words_text_similarity < 0.85:
            blocking_flags.append("transcript_text_words_mismatch")
        for result in range_results:
            blocking_flags.extend(result.get("blocking_flags", []))
        for result in join_results:
            blocking_flags.extend(result.get("blocking_flags", []))
        for result in residual_checks:
            blocking_flags.extend(result.get("blocking_flags", []))
        if timing_validation["invalid_words"]:
            blocking_flags.append("invalid_preview_word_timestamps")
        if timing_validation["out_of_bounds_words"]:
            blocking_flags.append("preview_words_out_of_bounds")
    else:
        words_text_similarity = None

    blocking_flags = list(dict.fromkeys(blocking_flags))

    # Compute hashes for binding
    wav_hash = ""
    if wav_path and wav_path.exists():
        wav_hash = compute_sha256(wav_path)

    if transcript_path and transcript_path.exists():
        transcript_hash = compute_sha256(transcript_path)
    else:
        # Compute hash of serialized JSON dict
        serialized = json.dumps(transcript_data, sort_keys=True, ensure_ascii=False)
        transcript_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    status = "review" if blocking_flags else "pass"
    report = {
        "schema_version": 2,
        "mode": "range_aware" if join_analysis else "legacy_global",
        "status": status,
        "transcript": str(transcript_path) if transcript_path else "generated",
        "edl_hash": edl_hash,
        "timeline_map_hash": timeline_map_hash,
        "preview_wav_hash": wav_hash,
        "transcript_hash": transcript_hash,
        "source_transcript_hashes": source_transcript_hashes or {},
        "settings": {
            "join_context_words": 3,
            "anchor_similarity_min": 0.90,
            "phrase_similarity_min": 0.85,
            "expected_token_recall_min": 0.90,
            "cross_tolerance_frames": 2,
            "interword_residual_tolerance_ms": 5,
        },
        "summary": {
            "text_chars": len(text),
            "token_count": len(tokens),
            "word_count": word_count,
            "timed_word_count": timed_word_count,
            "timing_coverage": round(timing_coverage, 4),
            "sentence_count": len(sentences),
            "adjacent_repeat_count": len(repeats),
            "repeated_ngram_count": len(ngrams),
            "cue_hit_count": len(cues),
            "expected_similarity": expected_diff["similarity"] if expected_diff else None,
            "expected_recall": expected_diff["recall"] if expected_diff else None,
            "words_text_similarity": round(words_text_similarity, 3) if words_text_similarity is not None else None,
            "range_count": len(range_results),
            "range_pass_count": sum(result.get("status") == "pass" for result in range_results),
            "range_review_count": sum(result.get("status") == "review" for result in range_results),
            "join_count": len(join_results),
            "join_pass_count": sum(result.get("status") == "pass" for result in join_results),
            "join_review_count": sum(result.get("status") == "review" for result in join_results),
            "interword_residual_count": len(residual_checks),
            "interword_residual_pass_count": sum(result.get("status") == "pass" for result in residual_checks),
            "interword_residual_review_count": sum(result.get("status") == "review" for result in residual_checks),
            "status": status,
            "blocking_flags": blocking_flags,
        },
        "words_evidence": words_evidence,
        "text": text,
        "adjacent_repeats": repeats,
        "repeated_ngrams": ngrams,
        "cue_hits": cues,
        "expected_diff": expected_diff,
        "external_expected_diff": external_expected_diff,
        "timing_validation": timing_validation,
        "ranges": range_results,
        "joins": join_results,
        "interword_residual_checks": residual_checks,
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe and QC every lexical join in preview.wav")
    ap.add_argument(
        "input_file",
        type=Path,
        nargs="?",
        default=None,
        help="Path to preview WAV audio file or persisted transcript JSON. If WAV, it will be transcribed."
    )
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output QC JSON")
    ap.add_argument("--audio", type=Path, default=None, help="Path to preview WAV audio file")
    ap.add_argument("--edl", type=Path, default=None, help="Refined EDL used to render the preview")
    ap.add_argument(
        "--transcripts",
        "--source-transcripts",
        dest="transcripts",
        type=Path,
        default=None,
        help="Directory containing source transcript JSON files",
    )
    ap.add_argument("--timeline-map", type=Path, default=None, help="preview_timeline.json path")
    ap.add_argument(
        "--transcript-output",
        type=Path,
        default=None,
        help="Persisted preview transcript (default: <edit>/transcripts/preview.json)",
    )
    ap.add_argument("--expected-text", type=Path, default=None, help="Optional expected script/plain text")
    ap.add_argument("--expected-transcript", type=Path, default=None, help="Optional expected transcript JSON")
    ap.add_argument(
        "--cue-term",
        action="append",
        default=[],
        help="Additional cue/audio-event term to flag. Can be passed multiple times.",
    )
    ap.add_argument(
        "--mock-transcript",
        type=Path,
        default=None,
        help="Path to mock transcript JSON to use instead of running the selected provider."
    )
    ap.add_argument(
        "--provider",
        choices=("whisperx", "elevenlabs", "local-whisper", "auto"),
        default="whisperx",
        help=(
            "Preview transcription backend. WhisperX is normative. 'auto' is a "
            "deprecated alias for WhisperX and never falls back to unaligned ASR."
        ),
    )
    args = ap.parse_args()

    input_path = args.input_file.resolve() if args.input_file else None
    wav_path = args.audio.resolve() if args.audio else None

    transcript_path = None
    transcript_data = None

    if input_path:
        if input_path.suffix.lower() == ".wav":
            wav_path = input_path
        elif input_path.suffix.lower() == ".json":
            transcript_path = input_path
            if not transcript_path.exists():
                print(f"Error: transcript not found: {transcript_path}", file=sys.stderr)
                sys.exit(1)
            transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
        else:
            print("Error: input must be a .wav or .json file", file=sys.stderr)
            sys.exit(1)

    explicit_context = any((args.edl, args.transcripts, args.timeline_map))
    if args.edl:
        edl_path = args.edl.resolve()
        edit_dir = edl_path.parent
    elif wav_path:
        edit_dir = wav_path.parent
        candidate = edit_dir / "edl.json"
        edl_path = candidate if candidate.exists() else None
    elif transcript_path:
        edit_dir = transcript_path.parent.parent if transcript_path.parent.name == "transcripts" else transcript_path.parent
        candidate = edit_dir / "edl.json"
        edl_path = candidate if candidate.exists() else None
    else:
        edit_dir = Path("edit").resolve()
        candidate = edit_dir / "edl.json"
        edl_path = candidate if candidate.exists() else None

    if wav_path is None:
        wav_path = edit_dir / "preview.wav"
    if not wav_path.exists():
        print(f"Error: WAV audio file not found at: {wav_path}", file=sys.stderr)
        sys.exit(1)

    context_requested = explicit_context or edl_path is not None
    if context_requested and (edl_path is None or not edl_path.exists()):
        print(f"Error: refined EDL not found at: {edl_path or edit_dir / 'edl.json'}", file=sys.stderr)
        sys.exit(1)

    if transcript_data is None:
        if args.mock_transcript:
            mock_path = args.mock_transcript.resolve()
            if not mock_path.exists():
                print(f"Error: mock transcript not found: {mock_path}", file=sys.stderr)
                sys.exit(1)
            transcript_data = json.loads(mock_path.read_text(encoding="utf-8"))
        else:
            try:
                if args.provider == "local-whisper":
                    transcript_data = LocalWhisperCppProvider().transcribe(wav_path)
                elif args.provider == "elevenlabs":
                    transcript_data = ElevenLabsScribeProvider().transcribe(wav_path)
                else:
                    transcript_data = PROVIDER.transcribe(wav_path)
            except Exception as e:
                print(f"Error: failed during transcription: {e}", file=sys.stderr)
                sys.exit(1)

        if not isinstance(transcript_data, dict):
            print("Error: transcription provider returned a non-object response", file=sys.stderr)
            sys.exit(1)
        transcript_data = json.loads(json.dumps(transcript_data))
        binding = transcript_data.get("_alano_cut")
        if not isinstance(binding, dict):
            binding = {}
            transcript_data["_alano_cut"] = binding
        binding["preview_wav_sha256"] = compute_sha256(wav_path)
        transcript_path = (
            args.transcript_output.resolve()
            if args.transcript_output
            else edit_dir / "transcripts" / "preview.json"
        )
        write_atomic_json(transcript_path, transcript_data)
        print(f"wrote preview transcript -> {transcript_path}")

    if not isinstance(transcript_data, dict):
        print("Error: transcript JSON must be an object", file=sys.stderr)
        sys.exit(1)

    edl_data = None
    timeline_map_data = None
    source_transcripts = None
    edl_hash = ""
    timeline_map_hash = ""
    source_transcript_hashes: dict[str, str] = {}
    if context_requested:
        assert edl_path is not None
        map_path = args.timeline_map.resolve() if args.timeline_map else edit_dir / "preview_timeline.json"
        transcripts_dir = args.transcripts.resolve() if args.transcripts else edit_dir / "transcripts"
        try:
            edl_data = json.loads(edl_path.read_text(encoding="utf-8"))
            timeline_map_data = json.loads(map_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error: failed to load EDL/timeline map: {exc}", file=sys.stderr)
            sys.exit(1)
        edl_hash = compute_sha256(edl_path)
        timeline_map_hash = compute_sha256(map_path)
        if timeline_map_data.get("edl_hash") != edl_hash:
            print("Error: timeline map is stale for the refined EDL", file=sys.stderr)
            sys.exit(1)
        source_transcripts = {}
        sources = edl_data.get("sources")
        if not isinstance(sources, dict) or not sources:
            print("Error: EDL sources must be a non-empty object", file=sys.stderr)
            sys.exit(1)
        for source_id in sources:
            source_path = transcripts_dir / f"{source_id}.json"
            try:
                source_transcripts[source_id] = json.loads(source_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Error: failed to load source transcript {source_id!r}: {exc}", file=sys.stderr)
                sys.exit(1)
            source_transcript_hashes[source_id] = compute_sha256(source_path)

        binding = transcript_data.get("_alano_cut")
        if not isinstance(binding, dict) or binding.get("preview_wav_sha256") != compute_sha256(wav_path):
            print("Error: preview transcript is not bound to the current preview WAV", file=sys.stderr)
            sys.exit(1)

    expected = load_expected(args)
    try:
        report = build_report(
            transcript_data,
            transcript_path,
            expected,
            DEFAULT_CUE_TERMS + args.cue_term,
            wav_path=wav_path,
            edl_data=edl_data,
            timeline_map=timeline_map_data,
            source_transcripts=source_transcripts,
            edl_hash=edl_hash,
            timeline_map_hash=timeline_map_hash,
            source_transcript_hashes=source_transcript_hashes,
        )
    except Exception as exc:
        print(f"Error: preview transcript QC failed structurally: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output.resolve() if args.output else edit_dir / "preview_transcript_qc.json"
    write_atomic_json(output_path, report)
    print(f"wrote preview transcript QC -> {output_path}")

    s = report["summary"]
    print(
        f"status={s['status']} repeats={s['adjacent_repeat_count'] + s['repeated_ngram_count']} "
        f"cue_hits={s['cue_hit_count']} joins_review={s['join_review_count']}"
    )
    if s["status"] == "review":
        sys.exit(2)


if __name__ == "__main__":
    main()
