# Step 02 — Transcription

Goal: create canonical local word-timed transcripts for every editorially relevant source.

## Load

- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file

## Tasks

1. Confirm the source inventory from `edit/run_state.md`.
2. Run the local transcription doctor and record the selected GPU/runtime.
3. Execute one controlled local transcription worker:

```powershell
.venv\Scripts\python.exe helpers\transcribe_batch.py <videos_dir> --provider whisper-vulkan --language pt
```

4. Use DeepFilterNet for the configured acoustic/ASR preprocessing path.
5. Use Whisper Vulkan for ASR, Wav2Vec2 DirectML for word alignment, and Pyannote ONNX DirectML for diarization when enabled.
6. Require positive timing for every lexical word used by the edit. Never invent or interpolate missing word timing.
7. Require speaker IDs when diarization is enabled; record any allowed fallback explicitly.
8. Accept cache only when source SHA-256, canonical schema, model/runtime identity and configuration fingerprint match.
9. Store canonical JSON in `<videos_dir>/edit/transcripts/` without tokens, API keys or private runtime secrets.
10. Record models, versions, source/config hashes, timing coverage, diarization status, device and GPU in `edit/run_state.md`.

WhisperX, ElevenLabs and AssemblyAI are legacy paths and must not be selected for new v0.6 runs.

## Gate

Every editorially relevant source must have a readable canonical transcript with complete positive word timing or a documented exclusion reason. Structural errors, missing timing, stale provenance and silent fallbacks are fatal.

## Output

- `edit/transcripts/<source>.json`
- updated `edit/run_state.md`

Do not pack transcripts or make editorial decisions in this step.
