# Workflow

Follow steps in order. At each step, read only the listed step module plus core invariants and the active `edit/run_state.md`.

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
- `ELEVENLABS_API_KEY`

Outputs:
- `edit/transcripts/<source>.json`
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

Next:
- Step 07 runtime revision

## Step 07 - Runtime Revision

Read:
- `.agents/steps/07_runtime_revision.md`

Inputs:
- `edit/edl.json`
- `edit/run_state.md`

Outputs:
- revised `edit/edl.json`, if needed
- updated `edit/run_state.md`

Next:
- Step 08 preview QC

## Step 08 - Preview QC

Read:
- `.agents/steps/08_preview_qc.md`

Inputs:
- `edit/edl.json`
- source media

Outputs:
- `edit/preview.mp4`
- `edit/edl_boundary_qc.json`
- `edit/preview_transcript_qc.json`, if preview audio is transcribed
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
