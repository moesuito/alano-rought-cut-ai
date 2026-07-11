"""Readiness gate tool to verify if the edit is ready for XML export.

Checks freshness, hashes, and status across the four QC reports:
- Boundary QC (edl_boundary_qc.json)
- Audio QC (preview_audio_qc.json)
- Semantic QC (edl_semantic_qc.json)
- Preview Transcript QC (preview_transcript_qc.json)

Exit Codes:
- 0: Pass (READY - all checks pass and are fresh)
- 2: Review (REVIEW NEEDED - checks are fresh, but some warnings/reviews are flagged)
- 1: Fatal (FATAL ERROR - missing/stale reports, schema errors, or fail status)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_mtime(path: Path) -> float:
    """Get the modification time of a file. Returns 0.0 if not exists."""
    if not path.exists():
        return 0.0
    return path.stat().st_mtime


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify if edit is ready for XML export")
    ap.add_argument("edl", type=Path, nargs="?", default=Path("edit/edl.json"), help="Path to edl.json")
    ap.add_argument("--transcripts", type=Path, default=Path("edit/transcripts"), help="Path to transcripts directory")
    ap.add_argument("--boundary-report", type=Path, default=Path("edit/edl_boundary_qc.json"), help="Path to edl_boundary_qc.json")
    ap.add_argument("--audio-report", type=Path, default=Path("edit/preview_audio_qc.json"), help="Path to preview_audio_qc.json")
    ap.add_argument("--semantic-report", type=Path, default=Path("edit/edl_semantic_qc.json"), help="Path to edl_semantic_qc.json")
    ap.add_argument("--transcript-report", type=Path, default=Path("edit/preview_transcript_qc.json"), help="Path to preview_transcript_qc.json")
    ap.add_argument("--audio", type=Path, default=Path("edit/preview.wav"), help="Path to preview.wav")
    ap.add_argument("--timeline-map", type=Path, default=Path("edit/preview_timeline.json"), help="Path to preview_timeline.json")

    args = ap.parse_args()

    edl_path = args.edl.resolve()
    transcripts_dir = args.transcripts.resolve()
    boundary_path = args.boundary_report.resolve()
    audio_path = args.audio_report.resolve()
    semantic_path = args.semantic_report.resolve()
    transcript_path = args.transcript_report.resolve()
    wav_path = args.audio.resolve()
    map_path = args.timeline_map.resolve()

    print("=== Alano Cut Edit Readiness Gate ===")

    # 1. Verify existence of core edit files
    if not edl_path.exists():
        print(f"FATAL: EDL file not found at {edl_path}")
        sys.exit(1)

    if not wav_path.exists():
        print(f"FATAL: Preview WAV file not found at {wav_path}")
        sys.exit(1)

    # Compute current source hashes
    current_edl_hash = compute_sha256(edl_path)
    current_wav_hash = compute_sha256(wav_path)
    current_map_hash = compute_sha256(map_path) if map_path.exists() else ""

    edl_mtime = get_mtime(edl_path)
    wav_mtime = get_mtime(wav_path)

    # 2. Check existence of all four reports
    reports = {
        "Boundary QC": boundary_path,
        "Audio QC": audio_path,
        "Semantic QC": semantic_path,
        "Preview Transcript QC": transcript_path,
    }

    missing_reports = []
    for name, r_path in reports.items():
        if not r_path.exists():
            missing_reports.append(name)
            print(f"FATAL: Report missing: {name} ({r_path.name})")

    if missing_reports:
        print("\nFATAL: One or more required QC reports are missing. Run the QC tools first.")
        sys.exit(1)

    # Load all reports
    try:
        boundary_data = json.loads(boundary_path.read_text(encoding="utf-8"))
        audio_data = json.loads(audio_path.read_text(encoding="utf-8"))
        semantic_data = json.loads(semantic_path.read_text(encoding="utf-8"))
        transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FATAL: Failed to parse one of the JSON reports: {e}")
        sys.exit(1)

    # 3. Freshness / Hash checks
    stale = False

    # A. Boundary QC
    # It does not contain edl_hash, check mtime >= edl_mtime (with 1s grace period)
    if get_mtime(boundary_path) < edl_mtime - 1.0:
        print(f"STALE: Boundary QC report is older than EDL. Re-run boundary validation.")
        stale = True

    # B. Audio QC
    if audio_data.get("edl_hash") != current_edl_hash:
        print(f"STALE: Audio QC report EDL hash mismatch. Re-run audio QC.")
        stale = True
    if map_path.exists() and audio_data.get("timeline_map_hash") != current_map_hash:
        print(f"STALE: Audio QC report timeline map hash mismatch. Re-run audio QC.")
        stale = True
    if audio_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Audio QC report preview WAV hash mismatch. Re-run audio QC.")
        stale = True

    # C. Semantic QC
    if semantic_data.get("edl_hash") != current_edl_hash:
        print(f"STALE: Semantic QC report EDL hash mismatch. Re-run semantic QC.")
        stale = True
    # Check each transcript hash in semantic report
    stored_transcript_hashes = semantic_data.get("transcript_hashes", {})
    if not isinstance(stored_transcript_hashes, dict):
        print("FATAL: Semantic QC report transcript_hashes is invalid format.")
        sys.exit(1)

    # Load source IDs from EDL
    try:
        edl_content = json.loads(edl_path.read_text(encoding="utf-8"))
        sources = edl_content.get("sources", {})
    except Exception as e:
        print(f"FATAL: Failed to load sources from EDL: {e}")
        sys.exit(1)

    for source_id in sources:
        t_path = transcripts_dir / f"{source_id}.json"
        current_t_hash = compute_sha256(t_path)
        stored_t_hash = stored_transcript_hashes.get(source_id)
        if current_t_hash != stored_t_hash:
            print(f"STALE: Semantic QC report source transcript hash mismatch for {source_id}. Re-run semantic QC.")
            stale = True

    # D. Preview Transcript QC
    if transcript_data.get("preview_wav_hash") != current_wav_hash:
        print(f"STALE: Preview transcript QC report WAV hash mismatch. Re-run preview transcript QC.")
        stale = True
    # If transcript file is specified and exists, check its hash
    t_file_val = transcript_data.get("transcript")
    if t_file_val and t_file_val != "generated":
        t_file_path = Path(t_file_val).resolve()
        if t_file_path.exists():
            if transcript_data.get("transcript_hash") != compute_sha256(t_file_path):
                print("STALE: Preview transcript QC report transcript file hash mismatch. Re-run preview transcript QC.")
                stale = True
    # Check if report is older than preview WAV (with 1s grace period)
    if get_mtime(transcript_path) < wav_mtime - 1.0:
        print("STALE: Preview transcript QC report is older than preview WAV. Re-run preview transcript QC.")
        stale = True

    if stale:
        print("\nFATAL: One or more QC reports are stale. Re-run the respective QC tools first.")
        sys.exit(1)

    # 4. Status mapping
    # We evaluate the statuses of all reports.
    # Statuses can be: pass, warning, review, fail
    boundary_status = "pass"
    if boundary_data.get("high_risk_count", 0) > 0:
        boundary_status = "review"
    # If there are waveform errors or missing sources, that's fatal
    if boundary_data.get("waveform_error_count", 0) > 0 or boundary_data.get("missing_source_count", 0) > 0:
        boundary_status = "fail"

    audio_status = audio_data.get("status", "pass")
    semantic_status = semantic_data.get("status", "pass")
    transcript_status = transcript_data.get("status", "pass")

    print(f"\nReport Statuses:")
    print(f"- Boundary QC:          {boundary_status.upper()}")
    print(f"- Audio QC:             {audio_status.upper()}")
    print(f"- Semantic QC:          {semantic_status.upper()}")
    print(f"- Preview Transcript QC: {transcript_status.upper()}")

    # Determine final output status and exit code
    all_statuses = [boundary_status, audio_status, semantic_status, transcript_status]

    if "fail" in all_statuses or "error" in all_statuses:
        print("\n=== GATE STATUS: FATAL ERROR ===")
        print("Edit has critical errors. Fix them before proceeding.")
        sys.exit(1)
    elif "review" in all_statuses or "warning" in all_statuses:
        print("\n=== GATE STATUS: REVIEW NEEDED ===")
        print("Edit has warnings or items that require manual review.")
        sys.exit(2)
    else:
        print("\n=== GATE STATUS: READY ===")
        print("All quality gates passed successfully. Ready for XML export.")
        sys.exit(0)


if __name__ == "__main__":
    main()
