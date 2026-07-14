# Architecture - Alano Cut

Status: living document.  
Updated: 2026-07-11

## Overview

The runtime is a Python/FFmpeg helper suite directed by the product `AGENTS.md` and modular `.agents/` workflow. Each workspace explicitly selects local CUDA WhisperX or ElevenLabs Scribe for source and preview transcription; EDL JSON is the editable editorial contract; FCP7/XMEML is the final deliverable.

## Stack

- Python 3.10+ with `numpy`, `requests`, and `pillow`.
- Shared Python 3.12 runtime on C: with WhisperX 3.8.6, faster-whisper 1.2.1, Pyannote 4.0.7, and PyTorch 2.8 CUDA 12.8.
- FFmpeg/ffprobe for media extraction, raw/RNNoise audio analysis, and XML source metadata.
- PowerShell installer and `alanocut` CLI for Windows workspace bootstrap/update.

## Modules

- `helpers/transcription_contract.py` — dependency-free canonical schema, fingerprints, cache validation, and atomic persistence.
- `helpers/transcription_settings.py` and `helpers/setup_wizard.py` — secret-free global/workspace profiles and the interactive provider setup.
- `helpers/whisperx_runtime.py`, `helpers/whisperx_worker.py`, and `helpers/transcription_providers.py` — pinned CUDA runtime, isolated heavy worker, forced alignment, optional Community-1 diarization, and pinned Silero VAD for the no-diarization profile.
- `helpers/transcribe*.py` — canonical source transcripts for WhisperX and ElevenLabs Scribe.
- `helpers/pack_transcripts.py` — compact editorial reading view.
- `helpers/refine_edl_boundaries.py` — in-place lexical/acoustic refinement to exact rational source frames.
- `helpers/render.py` — dry PCM preview WAV and cumulative timeline map.
- `helpers/preview_audio_qc.py`, `helpers/semantic_qc.py`, and `helpers/preview_transcript_qc.py` — hash-bound audio, required-beat, and join-content gates.
- `helpers/verify_edit_ready.py` — fail-closed artifact/schema/hash/status gate before agent-driven XML export.
- `helpers/*fcpxml*.py` and `helpers/edl_to_fcpxml.py` — XML conversion and round-trip.
- `.agents/` — product workflow and completion gates copied into user workspaces.
- `bin/` and `install.ps1` — global install/update and workspace initialization.

## Boundaries

- Helpers may create artifacts only in a selected video's `edit/` directory.
- XML references original media; preview artifacts are QA-only.
- The normative agent path is audio-only and applies no fades, normalization, frame inspection, or visual review.
- The mandatory dataflow is `refined EDL -> WAV/map -> audio QC -> semantic QC -> persisted timed preview transcript/hash -> join transcript QC -> readiness(0) -> XML`.
- Any EDL change invalidates every downstream artifact and restarts the dataflow at boundary refinement.
- Private source media, API keys, transcripts, and temporary edit artifacts are never committed.
- Credentials remain global. Hugging Face tokens and ElevenLabs keys are forbidden in command arguments, workspace config, config/result JSON, reports, exceptions, logs, and Git.
- Provider routing is fail-closed: no silent fallback is allowed. Local WhisperX is CUDA-only and requires 100% forced-aligned lexical timing; Community-1 speakers are required only for the selected Community-1 profile. ElevenLabs uses provider word timestamps and diarization. Well-formed unattributed local acoustic components may be cached provisionally before an EDL exists, but readiness rejects blockers that overlap selected source audio. A selected short inter-word residual is cleared only when the mapped preview ASR preserves both neighboring words consecutively and places no word over its interval.
- Development routing lives in `DEV_AGENTS.md`; the shipped product routing remains in `AGENTS.md`.

## Testing & Parallelism

Unit tests must use generated synthetic media. Private lesson regression is opt-in through an environment variable and writes only to temporary directories. One Antigravity worker owns one worktree at a time; product-code tasks are sequential in the v0.4 branch because they share EDL/timing interfaces.

## Decisions

- 2026-07-11 - Development harness uses `DEV_AGENTS.md` to avoid changing the product `AGENTS.md` role contract.
- 2026-07-11 - v0.4 preview is audio-only; visual review is not a normative gate.
- 2026-07-13 - v0.4 requires join-complete audio/transcript evidence and readiness exit code 0 before agent-driven XML export; legacy visual/validator helpers are manual-only until v0.5.0.
- 2026-07-14 - v0.4 adds explicit workspace provider profiles: CUDA WhisperX/faster-whisper with optional Community-1 diarization or ElevenLabs Scribe, all normalized into a hash-bound canonical schema.
- 2026-07-13 - Scopeable acoustic-review transcripts are stable provisional caches; the exact EDL interval audit remains mandatory and fail-closed before XML.
- 2026-07-13 - Short inter-word residuals are deferred to the mandatory preview transcript instead of being trusted from source timing alone.
