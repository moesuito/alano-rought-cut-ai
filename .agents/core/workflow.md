# Workflow

Follow the steps in order under either operating protocol from `AGENTS.md`.

- Protocol A (default): read the complete core workflow and all step modules before execution, then work end to end while checkpointing state.
- Protocol B (context-constrained): at each step, read only the listed step module plus core invariants and the active `edit/run_state.md`.

Both protocols use the same tasks, gates, artifacts, and completion criteria below.

## Step 01 - Inventory

Read:
- `.agents/steps/01_inventory.md`

Inputs:
- source media directory
- repo helpers

Outputs:
- `<videos_dir>/edit/`
- initialized or updated `edit/run_state.md`
- source list and basic metadata

Next:
- Step 02 transcription

## Step 02 - Transcription

Read:
- `.agents/steps/02_transcription.md`

Inputs:
- source media files
- passing `alanocut transcription-doctor`
- `HF_TOKEN` for the accepted Community-1 model

Outputs:
- `edit/transcripts/<source>.json`
- 100% forced-aligned words with Community-1 speaker IDs
- updated `edit/run_state.md`

Next:
- Step 03 pack transcripts

## Step 03 - Pack Transcripts

Read:
- `.agents/steps/03_pack_transcripts.md`

Inputs:
- `edit/transcripts/*.json`

Outputs:
- `edit/takes_packed.md`
- updated `edit/run_state.md`

Next:
- Step 04 brief inference

## Step 04 - Brief Inference

Read:
- `.agents/steps/04_brief_inference.md`

Inputs:
- `edit/USER_BRIEF.md`, if present
- `edit/project.md`, if present
- `edit/takes_packed.md`

Outputs:
- updated `edit/run_state.md` with inferred type, objective, target runtime, and uncertainty level
- XML timeline naming fields in `edit/run_state.md`

Next:
- Step 05 editorial strategy

## Step 05 - Editorial Strategy

Read:
- `.agents/steps/05_editorial_strategy.md`
- one matching file from `.agents/archetypes/`, or at most two if uncertain

Inputs:
- `edit/run_state.md`
- `edit/takes_packed.md`
- selected archetype module

Outputs:
- updated `edit/run_state.md` with strategy and pre-scan notes

Next:
- Step 06 EDL generation

## Step 06 - EDL Generation

Read:
- `.agents/steps/06_edl_generation.md`
- `.agents/prompts/editor_subagent.md` if using a sub-agent

Inputs:
- `edit/run_state.md`
- `edit/takes_packed.md`
- `edit/transcripts/*.json` when exact word timestamps are needed

Outputs:
- `edit/edl.json`
- updated `edit/run_state.md`
- `metadata.timeline_name` inside `edit/edl.json`
- rational `metadata.sequence_fps` and mandatory `metadata.required_beats` inside `edit/edl.json`
- `ranges[].beat_id` references for every declared editorial beat

Next:
- Step 07 runtime revision

## Step 07 - Runtime Revision

Read:
- `.agents/steps/07_runtime_revision.md`

Inputs:
- `edit/edl.json`
- `edit/run_state.md`
- `edit/transcripts/*.json`
- source media and the bundled RNNoise model

Outputs:
- runtime-revised and boundary-refined `edit/edl.json`
- exact `source_in_frame` / `source_out_frame` values on every range
- `edit/edl_boundary_qc.json` from `refine_edl_boundaries.py`
- updated `edit/run_state.md`

Next:
- Step 08 preview QC

## Step 08 - Preview QC

Read:
- `.agents/steps/08_preview_qc.md`

Inputs:
- refined `edit/edl.json`
- `edit/edl_boundary_qc.json`
- `edit/transcripts/*.json`
- source media

Outputs:
- `edit/preview.wav`
- `edit/preview_timeline.json`
- `edit/preview_audio_qc.json`
- `edit/edl_semantic_qc.json`
- forced, persisted `edit/transcripts/preview.json` bound to the preview WAV hash
- `edit/preview_transcript_qc.json` with evidence for every join
- verification notes in `edit/run_state.md`
- EDL fixes, if needed

Next:
- Step 09 XML export

## Step 09 - XML Export

Read:
- `.agents/steps/09_xml_export.md`

Inputs:
- `edit/edl.json`
- source media paths
- every Step 07/08 artifact and QC report
- a successful `verify_edit_ready.py` result with exit code 0

Outputs:
- `edit/timeline.xml`
- optional imported EDL from `helpers/fcpxml_to_edl.py` when validating a human/editor correction XML
- updated `edit/run_state.md` with XML path and timeline name

Next:
- Step 10 persist memory

## Step 10 - Persist Memory

Read:
- `.agents/steps/10_persist_memory.md`

Inputs:
- `edit/run_state.md`
- `edit/edl.json`
- QC/export results

Outputs:
- appended `edit/project.md`

Next:
- Done

## Shared Completion Criteria

These criteria apply identically to Protocol A and Protocol B. Finish only when:

- every intended source is inventoried and accounted for;
- every editorially relevant source has a readable transcript or a documented exclusion reason;
- every included source and preview transcript uses the canonical WhisperX schema, matching hashes/configuration, 100% forced-aligned word timing, and Community-1 diarization;
- the EDL is coherent, duration-checked, structurally valid, declares `sequence_fps` and `required_beats`, and uses refined exact-frame ranges;
- every join is represented in both preview audio QC and timed preview transcript QC;
- no unresolved boundary review, excessive entry silence, tight first-word attack, orphan cue/token, crossed join, missing beat, or stale hash remains;
- material EDL revisions restarted the mandatory chain at boundary refinement;
- `verify_edit_ready.py` returned exit code 0 for the exact EDL, WAV, map, transcripts, and reports being exported;
- `edit/timeline.xml` exists, has the intended sequence name, and references original media;
- `edit/run_state.md` and `edit/project.md` preserve the result for recovery and future sessions;
- the final handoff reports the XML path and any outstanding human-review items.
