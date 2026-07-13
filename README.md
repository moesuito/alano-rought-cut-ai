# alano-rought-cut-ai


Introducing **alano-rought-cut-ai** — a specialized AI assistant skill for rough video cutting and Adobe Premiere Pro timeline XML export.

Current release: **v0.4.0**.

This repository is a customized fork of the open-source [video-use](https://github.com/browser-use/video-use) project (all credits to the original creators at browser-use). It has been streamlined and adapted to act exclusively as a **Rough Cut Specialist**, discarding final rendering features, subtitles, color grading, overlays, and animations in favor of direct timeline integration with Premiere Pro.

The agent instructions use a capability-routed dual protocol. Capable agents read the complete operational workflow up front and execute end to end; context-constrained agents retain the modular, one-step-at-a-time fallback. `SKILL.md` and `AGENTS.md` route both kinds of agent explicitly.

## What it does

- **Identifies and cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes.
- **Infers the video type and rough-cut structure** from the transcript before editing, instead of assuming a fixed format.
- **Transcribes locally on NVIDIA CUDA** with faster-whisper `large-v3`, WhisperX forced word alignment, and Pyannote Community-1 exclusive speaker diarization.
- **Compares repeated takes by meaning and delivery**, choosing the best version of each narrative beat.
- **Turns every internal lexical gap strictly above 300ms into a jump cut**, with exact 300ms retention and narrow reasoned overrides for intentional pauses.
- **Validates tight cuts against waveform energy**, so ASR timestamp drift does not become the only boundary signal.
- **Uses lightweight preview renders for QA**, including render-level cut checks before exporting XML.
- **Always persists a timed preview-audio transcript bound to the WAV hash**, then validates every join for repeated, clipped, orphaned, crossed, or semantically wrong content.
- **Generates a Final Cut Pro 7 XML timeline (`timeline.xml`)** ready to be imported directly into **Adobe Premiere Pro 2026**.
- **Names the Premiere XML sequence from context**, using names like `reels 35_cadastro_alano-cut` instead of a generic fixed timeline name.
- **Round-trips corrected Premiere XML back to EDL JSON** for comparison against the agent cut.
- **Maps audio to a single linked stereo track (A1)**, preventing Premiere Pro from importing duplicate mono tracks.
- **Renders quick, lightweight preview audio (preview.wav)** for audio boundary checks, accompanied by a timeline mapping JSON file.
- **Persists session memory** in `project.md` so editing sessions can resume seamlessly.

## Installation (Windows PowerShell)

Install the assistant and the global CLI utility `alanocut` by running the following command in PowerShell:

```powershell
irm https://raw.githubusercontent.com/moesuito/alano-rought-cut-ai/main/install.ps1 | iex
```

*Note: Restart your terminal/IDE after installation to load the updated `PATH` environment variables.*

## Updating

If `alanocut` is already installed, update the global install from the latest GitHub release:

```powershell
alanocut update
```

`alanocut init` also checks for updates before initializing a workspace.

`alanocut update` refreshes the global installation under `%APPDATA%\alano-rought-cut-ai`. Existing workspaces keep their copied `AGENTS.md`, `.agents/`, helpers, and configuration until they are refreshed. After updating, run this once inside each existing workspace that should receive the new harness:

```powershell
alanocut init
```

This refresh preserves the workspace's `.env`, `raw_video/`, and `raw_video/edit/` contents.

## How to use (`alanocut init`)

Instead of cloning and registering the skill manually for each project, navigate to the folder containing your raw videos and run:

```powershell
alanocut init
```

This will:
1. Initialize the directory structure (`raw_video/` and `raw_video/edit/`).
2. Copy the helper scripts plus `AGENTS.md` and `.agents/` modular editing rules into your directory.
3. Automatically register the editing skill for Claude Code (`~/.claude/skills/video-use`) and Gemini (`~/.gemini/config/skills/video-use`) pointing to your current folder.

After running `init`:
1. Drop your raw video files inside `raw_video/`.
2. Accept the Community-1 terms on Hugging Face and configure `HF_TOKEN` in the generated `.env` file. The token is never placed in argv, transcripts, reports, or Git.
3. Optionally add editing context in `raw_video/edit/USER_BRIEF.md` (target duration, audience, must keep/cut, pacing).
4. Open your AI agent (like Claude Code or Gemini), read `AGENTS.md`, and say: *"edit these clips"* or *"make a rough cut"*.


## How it works

The agent uses an audio-only evidence stack for word-boundary precision:

1. **Source transcripts**: a shared Python 3.12 runtime runs faster-whisper `large-v3` on CUDA, WhisperX forced alignment, and `pyannote/speaker-diarization-community-1`. A pinned, windowed `small` verifier may recover recording cues only after two-window consensus; ordinary verifier text is never copied. Canonical schema-v1 transcripts require a positive aligned interval and speaker on every word. A transcript with only auditable unattributed acoustic components is cached provisionally until the EDL interval audit; selected short inter-word residuals must also be cleared by the independent preview transcription. Packed takes remain the model's primary editorial reading view.
2. **Exact boundary refinement**: `refine_edl_boundaries.py` first splits every canonical consecutive-word gap strictly above 300ms, then combines lexical anchors, raw max-per-channel waveform evidence, and RNNoise to write exact `source_in_frame` / `source_out_frame` values and a hash-bound report.
3. **Dry preview and audio QC**: `render.py` creates PCM16/48 kHz stereo `preview.wav` plus `preview_timeline.json`; `preview_audio_qc.py` validates every entry/join for inactivity, attack/tail safety, residual activity, clipping, and pops.
4. **Content coverage**: `semantic_qc.py` validates `metadata.required_beats` against words actually selected from source transcripts.
5. **Join transcript QC**: the preview is always re-transcribed by the same local aligned/diarized stack and persisted with its WAV hash. `preview_transcript_qc.py` compares the expected left suffix/right prefix at every mapped join, audits the fixed internal-silence contract, and uses global similarity/recall only as supplemental evidence.
6. **Readiness and XML**: `verify_edit_ready.py` must return exit code 0 for the exact current artifacts before the agent calls `edl_to_fcpxml.py`.

`timeline_view.py` and `validate_edl_boundaries.py` are legacy manual diagnostics outside the agent workflow and are scheduled for removal in v0.5.0.

## Pipeline

```
Local CUDA WhisperX -> Pack -> Editorial EDL -> Refine exact frames -> WAV/map -> Audio QC -> Semantic QC
                                      ^                                      |
                                      |                                      v
                                      +-- EDL change <- Persist preview transcript/hash -> Join transcript QC -> Readiness(0) -> XML
```

Any EDL change invalidates downstream artifacts and restarts the chain at boundary refinement. The normative agent path uses no fades, video frames, or visual inspection.

## Agent protocols

- **Protocol A — capable agent (default):** read the invariants, workflow, unified capable-agent protocol, and all ten step modules before editing. Keep the end-to-end model in context and execute continuously while checkpointing `run_state.md`.
- **Protocol B — context-constrained fallback:** load one step module at a time and use `run_state.md` as the memory bridge.

Routing is based on real context capacity, not a brittle model-name allowlist. ChatGPT, Codex, Claude Code, Claude Opus/Sonnet, Gemini, and Antigravity are typical Protocol A candidates. Both protocols keep archetype loading selective and produce the same artifacts and QC gates.

The protocols differ only in context strategy. Core invariants, workflow, step modules, gates, helpers, artifacts, QC, and completion criteria are shared and normative for every agent.

## What's new in v0.4.0

- Added a strict quality gate script (`verify_edit_ready.py`) run before XML export.
- Support for `source_in_frame` / `source_out_frame` mapping inside EDL ranges and XML conversion for precise cut alignment.
- Switched workflow to be audio-only (`preview.wav` and `preview_timeline.json`), rejecting `.mp4` visual renders.
- Made boundary refinement, audio QC, required-beat QC, persisted preview transcription, and join-centric transcript QC mandatory and hash-bound.
- Made every internal lexical gap strictly above 300ms an automatic, readiness-enforced jump cut.
- Marked `timeline_view.py` as legacy, scheduled for removal in v0.5.0.

## What shipped in v0.3.0

- Full-context execution is now the default for capable agents.
- The original modular one-step-at-a-time workflow remains available for context-constrained agents.
- Both protocols now share one normative rule set and identical completion gates.

## QA helper commands

```powershell
alanocut setup-transcription
alanocut transcription-doctor
.venv\Scripts\python.exe helpers\transcribe_batch.py raw_video --provider whisperx --language pt --model large-v3 --batch-size 2
.venv\Scripts\python.exe helpers\refine_edl_boundaries.py raw_video\edit\edl.json --transcripts raw_video\edit\transcripts --report raw_video\edit\edl_boundary_qc.json
.venv\Scripts\python.exe helpers\render.py raw_video\edit\edl.json -o raw_video\edit\preview.wav --timeline-map raw_video\edit\preview_timeline.json
.venv\Scripts\python.exe helpers\preview_audio_qc.py raw_video\edit\preview.wav --timeline-map raw_video\edit\preview_timeline.json --edl raw_video\edit\edl.json --output raw_video\edit\preview_audio_qc.json
.venv\Scripts\python.exe helpers\semantic_qc.py raw_video\edit\edl.json --transcripts raw_video\edit\transcripts --output raw_video\edit\edl_semantic_qc.json
.venv\Scripts\python.exe helpers\preview_transcript_qc.py raw_video\edit\preview.wav --provider whisperx --audio raw_video\edit\preview.wav --edl raw_video\edit\edl.json --transcripts raw_video\edit\transcripts --timeline-map raw_video\edit\preview_timeline.json --transcript-output raw_video\edit\transcripts\preview.json --output raw_video\edit\preview_transcript_qc.json
.venv\Scripts\python.exe helpers\verify_edit_ready.py raw_video\edit\edl.json --transcripts raw_video\edit\transcripts --boundary-report raw_video\edit\edl_boundary_qc.json --audio-report raw_video\edit\preview_audio_qc.json --semantic-report raw_video\edit\edl_semantic_qc.json --transcript-report raw_video\edit\preview_transcript_qc.json --audio raw_video\edit\preview.wav --timeline-map raw_video\edit\preview_timeline.json
.venv\Scripts\python.exe helpers\edl_to_fcpxml.py raw_video\edit\edl.json -o raw_video\edit\timeline.xml --timeline-name "reels 35_cadastro_alano-cut"
.venv\Scripts\python.exe helpers\fcpxml_to_edl.py raw_video\edit\timeline_fix.xml -o raw_video\edit\timeline_fix_from_xml.edl.json --media-root raw_video
```

## License

This project is licensed under the MIT License (inherited from the original [video-use](https://github.com/browser-use/video-use) project).
