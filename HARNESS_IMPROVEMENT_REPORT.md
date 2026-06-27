# Harness Improvement Report

Date: 2026-06-18

## What Changed

- Added `helpers/validate_edl_boundaries.py` to check every EDL cut against word timestamps and waveform energy.
- Added `helpers/preview_transcript_qc.py` to detect repeated final lines, leftover direction/audio-event words, and obvious semantic mismatches in a rendered preview transcript.
- Added `helpers/fcpxml_to_edl.py` to convert a human/editor-corrected FCP7 XML back into Alano EDL JSON for comparison and learning.
- Updated `.agents/steps/06_edl_generation.md` with semantic repair guidance for stitched phrases across retakes.
- Updated `.agents/steps/08_preview_qc.md` so boundary QC and preview transcript QC are part of the normal harness.
- Updated `.agents/steps/09_xml_export.md` with optional XML round-trip validation for corrected timelines.
- Updated `README.md` and core invariants/workflow docs to reflect the stricter QA flow.

## Test Fixture

Validation used the real correction artifacts from:

`C:\Users\Alano\Desktop\alanocut test 2\raw_video\edit`

The fixture includes the original agent EDL, the human-corrected `timeline_fix.xml`, source media, original source transcripts, and preview/fixed-timeline transcripts.

## Expected Harness Behavior

- A transcript-only inside-word flag is not enough to reject a cut because ASR timestamps can drift.
- A cut becomes high risk when word timing and waveform both disagree with the boundary, or when media analysis fails.
- Preview transcript QC catches issues that waveform cannot see, such as duplicated phrasing or a wrong semantic substitution.
- Human/editor fixes can be imported back from XML into EDL JSON so future analysis can compare what changed.

## Quick Validation Results

- `python -m py_compile` passed for the three new helpers plus `helpers/transcribe.py` and `helpers/transcribe_batch.py`.
- `helpers/fcpxml_to_edl.py --help`, `helpers/validate_edl_boundaries.py --help`, and `helpers/preview_transcript_qc.py --help` all ran successfully.
- `helpers/transcribe.py --help` shows the new `--force` option, and `helpers/transcribe_batch.py --help` still runs.
- XML round-trip on `timeline_fix.xml` produced 24 ranges and `total_duration_s=99.334`.
- Boundary QC on the original agent EDL produced 19 ranges, 38 boundaries, 0 inside-word flags, 0 waveform no-quiet flags, and 0 high-risk boundaries.
- Boundary QC on the human-corrected XML EDL produced 24 ranges, 48 boundaries, 6 inside-word transcript flags, 0 waveform no-quiet flags, and 0 high-risk boundaries.
- Preview transcript QC on the original agent preview produced `status=review`, with duplicate-content flags and mouth-click/audio-event flags.
- Preview transcript QC on the human-corrected timeline produced `status=review`, with the expected semantic warning for "pessoa fisica" near PJ fields.

## Fixes From Validation

- `fcpxml_to_edl.py` now prefers `--media-root` filename resolution over stale XML `pathurl` locations. This keeps corrected Premiere XML import usable even when the XML references an older absolute media path.
- `helpers/transcribe.py` now supports `--force`, and Step 08 uses it for preview transcription so a re-rendered `preview.mp4` cannot accidentally reuse a stale `transcripts/preview.json`.

## Remaining Human Review

- The helper can flag likely semantic mismatches, but it cannot prove domain truth. A human/editor still needs to confirm cases like pessoa fisica versus pessoa juridica when the script intent is ambiguous.
- The harness still produces a rough-cut XML, not final finishing media.
