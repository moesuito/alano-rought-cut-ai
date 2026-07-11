"""QC check for semantic coverage of required beats in the EDL.

Validates that metadata.required_beats exists, is properly formed, and that
evidence for each required beat is present in the source transcripts within
the time ranges specified in the EDL.

Usage:
    python helpers/semantic_qc.py edit/edl.json --transcripts edit/transcripts -o edit/edl_semantic_qc.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


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


def is_phrase_in_text(phrase: str, text: str) -> bool:
    phrase_tokens = tokenize(phrase)
    text_tokens = tokenize(text)
    if not phrase_tokens:
        return True
    if len(phrase_tokens) > len(text_tokens):
        return False
    # Check if phrase_tokens is a contiguous sublist of text_tokens
    for i in range(len(text_tokens) - len(phrase_tokens) + 1):
        if text_tokens[i : i + len(phrase_tokens)] == phrase_tokens:
            return True
    return False


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_words(transcripts_dir: Path | None, source_id: str) -> list[dict[str, Any]]:
    if transcripts_dir is None:
        return []
    path = transcripts_dir / f"{source_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    words = []
    for w in data.get("words", []):
        if w.get("type") != "word":
            continue
        if w.get("start") is None or w.get("end") is None:
            continue
        words.append(w)
    return words


def validate_beat_schema(beat: Any, idx: int) -> tuple[bool, str]:
    if not isinstance(beat, dict):
        return False, f"Beat at index {idx} must be a dictionary."
    if "id" not in beat or not isinstance(beat["id"], str) or not beat["id"].strip():
        return False, f"Beat at index {idx} is missing a non-empty string 'id'."
    if "description" not in beat or not isinstance(beat["description"], str):
        return False, f"Beat at index {idx} is missing a string 'description'."
    if "evidence_any_of" not in beat or not isinstance(beat["evidence_any_of"], list):
        return False, f"Beat at index {idx} is missing list 'evidence_any_of'."
    if not beat["evidence_any_of"]:
        return False, f"Beat at index {idx} has an empty 'evidence_any_of' list."
    for ev_idx, ev in enumerate(beat["evidence_any_of"]):
        if not isinstance(ev, str) or not ev.strip():
            return False, f"Beat at index {idx} has empty or non-string evidence at index {ev_idx}."
    return True, ""


def run_semantic_qc(edl_path: Path, transcripts_dir: Path) -> dict[str, Any]:
    if not edl_path.exists():
        raise FileNotFoundError(f"EDL file not found: {edl_path}")

    edl_hash = compute_sha256(edl_path)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))

    # 1. Validate required_beats is present in metadata
    metadata = edl.get("metadata")
    if not isinstance(metadata, dict) or "required_beats" not in metadata:
        return {
            "edl_path": str(edl_path),
            "edl_hash": edl_hash,
            "status": "fail",
            "error": "metadata.required_beats is mandatory.",
            "beats": [],
        }

    required_beats = metadata["required_beats"]
    if not isinstance(required_beats, list):
        return {
            "edl_path": str(edl_path),
            "edl_hash": edl_hash,
            "status": "fail",
            "error": "metadata.required_beats must be a list.",
            "beats": [],
        }

    # 2. Validate beat schemas
    for idx, beat in enumerate(required_beats):
        ok, err_msg = validate_beat_schema(beat, idx)
        if not ok:
            return {
                "edl_path": str(edl_path),
                "edl_hash": edl_hash,
                "status": "fail",
                "error": f"Invalid beat schema: {err_msg}",
                "beats": [],
            }

    # 3. Compute transcript hashes and load words for sources referenced in the EDL
    sources = edl.get("sources", {})
    transcript_hashes: dict[str, str] = {}
    words_by_source: dict[str, list[dict[str, Any]]] = {}

    for source_id in sources:
        t_path = transcripts_dir / f"{source_id}.json"
        if t_path.exists():
            transcript_hashes[source_id] = compute_sha256(t_path)
            words_by_source[source_id] = load_words(transcripts_dir, source_id)
        else:
            transcript_hashes[source_id] = ""
            words_by_source[source_id] = []

    # 4. Verify evidence for each required beat
    beats_results = []
    ranges = edl.get("ranges", [])
    all_satisfied = True
    errors = []

    for beat in required_beats:
        beat_id = beat["id"]
        description = beat["description"]
        evidence_any_of = beat["evidence_any_of"]

        # Find ranges referencing this beat_id
        # Check both "beat_id" and "beat" keys
        matching_ranges = []
        for r_idx, r in enumerate(ranges):
            ref_id = r.get("beat_id") or r.get("beat")
            if ref_id == beat_id:
                matching_ranges.append((r_idx, r))

        if not matching_ranges:
            beats_results.append({
                "id": beat_id,
                "description": description,
                "satisfied": False,
                "matched_evidence": None,
                "ranges_checked": [],
                "text_analyzed": "",
            })
            all_satisfied = False
            continue

        # Extract words from the transcripts selected by the matching ranges
        combined_words = []
        ranges_checked = []
        for r_idx, r in matching_ranges:
            ranges_checked.append(r_idx)
            source_id = r.get("source")
            start = r.get("start")
            end = r.get("end")

            if not source_id or start is None or end is None:
                errors.append(f"Range {r_idx} is missing source, start, or end.")
                continue

            words = words_by_source.get(source_id, [])
            for w in words:
                w_start = w.get("start")
                w_end = w.get("end")
                if w_start is None or w_end is None:
                    continue
                # Select word if its midpoint is within the range
                midpoint = (w_start + w_end) / 2.0
                if start <= midpoint <= end:
                    combined_words.append(w)

        # Sort combined words by their start time to reconstruct coherent speech
        # (Though they are usually ordered, sorting is safer if from multiple ranges)
        combined_words.sort(key=lambda x: x["start"])
        reconstructed_text = " ".join(w.get("text", "") for w in combined_words).strip()

        # Check evidence
        matched_evidence = None
        for ev in evidence_any_of:
            if is_phrase_in_text(ev, reconstructed_text):
                matched_evidence = ev
                break

        satisfied = matched_evidence is not None
        if not satisfied:
            all_satisfied = False

        beats_results.append({
            "id": beat_id,
            "description": description,
            "satisfied": satisfied,
            "matched_evidence": matched_evidence,
            "ranges_checked": ranges_checked,
            "text_analyzed": reconstructed_text,
        })

    # Status: pass if 100% of required beats are satisfied and there are no schema errors
    status = "pass" if (all_satisfied and not errors) else "review"

    return {
        "edl_path": str(edl_path),
        "edl_hash": edl_hash,
        "transcript_hashes": transcript_hashes,
        "status": status,
        "beats": beats_results,
        "errors": errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="QC check for semantic coverage of required beats")
    ap.add_argument("edl", type=Path, nargs="?", default=Path("edit/edl.json"), help="Path to edl.json")
    ap.add_argument("--transcripts", type=Path, default=Path("edit/transcripts"), help="Path to transcripts directory")
    ap.add_argument("-o", "--output", type=Path, default=Path("edit/edl_semantic_qc.json"), help="Path to output semantic QC report")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    transcripts_dir = args.transcripts.resolve()
    output_path = args.output.resolve()

    if not edl_path.exists():
        sys.exit(f"Error: EDL not found at {edl_path}")

    try:
        report = run_semantic_qc(edl_path, transcripts_dir)
    except Exception as e:
        sys.exit(f"Error executing semantic QC: {e}")

    # Write report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote semantic QC -> {output_path}")

    # Check exit status for pipeline usage (1 on fatal failure)
    if report["status"] == "fail":
        sys.exit(1)


if __name__ == "__main__":
    main()
