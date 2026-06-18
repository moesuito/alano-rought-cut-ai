<p align="center">
  <img src="static/video-use-banner.png" alt="alano-rought-cut-ai" width="100%">
</p>

# alano-rought-cut-ai

Introducing **alano-rought-cut-ai** — a specialized AI assistant skill for rough video cutting and Adobe Premiere Pro timeline XML export.

This repository is a customized fork of the open-source [video-use](https://github.com/browser-use/video-use) project (all credits to the original creators at browser-use). It has been streamlined and adapted to act exclusively as a **Rough Cut Specialist**, discarding final rendering features, subtitles, color grading, overlays, and animations in favor of direct timeline integration with Premiere Pro.

## What it does

- **Identifies and cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes.
- **Snaps cuts to word boundaries** and silence gaps using sub-second ASR timestamps.
- **Bakes in 30ms audio fades** at every segment boundary to prevent clicks and pops in the timeline.
- **Generates a Final Cut Pro 7 XML timeline (`timeline.xml`)** ready to be imported directly into **Adobe Premiere Pro 2026**.
- **Maps audio to a single linked stereo track (A1)**, preventing Premiere Pro from importing duplicate mono tracks.
- **Renders quick, lightweight preview videos** for visual/audio boundary checks.
- **Persists session memory** in `project.md` so editing sessions can resume seamlessly.

## Setup prompt

Paste into Gemini, Claude Code, or any agent with shell access:

```text
Set up https://github.com/Alano/alano-rought-cut-ai for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

The agent handles dependencies, skill registration, and prompts you once for your ElevenLabs API key (obtainable at [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)).

Then point your agent at a folder of raw footage:

```bash
cd /path/to/your/videos
claude    # or codex, hermes, gemini, etc.
```

And command:

> edit these into a rough cut

It inventories the sources, proposes a strategy, waits for your approval, and outputs the `edit/timeline.xml` timeline to import into Premiere Pro.

## Manual install

If you'd rather do it by hand:

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/Alano/alano-rought-cut-ai ~/Developer/alano-rought-cut-ai
ln -sfn ~/Developer/alano-rought-cut-ai ~/.claude/skills/video-use        # Claude Code

# 2. Install deps
cd ~/Developer/alano-rought-cut-ai
uv sync                         # or: pip install -e .
winget install FFmpeg           # Windows
```

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
