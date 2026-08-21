# Step 08 - Preview QC

Goal: render and validate the exact audio-only rough cut before XML export.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Confirm Step 07 completed with a current `edl_boundary_qc.json`, exit code 0, and `review_required: false` on every EDL range. The legacy validator is not a substitute.
2. Render the dry, frame-exact preview WAV and timeline map:

```powershell
.venv\Scripts\python.exe helpers\render.py <edit_dir>\edl.json -o <edit_dir>\preview.wav --timeline-map <edit_dir>\preview_timeline.json
```

3. Run preview audio QC against that exact WAV/map/EDL:

```powershell
.venv\Scripts\python.exe helpers\preview_audio_qc.py <edit_dir>\preview.wav --timeline-map <edit_dir>\preview_timeline.json --edl <edit_dir>\edl.json --output <edit_dir>\preview_audio_qc.json
```

4. Validate required-beat evidence in the actually selected source words:

```powershell
.venv\Scripts\python.exe helpers\semantic_qc.py <edit_dir>\edl.json --transcripts <edit_dir>\transcripts --output <edit_dir>\edl_semantic_qc.json
```

5. Always transcribe the exact preview WAV, persist timed words plus its SHA-256 as `edit/transcripts/preview.json`, and then run join-centric QC. The helper performs those two actions in that order in one invocation:

```powershell
.venv\Scripts\python.exe helpers\preview_transcript_qc.py <edit_dir>\preview.wav --provider configured --audio <edit_dir>\preview.wav --edl <edit_dir>\edl.json --transcripts <edit_dir>\transcripts --timeline-map <edit_dir>\preview_timeline.json --transcript-output <edit_dir>\transcripts\preview.json --output <edit_dir>\preview_transcript_qc.json
```

6. Require both persisted artifacts and evidence for every range entry and every join. Check excessive entry inactivity, a tight first-word attack, residual rejected/cue activity, left-tail damage, clipping/pops, crossed joins, unexpected one- or two-token prefixes, missing/deformed expected words, duplicates, missing beats, untimed preview words, and any canonical internal lexical gap still above 300ms without an exact reasoned override.
7. Treat global similarity and token recall as supplemental evidence; join-local expected suffix/prefix evidence is authoritative for transition integrity.
8. Treat `preview.wav` and `preview_timeline.json` as QA only, not the final deliverable. Inspect cumulative preview times, not original-source timeline positions.
9. If any EDL range changes, restart at the Step 07 refiner and regenerate every downstream artifact in this order.
10. Update `edit/run_state.md` with artifact hashes, per-gate statuses, findings, and fixes.

The normative agent workflow is audio-only and does not inspect frames. `timeline_view.py` and `validate_edl_boundaries.py` remain manual-only legacy diagnostics outside this gate chain pending dependency-aware removal.

## QC Gate

- Do not proceed while any mandatory artifact is missing, stale, structurally inconsistent, or not `pass`.
- Preview transcription is mandatory and must use the same canonical provider/configuration as the sources. Missing selected-provider timing, zero timed-word coverage, a mismatched provider/configuration, or missing required diarization is blocking.
- Every audio and transcript join report must have the same join cardinality as `preview_timeline.json`.
- Do not treat the existence of `preview.wav` or any individual report as proof that QC passed.
- Step 09 still must run `verify_edit_ready.py` and receive exit code 0 for this exact artifact set.

## Output

- `edit/preview.wav`
- `edit/preview_timeline.json`
- `edit/preview_audio_qc.json`
- `edit/edl_semantic_qc.json`
- `edit/transcripts/preview.json` with timed words and preview WAV hash binding
- `edit/preview_transcript_qc.json` with every join represented
- updated `edit/run_state.md`
- revised `edit/edl.json`, if QC required changes
