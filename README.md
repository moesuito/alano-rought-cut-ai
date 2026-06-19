# alano-rought-cut-ai


Introducing **alano-rought-cut-ai** — a specialized AI assistant skill for rough video cutting and Adobe Premiere Pro timeline XML export.

This repository is a customized fork of the open-source [video-use](https://github.com/browser-use/video-use) project (all credits to the original creators at browser-use). It has been streamlined and adapted to act exclusively as a **Rough Cut Specialist**, discarding final rendering features, subtitles, color grading, overlays, and animations in favor of direct timeline integration with Premiere Pro.

## What it does

- **Identifies and cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes.
- **Infers the video type and rough-cut structure** from the transcript before editing, instead of assuming a fixed format.
- **Compares repeated takes by meaning and delivery**, choosing the best version of each narrative beat.
- **Snaps cuts to word boundaries** and silence gaps using sub-second ASR timestamps.
- **Bakes in 30ms audio fades** at every segment boundary to prevent clicks and pops in the timeline.
- **Generates a Final Cut Pro 7 XML timeline (`timeline.xml`)** ready to be imported directly into **Adobe Premiere Pro 2026**.
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
2. Copy the helper scripts and editing skill rules into your directory.
3. Automatically register the editing skill for Claude Code (`~/.claude/skills/video-use`) and Gemini (`~/.gemini/config/skills/video-use`) pointing to your current folder.

After running `init`:
1. Drop your raw video files inside `raw_video/`.
2. Configure your `ELEVENLABS_API_KEY` in the generated `.env` file.
3. Optionally add editing context in `raw_video/edit/USER_BRIEF.md` (target duration, audience, must keep/cut, pacing).
4. Open your AI agent (like Claude Code or Gemini) in the directory and say: *"edit these clips"* or *"make a rough cut"*.


## How it works

The AI reads the video through two layers that give it word-boundary precision:

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite" width="100%">
</p>

1. **Audio Transcript (Layer 1)**: One ElevenLabs Scribe call per source gives word-level timestamps, speaker diarization, and audio events (`(laughter)`, `(sigh)`). All takes pack into a single ~12KB `takes_packed.md` — the LLM's primary reading view.
2. **Visual Composite (Layer 2)**: `timeline_view.py` produces a filmstrip + waveform + word labels PNG for any time range. It is called at decision points like ambiguous pauses or cut-point sanity checks.

## Pipeline

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Preview Render ──> Self-Eval ──> FCP 7 XML Export
                                                                     │
                                                                     └─ issue? fix + re-render (max 3)
```

The self-eval loop runs `timeline_view` on the preview video at every cut boundary to catch visual jumps or audio pops. Once verified, it exports `timeline.xml` for Premiere.

## License

This project is licensed under the MIT License (inherited from the original [video-use](https://github.com/browser-use/video-use) project).
