# Step 02 - Transcription

Goal: create canonical, provider-bound timed transcripts for each source.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Read workspace `alanocut.json`; it selects the only allowed provider for this run. Credentials resolve globally and are never copied into the workspace.
2. Run `alanocut transcription-doctor`. WhisperX must pass CUDA/runtime checks; CPU fallback is prohibited. ElevenLabs must have its API key configured.
3. Confirm the source list from `edit/run_state.md`.
4. Run one sequential GPU worker (RTX-class 6 GB default: batch size 2):

```powershell
.venv\Scripts\python.exe helpers\transcribe_batch.py <videos_dir> --provider configured --language pt --model large-v3 --batch-size 2
```

5. WhisperX uses `faster-whisper -> forced alignment` and, when selected, Community-1 exclusive diarization. The explicit no-diarization profile uses pinned Silero VAD and has no speaker IDs. ElevenLabs uses Scribe provider word timestamps and diarization.
6. Require 100% timed lexical words. Do not interpolate missing words or accept segment-only timing. Require speaker IDs whenever the selected profile provides diarization.
7. Cache only when source SHA-256, canonical schema, provider, models/runtime settings, and configuration hash match. A transcript from another provider is not a cache hit. A local transcript whose only open issue is well-formed unattributed acoustic activity may be provisional until the final selected-interval audit.
8. Store canonical JSON in `<videos_dir>/edit/transcripts/`; never store tokens or API keys in transcripts, reports, argv, or logs.
9. Process local sources sequentially on the single GPU. Do not start concurrent WhisperX workers.
10. Update `edit/run_state.md` with provider, model IDs, versions, source/config hashes, timing coverage, diarization status, device/service, and GPU when local.

## Gate

Every editorially relevant source must have a valid schema-v2 transcript for the selected provider with 100% timed words, or a documented exclusion reason, before packing begins. WhisperX words must be forced-aligned; Community-1 speakers are required only when that profile is selected. A scopeable local acoustic review may proceed because the EDL does not exist yet; structural, timing, selected-profile, and non-scopeable reviews remain fatal. Before XML, every blocking local component must be outside selected source intervals; every short selected inter-word residual must additionally pass the mapped preview-transcript neighbor/gap check.

## Output

- `edit/transcripts/<source>.json`
- updated `edit/run_state.md`

Do not pack transcripts or make edit decisions in this step.
