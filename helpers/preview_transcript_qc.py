"""QC a transcript from the rendered preview/final timeline audio.

This helper catches content problems that boundary checks cannot see:
duplicated lines, leftover direction words like "corta", obvious audio-event
artifacts, and domain-specific semantic mismatches.

It accepts ElevenLabs Scribe JSON and optionally compares it to an expected
text file or expected transcript JSON.

Usage:
    python helpers/preview_transcript_qc.py raw_video/edit/transcripts/preview.json -o raw_video/edit/preview_transcript_qc.json
    python helpers/preview_transcript_qc.py raw_video/edit/transcripts/preview.json --expected-text raw_video/edit/EXPECTED_SCRIPT.txt
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
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

BUSINESS_FIELDS = [
    "razao social",
    "cnpj",
    "socio administrador",
    "faturamento",
]

PERSONAL_FIELDS = [
    "cpf",
    "data de nascimento",
    "dados pessoais",
    "nome completo",
]


def strip_accents(text: str) -> str:
    table = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüçÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇ",
        "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC",
    )
    return text.translate(table)


def normalize_text(text: str) -> str:
    text = strip_accents(text).lower()
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


def window_contains(tokens: list[str], start_phrase: list[str], targets: list[list[str]], window: int = 14) -> bool:
    plen = len(start_phrase)
    for i in range(0, len(tokens) - plen + 1):
        if tokens[i : i + plen] != start_phrase:
            continue
        end = min(len(tokens), i + plen + window)
        window_tokens = tokens[i + plen : end]
        for target in targets:
            tlen = len(target)
            for j in range(0, len(window_tokens) - tlen + 1):
                if window_tokens[j : j + tlen] == target:
                    return True
    return False


def semantic_warnings(tokens: list[str]) -> list[dict[str, str]]:
    warnings = []
    business_targets = [tokenize(term) for term in BUSINESS_FIELDS]
    personal_targets = [tokenize(term) for term in PERSONAL_FIELDS]

    if window_contains(tokens, ["pessoa", "fisica"], business_targets):
        warnings.append(
            {
                "code": "pf_with_business_fields",
                "message": "Pessoa fisica appears near business-only fields such as razao social/CNPJ/faturamento.",
            }
        )

    if window_contains(tokens, ["pessoa", "juridica"], personal_targets):
        warnings.append(
            {
                "code": "pj_with_personal_fields",
                "message": "Pessoa juridica appears near personal fields such as CPF/dados pessoais.",
            }
        )

    return warnings


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
        # Local import to avoid circular dependency or import errors when not needed
        from helpers.transcribe import call_scribe, load_api_key
        api_key = load_api_key()
        return call_scribe(audio_path, api_key)


class MockTranscriptProvider(TranscriptProvider):
    def __init__(self, response_data: dict[str, Any]):
        self.response_data = response_data

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        return self.response_data


# Global provider that can be dynamically set/patched in tests
PROVIDER: TranscriptProvider = ElevenLabsScribeProvider()


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    transcript_data: dict[str, Any],
    transcript_path: Path | None,
    expected: str | None,
    cue_terms: list[str],
    wav_path: Path | None = None,
) -> dict[str, Any]:
    text = transcript_text(transcript_data)
    normalized = normalize_text(text)
    tokens = tokenize(text)
    sentences = split_sentences(text)

    repeats = adjacent_repeats(sentences)
    ngrams = repeated_ngrams(tokens)
    cues = cue_hits(normalized, cue_terms)
    semantic = semantic_warnings(tokens)
    expected_diff = compare_expected(text, expected)

    blocking_flags = []
    if repeats or ngrams:
        blocking_flags.append("possible_duplicate_content")
    if cues:
        blocking_flags.append("possible_leftover_direction_or_audio_event")
    if semantic:
        blocking_flags.append("semantic_review_needed")

    if expected_diff:
        if expected_diff["similarity"] < 0.85:
            blocking_flags.append("expected_text_mismatch")
        if expected_diff["recall"] < 0.90:
            blocking_flags.append("expected_token_recall_low")
        if expected_diff["missing_spans"]:
            blocking_flags.append("missing_span_detected")

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

    return {
        "transcript": str(transcript_path) if transcript_path else "generated",
        "preview_wav_hash": wav_hash,
        "transcript_hash": transcript_hash,
        "summary": {
            "text_chars": len(text),
            "token_count": len(tokens),
            "sentence_count": len(sentences),
            "adjacent_repeat_count": len(repeats),
            "repeated_ngram_count": len(ngrams),
            "cue_hit_count": len(cues),
            "semantic_warning_count": len(semantic),
            "expected_similarity": expected_diff["similarity"] if expected_diff else None,
            "expected_recall": expected_diff["recall"] if expected_diff else None,
            "status": "review" if blocking_flags else "pass",
            "blocking_flags": blocking_flags,
        },
        "text": text,
        "adjacent_repeats": repeats,
        "repeated_ngrams": ngrams,
        "cue_hits": cues,
        "semantic_warnings": semantic,
        "expected_diff": expected_diff,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="QC a rendered preview transcript for repeated/clipped/semantic content issues")
    ap.add_argument(
        "input_file",
        type=Path,
        nargs="?",
        default=None,
        help="Path to preview WAV audio file OR ElevenLabs Scribe JSON transcript. If WAV, it will be transcribed."
    )
    ap.add_argument("-o", "--output", type=Path, default=Path("edit/preview_transcript_qc.json"), help="Output QC JSON")
    ap.add_argument("--audio", type=Path, default=Path("edit/preview.wav"), help="Path to preview WAV audio file")
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
        help="Path to mock transcript JSON to use instead of calling ElevenLabs Scribe."
    )
    args = ap.parse_args()

    # Determine input type
    input_path = args.input_file
    wav_path = args.audio.resolve() if args.audio else None

    transcript_path = None
    transcript_data = None

    if input_path:
        input_path = input_path.resolve()
        if input_path.suffix.lower() == ".wav":
            wav_path = input_path
        elif input_path.suffix.lower() == ".json":
            transcript_path = input_path
            if not transcript_path.exists():
                sys.exit(f"transcript not found: {transcript_path}")
            transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))

    if transcript_data is None:
        # We need to transcribe the WAV file
        if not wav_path or not wav_path.exists():
            sys.exit(f"WAV audio file not found at: {wav_path or 'not specified'}")

        if args.mock_transcript:
            mock_path = args.mock_transcript.resolve()
            if not mock_path.exists():
                sys.exit(f"mock transcript not found: {mock_path}")
            transcript_data = json.loads(mock_path.read_text(encoding="utf-8"))
            transcript_path = mock_path
        else:
            try:
                transcript_data = PROVIDER.transcribe(wav_path)
            except Exception as e:
                sys.exit(f"Failed during transcription: {e}")

    expected = load_expected(args)
    report = build_report(transcript_data, transcript_path, expected, DEFAULT_CUE_TERMS + args.cue_term, wav_path=wav_path)

    text = json.dumps(report, indent=2, ensure_ascii=False)
    output_path = args.output.resolve() if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        print(f"wrote preview transcript QC -> {output_path}")

    s = report["summary"]
    print(
        f"status={s['status']} repeats={s['adjacent_repeat_count'] + s['repeated_ngram_count']} "
        f"cue_hits={s['cue_hit_count']} semantic={s['semantic_warning_count']}"
    )


if __name__ == "__main__":
    main()
