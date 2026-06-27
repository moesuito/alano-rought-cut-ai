# Step 03 - Pack Transcripts

Goal: create the compact transcript view used for reading and phrase-level selection.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Confirm `edit/transcripts/*.json` exists.
2. Run:

```powershell
.venv\Scripts\python.exe helpers\pack_transcripts.py --edit-dir <edit_dir>
```

3. Produce `edit/takes_packed.md`.
4. Treat `takes_packed.md` as the primary reading view and phrase-level map.
5. Do not treat packed transcript lines as final editorial interpretation.
6. If exact cuts inside a packed phrase are needed later, use raw Scribe JSON word timestamps.
7. Update `edit/run_state.md`.

## Output

- `edit/takes_packed.md`
- updated `edit/run_state.md`

Do not generate an EDL in this step.
