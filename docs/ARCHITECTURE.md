# Architecture - Alano Cut

Status: living document.  
Updated: 2026-08-20 (v0.6.0 Agentic Loop Architecture)

## Overview

The runtime is a Python/FFmpeg helper suite directed by the product `AGENTS.md` and modular `.agents/` workflow, or executed autonomously via the embedded **Agentic Multi-Turn Loop Engine (`helpers/agentic_editor.py`)** and interactive CLI (`alanocut`).

Each workspace explicitly selects local GPU Whisper (Vulkan + DirectML), AssemblyAI, or ElevenLabs Scribe for source and preview transcription; EDL JSON is the editable editorial contract; FCP7/XMEML is the final deliverable.

## Stack

- **Python 3.10+** with `numpy<2.0`, `requests`, `torch`, `torchaudio`, `deepfilternet`, `onnxruntime-directml`, `scipy`, `scikit-learn`, `rich`.
- **Universal GPU Runtime on Windows**:
  - `whisper.cpp` (Vulkan) for word timestamps;
  - `Wav2Vec2 CTC` (`jonatasgrosman/wav2vec2-large-xlsr-53-portuguese` via DirectML) for millisecond-exact forced alignment;
  - `Pyannote ONNX` (DirectML) for speaker diarization;
  - `DeepFilterNet 3` for neural pre-filtering (100 dB reduction).
  - Runs universally across NVIDIA GeForce/RTX, AMD Radeon, and Intel Arc/Iris GPUs without requiring 16 GB CUDA-only environments.
- **Cognitive Editorial Engine**: Multi-turn sequential agentic loop (`helpers/agentic_editor.py`) executing Strategy -> Assembly -> Autonomous Reflection/Critique Loop with any OpenAI-Compatible API (NVIDIA NIM, Ollama, vLLM, OpenAI, Groq).
- **FFmpeg/ffprobe** for media extraction, audio analysis, and XML source metadata.
- **PowerShell installer & Interactive TUI (`alanocut`)** for Windows workspace bootstrap, update, and zero-pollution session runs.

## Modules

- `helpers/agentic_editor.py` — Autonomous multi-turn cognitive loop engine (Strategy -> Assembly -> Reflection/Refinement Loop -> Dynamic Sign-off).
- `helpers/prompts/agentic_prompts.py` — Modular task prompts for the agentic loop engine.
- `helpers/prompts/editor_system_prompt.md` — 46-section Master Editorial System Prompt for one-shot mode.
- `helpers/orchestrator.py` — Unified 10-step orchestrator pipeline with `--mode agentic` (default) and `--mode one-shot`.
- `helpers/interactive_cli.py` — Modern terminal UI with Rich spinners, format selector, and live progress.
- `helpers/session_manager.py` — AppData session caching (`%APPDATA%/AlanoCut/sessions/`), zero folder pollution, and audit persistence (`session.log`, `editorial_audit.txt`, `editorial_strategy.json`).
- `helpers/transcription_contract.py` — Canonical transcript schema, fingerprints, cache validation, and atomic persistence.
- `helpers/forced_alignment.py` — Wav2Vec2 CTC forced alignment engine with trellis jump suppression and pause chunking.
- `helpers/vulkan_runtime.py` and `helpers/directml_diarization.py` — Universal local GPU acceleration across Vulkan and DirectML.
- `helpers/pack_transcripts.py` — Compact editorial reading view (`takes_packed.md`).
- `helpers/refine_edl_boundaries.py` — In-place lexical/acoustic refinement (VAD, Snapper, 66ms padding).
- `helpers/render.py` — Dry PCM preview WAV and cumulative timeline map.
- `helpers/preview_audio_qc.py` — Audio quality validation (clipping, phase, pops).
- `helpers/edl_to_fcpxml.py` — Final Cut Pro 7 XML timeline generator for Adobe Premiere Pro.

## Boundaries & Principles

- **Zero Folder Pollution**: All session caches, transcripts, and logs live in `%APPDATA%/AlanoCut/sessions/<session_id>/`. The only file written to the user's working folder is `./timeline.xml`.
- **Original Media Referencing**: XML strictly references original high-quality media files (`.mov`, `.mp4`).
- **Never Cut Inside a Word**: Word boundaries are strictly enforced. Acoustic attacks are snapped with $\ge 66$ms padding by `refine_edl_boundaries.py`.
- **Audio-First QA**: Agent QC validates audio energy, clipping, and continuous speech flow without requiring heavy video rendering.
- **Fail-Closed Verification**: Transcripts must be forced-aligned and valid before EDL generation.

## Reference Architecture Documents

- [`docs/AGENTIC_LOOP_V0.6.0.md`](file:///O:/Antigravity/alano-cut/.worktrees/codex-feature-v0.4-audio-snapper/docs/AGENTIC_LOOP_V0.6.0.md) — Comprehensive technical reference for the Multi-Turn Cognitive Loop Engine.
- [`docs/AUTONOMOUS_ORCHESTRATOR_V0.5.0.md`](file:///O:/Antigravity/alano-cut/.worktrees/codex-feature-v0.4-audio-snapper/docs/AUTONOMOUS_ORCHESTRATOR_V0.5.0.md) — Specification for the local GPU stack and autonomous orchestration.
