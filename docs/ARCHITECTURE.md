# Architecture - Alano Cut

Status: living document.  
Updated: 2026-07-11

## Overview

The runtime is a Python/FFmpeg helper suite directed by the product `AGENTS.md` and modular `.agents/` workflow. Source transcription comes from ElevenLabs Scribe; EDL JSON is the editable editorial contract; FCP7/XMEML is the final deliverable.

## Stack

- Python 3.10+ with `numpy`, `requests`, and `pillow`.
- FFmpeg/ffprobe for media extraction, waveform work, and XML source metadata.
- PowerShell installer and `alanocut` CLI for Windows workspace bootstrap/update.

## Modules

- `helpers/transcribe*.py` — cached Scribe transcripts.
- `helpers/pack_transcripts.py` — compact editorial reading view.
- `helpers/*edl*.py` — EDL validation, refinement, conversion, and XML round-trip.
- `helpers/render.py` — QA preview media only.
- `.agents/` — product workflow and completion gates copied into user workspaces.
- `bin/` and `install.ps1` — global install/update and workspace initialization.

## Boundaries

- Helpers may create artifacts only in a selected video's `edit/` directory.
- XML references original media; preview artifacts are QA-only.
- Private source media, API keys, transcripts, and temporary edit artifacts are never committed.
- Development routing lives in `DEV_AGENTS.md`; the shipped product routing remains in `AGENTS.md`.

## Testing & Parallelism

Unit tests must use generated synthetic media. Private lesson regression is opt-in through an environment variable and writes only to temporary directories. One Antigravity worker owns one worktree at a time; product-code tasks are sequential in the v0.4 branch because they share EDL/timing interfaces.

## Decisions

- 2026-07-11 - Development harness uses `DEV_AGENTS.md` to avoid changing the product `AGENTS.md` role contract.
- 2026-07-11 - v0.4 preview is audio-only; visual review is not a normative gate.
