# Step 08 - Preview QC

Goal: render a lightweight audio preview and inspect the rough cut before XML export.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Run boundary QC before preview review:

```powershell
.venv\Scripts\python.exe helpers\validate_edl_boundaries.py <edit_dir>\edl.json --transcripts <edit_dir>\transcripts -o <edit_dir>\edl_boundary_qc.json
```

2. Treat transcript-only `inside_word` flags as review signals, not automatic failures. ASR timestamps can drift.
3. Treat a boundary as high risk when `edl_boundary_qc.json` reports `high_risk: true`, because both word timing and waveform energy failed the cut or media analysis failed.
4. For high-risk or editorially suspicious boundaries, use `timeline_view.py` around the original source timestamp and revise `edl.json` if needed. Note: `timeline_view.py` is legacy and marked for removal in v0.5.
5. Run:

```powershell
.venv\Scripts\python.exe helpers\render.py <edit_dir>\edl.json -o <edit_dir>\preview.wav --timeline-map <edit_dir>\preview_timeline.json
```

6. Treat `preview.wav` and `preview_timeline.json` as QA only, not the final deliverable.
7. When checking preview cut boundaries, inspect cumulative timeline times in `preview.wav`, not original source timestamps.
8. If API/key/time allows, transcribe preview audio and run transcript QC:

```powershell
.venv\Scripts\python.exe helpers\transcribe.py <edit_dir>\preview.wav --edit-dir <edit_dir> --force
.venv\Scripts\python.exe helpers\preview_transcript_qc.py <edit_dir>\transcripts\preview.json -o <edit_dir>\preview_transcript_qc.json
```

9. Check:
   - missing context;
   - unnatural pacing;
   - waveform spikes;
   - obvious audio pops;
   - cut too close to a word;
   - speaker reaction accidentally removed.
   - duplicated final phrasing in the preview transcript;
   - clipped phrases or missing beat content;
   - leftover direction words or audio events such as "corta", "gravando", or mouth-click descriptions;
   - semantic mismatches introduced by retake stitching.
10. Apply small EDL fixes if needed.
11. If EDL changes materially, re-run boundary QC, re-render `preview.wav` / `preview_timeline.json`, and repeat any relevant preview transcript QC.
12. Update `edit/run_state.md` with QC findings and fixes.

## QC Gate

- Do not proceed to XML export while any boundary still reports `high_risk: true`.
- A transcript-only `inside_word` review signal does not block export when waveform and editorial inspection support the boundary.
- For stitched semantic repairs, complete both waveform boundary QC and preview transcript QC as required by Step 06. If preview transcription is unavailable, avoid the stitch or leave the edit explicitly incomplete for human resolution.
- Do not treat the existence of `preview.wav` as proof that QC passed.

## Output

- `edit/preview.wav`
- `edit/preview_timeline.json`
- `edit/edl_boundary_qc.json`
- `edit/preview_transcript_qc.json`, when preview transcription was possible
- updated `edit/run_state.md`
- revised `edit/edl.json`, if QC required changes
