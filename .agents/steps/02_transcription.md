# Step 02 - Transcription

Goal: create cached word-level Scribe JSON transcripts for each source.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Verify `ELEVENLABS_API_KEY` resolves from environment or `.env`.
2. Confirm source list from `edit/run_state.md`.
3. Run:

```powershell
.venv\Scripts\python.exe helpers\transcribe_batch.py <videos_dir>
```

4. Use word-level verbatim Scribe transcription.
5. Do not use phrase-level mode, SRT-only mode, or normalized fillers.
6. Cache transcripts per source.
7. Do not re-transcribe unchanged files.
8. Store raw JSONs in `<videos_dir>/edit/transcripts/`.
9. Update `edit/run_state.md` with transcription status.

## Output

- `edit/transcripts/<source>.json`
- updated `edit/run_state.md`

Do not pack transcripts or make edit decisions in this step.
