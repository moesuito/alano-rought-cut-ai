# Step 02 - Transcription

Goal: create canonical, forced-aligned, speaker-diarized transcripts for each source.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Verify `HF_TOKEN` resolves from environment or `.env` and the user has accepted the Community-1 model terms.
2. Run `alanocut transcription-doctor`; every CUDA/runtime check must pass. CPU fallback is prohibited.
3. Confirm the source list from `edit/run_state.md`.
4. Run one sequential GPU worker (RTX-class 6 GB default: batch size 2):

```powershell
.venv\Scripts\python.exe helpers\transcribe_batch.py <videos_dir> --provider whisperx --language pt --model large-v3 --batch-size 2
```

5. Require the normative chain: faster-whisper -> WhisperX forced alignment -> `pyannote/speaker-diarization-community-1` exclusive diarization.
6. Require 100% timed lexical words and a diarized `speaker_id` on every word. Do not interpolate missing words or accept segment-only timing.
7. Cache only when source SHA-256, canonical schema, provider, models, runtime versions, and configuration hash match. A legacy ElevenLabs/whisper.cpp file is not a cache hit.
8. Store canonical JSON in `<videos_dir>/edit/transcripts/`; never store the Hugging Face token in transcripts, reports, argv, or logs.
9. Process sources sequentially on the single GPU. Do not start concurrent WhisperX workers.
10. Update `edit/run_state.md` with provider, model IDs, versions, source/config hashes, alignment coverage, diarization status, device, and GPU.

## Gate

Every editorially relevant source must have a valid schema-v1 `whisperx_faster_whisper` transcript with 100% forced-aligned word timing and Community-1 diarization, or a documented exclusion reason, before packing begins.

## Output

- `edit/transcripts/<source>.json`
- updated `edit/run_state.md`

Do not pack transcripts or make edit decisions in this step.
