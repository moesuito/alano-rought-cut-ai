# Step 08 - Preview QC

Goal: render a lightweight preview and inspect the rough cut before XML export.

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
4. For high-risk or editorially suspicious boundaries, use `timeline_view.py` around the original source timestamp and revise `edl.json` if needed.
5. Run:

```powershell
.venv\Scripts\python.exe helpers\render.py <edit_dir>\edl.json -o <edit_dir>\preview.mp4 --preview
```

6. Treat `preview.mp4` as QA only, not the final deliverable.
7. When checking preview cut boundaries, inspect cumulative timeline times in `preview.mp4`, not original source timestamps.
8. When checking source visual context, use original source timestamps.
9. If API/key/time allows, transcribe preview audio and run transcript QC:

```powershell
.venv\Scripts\python.exe helpers\transcribe.py <edit_dir>\preview.mp4 --edit-dir <edit_dir> --force
.venv\Scripts\python.exe helpers\preview_transcript_qc.py <edit_dir>\transcripts\preview.json -o <edit_dir>\preview_transcript_qc.json
```

10. Check:
   - missing context;
   - unnatural pacing;
   - visual jumps;
   - waveform spikes;
   - obvious audio pops;
   - cut too close to a word;
   - speaker reaction accidentally removed.
   - duplicated final phrasing in the preview transcript;
   - clipped phrases or missing beat content;
   - leftover direction words or audio events such as "corta", "gravando", or mouth-click descriptions;
   - semantic mismatches introduced by retake stitching, for example a "pessoa fisica" phrase attached to CNPJ/razao social fields.
11. Apply small EDL fixes if needed.
12. If EDL changes materially, re-run boundary QC and re-render preview.
13. Update `edit/run_state.md` with QC findings and fixes.

## Fade Wording

`helpers/render.py` currently bakes short fades into preview media to help catch and reduce preview pops. Describe those as preview/render-level QA aids. Do not claim the final XML has fades unless XML fade support is implemented in `helpers/edl_to_fcpxml.py`.

## Output

- `edit/preview.mp4`
- `edit/edl_boundary_qc.json`
- `edit/preview_transcript_qc.json`, when preview transcription was possible
- updated `edit/run_state.md`
- revised `edit/edl.json`, if QC required changes
