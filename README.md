# alano-rought-cut-ai


Introducing **alano-rought-cut-ai** — a specialized AI assistant skill for rough video cutting and Adobe Premiere Pro timeline XML export.

This repository is a customized fork of the open-source [video-use](https://github.com/browser-use/video-use) project (all credits to the original creators at browser-use). It has been streamlined and adapted to act exclusively as a **Rough Cut Specialist**, discarding final rendering features, subtitles, color grading, overlays, and animations in favor of direct timeline integration with Premiere Pro.

The agent instructions are modular: start with `AGENTS.md`, then load only the current step under `.agents/`. The old `SKILL.md` is now only a compatibility stub for tools that still look for that file.

## What it does

- **Identifies and cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes.
- **Infers the video type and rough-cut structure** from the transcript before editing, instead of assuming a fixed format.
- **Compares repeated takes by meaning and delivery**, choosing the best version of each narrative beat.
- **Snaps cuts to word boundaries** and silence gaps using sub-second ASR timestamps.
- **Validates tight cuts against waveform energy**, so ASR timestamp drift does not become the only boundary signal.
- **Uses lightweight preview renders for QA**, including render-level cut checks before exporting XML.
- **Transcribes preview audio for content QC**, catching repeated lines, clipped phrases, leftover direction words, and semantic mismatches.
- **Generates a Final Cut Pro 7 XML timeline (`timeline.xml`)** ready to be imported directly into **Adobe Premiere Pro 2026**.
- **Names the Premiere XML sequence from context**, using names like `reels 35_cadastro_alano-cut` instead of a generic fixed timeline name.
- **Round-trips corrected Premiere XML back to EDL JSON** for comparison against the agent cut.
- **Maps audio to a single linked stereo track (A1)**, preventing Premiere Pro from importing duplicate mono tracks.
- **Renders quick, lightweight preview videos** for visual/audio boundary checks.
- **Persists session memory** in `project.md` so editing sessions can resume seamlessly.

## Installation (Windows PowerShell)

Install the assistant and the global CLI utility `alanocut` by running the following command in PowerShell:

```powershell
irm https://raw.githubusercontent.com/moesuito/alano-rought-cut-ai/main/install.ps1 | iex
```

*Note: Restart your terminal/IDE after installation to load the updated `PATH` environment variables.*

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
2. Configure your `ELEVENLABS_API_KEY` in the generated `.env` file.
3. Optionally add editing context in `raw_video/edit/USER_BRIEF.md` (target duration, audience, must keep/cut, pacing).
4. Open your AI agent (like Claude Code or Gemini), read `AGENTS.md`, and say: *"edit these clips"* or *"make a rough cut"*.


## How it works

The AI reads the video through two layers that give it word-boundary precision:

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite" width="100%">
</p>

1. **Audio Transcript (Layer 1)**: One ElevenLabs Scribe call per source gives word-level timestamps, speaker diarization, and audio events (`(laughter)`, `(sigh)`). All takes pack into a single ~12KB `takes_packed.md` — the LLM's primary reading view.
2. **Waveform Boundary QC (Layer 2)**: `validate_edl_boundaries.py` checks every EDL cut against both raw word timestamps and local waveform energy. Transcript-only flags are review signals; high-risk flags require both word and waveform evidence, or media-analysis failure.
3. **Visual Composite (Layer 3)**: `timeline_view.py` produces a filmstrip + waveform + word labels PNG for any time range. It is called at decision points like ambiguous pauses or cut-point sanity checks.
4. **Preview Transcript QC (Layer 4)**: `preview_transcript_qc.py` reviews the rendered preview transcript for duplicated content, leftover direction words, audio-event artifacts, and obvious semantic mismatches.

## Pipeline

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Boundary QC ──> Preview Render ──> Preview Transcript QC ──> FCP 7 XML Export
                                                           │                              │
                                                           └─ issue? fix + re-run QC/render
```

The self-eval loop runs boundary QC on every cut, uses `timeline_view` only on suspicious points, and can transcribe the preview to catch content-level problems before exporting `timeline.xml` for Premiere.

## QA helper commands

```powershell
.venv\Scripts\python.exe helpers\validate_edl_boundaries.py raw_video\edit\edl.json --transcripts raw_video\edit\transcripts -o raw_video\edit\edl_boundary_qc.json
.venv\Scripts\python.exe helpers\transcribe.py raw_video\edit\preview.mp4 --edit-dir raw_video\edit --force
.venv\Scripts\python.exe helpers\preview_transcript_qc.py raw_video\edit\transcripts\preview.json -o raw_video\edit\preview_transcript_qc.json
.venv\Scripts\python.exe helpers\edl_to_fcpxml.py raw_video\edit\edl.json -o raw_video\edit\timeline.xml --timeline-name "reels 35_cadastro_alano-cut"
.venv\Scripts\python.exe helpers\fcpxml_to_edl.py raw_video\edit\timeline_fix.xml -o raw_video\edit\timeline_fix_from_xml.edl.json --media-root raw_video
```

## License

This project is licensed under the MIT License (inherited from the original [video-use](https://github.com/browser-use/video-use) project).
