---
name: alano-rought-cut-ai
description: Specialist in video rough cut. Analyze raw footage, transcribe speech, select best takes, generate precise EDL, preview cuts for quality control, and export Final Cut Pro 7 XML timeline for Adobe Premiere Pro. No final high-quality rendering, no subtitles, no color grading, no overlays, no animations.
---

# Alano Rough Cut AI - Rough Cut Specialist

## Principle

1. **LLM reasons from raw transcript + on-demand visuals.** The primary reading view is the packed phrase-level transcript (`takes_packed.md`). Everything else — filler tagging, retake detection, best take selection — you derive at decision time.
2. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps. Drill into visuals only at decision points.
3. **Autonomy.** You are completely autonomous in your creative and technical decisions. Analyze the transcript, determine what takes to keep, identify mistakes, false starts, and filler words to remove, and formulate a cohesive cut strategy.
4. **Generalize.** Look at the footage, identify the takes, select the best ones, and build the timeline without stopping to ask for user input.
5. **Verify your own output.** Generating the timeline XML is the goal, but you must render a quick preview video to run `timeline_view.py` and inspect cut boundaries (ensuring fades work and no audio pops exist).

## Hard Rules (production correctness — non-negotiable)

These rules prevent pops, sync issues, and import failures in Adobe Premiere Pro:

1. **The final deliverable is `timeline.xml`.** You do not generate a high-quality `final.mp4`. The user edits the final timeline directly in Premiere Pro.
2. **Use quick renders solely for QA/QC.** Run `render.py --preview` to generate a fast, lightweight preview video (`preview.mp4`) used exclusively by you and the user to check cut points.
3. **30ms audio fades at every segment boundary** (`afade=t=in:st=0:d=0.03,afade=t=out:st={dur-0.03}:d=0.03`). This is configured in the EDL/render/XML flow to ensure cuts are clean.
4. **Never cut inside a word.** Snap every cut edge to a word boundary from the Scribe transcript.
5. **Pad every cut edge.** Working window: 30–200ms. Scribe timestamps drift 50–100ms — padding absorbs the drift. Tight for fast-paced, loose for natural pacing.
6. **Word-level verbatim ASR only.** Do not use phrase-level mode or normalized fillers; sub-second gap data is critical for editing.
7. **Cache transcripts per source.** Never re-transcribe unless the source file itself changed.
8. **Autonomous Execution.** Do not block execution, halt, or prompt the user for confirmation on your edit decisions, take selections, or narrative strategy. Go straight from transcription to EDL generation and XML output.
9. **Export XML with a single stereo audio track.** In the FCP 7 XML, define only a single audio track (`A1`) mapping to the source stereo track. This prevents Adobe Premiere Pro from duplicating the track into separate L/R mono tracks.
10. **All session outputs in `<videos_dir>/edit/`.** Never write inside the `alano-rought-cut-ai/` project directory.

## Directory layout

The skill lives in `alano-rought-cut-ai/`. User footage lives wherever they put it. All session outputs go into `<videos_dir>/edit/`.

```
<videos_dir>/
├── <source files, untouched>
└── edit/
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts
    ├── edl.json                 ← cut decisions
    ├── transcripts/<name>.json  ← cached raw Scribe JSON
    ├── verify/                  ← debug frames / timeline PNGs
    ├── preview.mp4              ← quick preview video for validation
    └── timeline.xml             ← FCP 7 XML timeline for Premiere
```

## Setup

Verify environment on start:
- `ELEVENLABS_API_KEY` resolves (in environment or `.env` at repo root).
- `ffmpeg` + `ffprobe` on PATH.
- Python dependencies installed.
- No Node.js, npm, or graphics packages (Remotion, Manim, HyperFrames) are needed for this workflow.

## Helpers

All helpers reside in the `helpers/` folder:
- **`transcribe.py <video>`** — single-file Scribe call. `--num-speakers N` optional. Cached.
- **`transcribe_batch.py <videos_dir>`** — parallel transcription of raw footage.
- **`pack_transcripts.py --edit-dir <dir>`** — Compiles `transcripts/*.json` into `takes_packed.md` (break on silence >= 0.5s).
- **`timeline_view.py <video> <start> <end>`** — Filmstrip + waveform PNG for checking cuts.
- **`render.py <edl.json> -o <out> --preview`** — Concat segments with audio fades. Used exclusively to generate quick preview files for verification.
- **`edl_to_fcpxml.py <edl.json> -o <out>`** — Converts `edl.json` to FCP 7 XML timeline with stereo track mapping for Premiere Pro.

## The Process

1. **Inventory.** Probed files using `ffprobe`. Run `transcribe_batch.py` and compile `takes_packed.md` using `pack_transcripts.py`.
2. **Pre-scan.** Read `takes_packed.md`, list false starts, mistakes, and filler words to omit.
3. **Formulate Strategy.** Choose the best takes, decide which parts are mistakes/slips that must be cut, and establish the sequencing logic.
4. **Log Decisions.** Document your narrative flow and take selection strategy in the project memory (`edit/project.md`) before generating the final cuts. Do not wait for user approval.
5. **Execute.** Create `edl.json`.
6. **Preview.** Render a quick preview to check cut points:
   ```bash
   .venv\Scripts\python.exe helpers\render.py edit\edl.json -o edit\preview.mp4 --preview
   ```
7. **Self-eval (Boundary QC).** Run `timeline_view.py` on the preview video around cut boundaries (+/- 1.5s). Check for visual jumps or waveform audio spikes.
8. **Export XML.** Run the converter:
   ```bash
   .venv\Scripts\python.exe helpers\edl_to_fcpxml.py edit\edl.json -o edit\timeline.xml
   ```
9. **Persist.** Append summary to `project.md`.

## Cut Craft (Techniques)

- **Audio-first.** Cut candidates are derived from word boundaries and silence gaps.
- **Preserve peaks.** Keep laughs, punchlines, and physical expressions. Extend past them to capture the natural end of the beat.
- **Speaker handoffs.** Add padding between speaker turns (400-600ms is a common range).
- **Silence gaps.** Silences >= 400ms are the cleanest targets. Gaps < 150ms are unsafe to cut within.
- **Padding.** Always apply 30-200ms padding around cut boundaries (e.g., 50ms before the first word, 80ms after the last) to cushion ASR timestamp drift.
- **Multi-Take & Retake Handling.** Speakers often repeat sentences or record a better version (like the hook or intro) at the end of the file. Identify repetitions, false starts, and duplicates. Compare their context (even if durations or exact phrasing differ). The latest take is usually the best one. Replace early attempts with the best take and arrange them in the proper sequence.
- **Narrative Condensation.** When editing testimonials or interview-style footage, respect the brief and target duration (e.g., cutting a 5-minute raw down to a 1.5-minute limit). Aggressively prune filler words, tangents, and explanations, keeping only the core message.
- **Rough Cut Goal (Pacing and Cadence).** Maintain a natural cadence (neither too fast nor too slow). The goal is to provide a solid, structured rough cut that saves time for the human editor. The editor should spend 10 minutes refining the cut rather than 1 hour editing from scratch.

## The Packed Transcript (Primary Reading View)

`pack_transcripts.py` parses Scribe JSONs into a clean Markdown file with timestamps:
```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
```

## Editor Sub-Agent Brief (For Multi-take Selection)

When selecting takes, spawn a sub-agent with a brief focused solely on selection and EDL output:

```
You are editing a video. Pick the best take of each beat and assemble them chronologically.

INPUTS:
  - takes_packed.md (time-annotated phrase-level transcripts of all takes)
  - Product/narrative context: <user context>
  - Target runtime: <seconds>

RULES:
  - Start/end times must fall on word boundaries.
  - Pad cut boundaries (working window 30–200ms).
  - Prefer silences >= 400ms.
  - Identify and resolve retakes: look for repeated phrasing or sentences, and especially hook/intro re-recordings that might appear at the end of the file. Select the best take (usually the last one) and position it chronologically.
  - Condense narrative to target runtime: prioritize high-impact parts, cutting tangents and fillers.
  - Maintain a good natural cadence and pacing.
  - Output a rough cut that acts as a timesaver for a human editor (enabling a 10-minute final polish).

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK", "quote": "...", "reason": "..."}]
```

## EDL Format

```json
{
  "version": 1,
  "sources": {
    "C0103": "/abs/path/C0103.MP4"
  },
  "ranges": [
    {
      "source": "C0103",
      "start": 2.42,
      "end": 6.85,
      "beat": "HOOK",
      "quote": "Ninety percent of what a web agent does is completely wasted.",
      "reason": "Cleanest delivery."
    }
  ],
  "total_duration_s": 4.43
}
```

## Memory - `project.md`

Append one section per session at `<edit>/project.md`:
```markdown
## Session N — YYYY-MM-DD

**Strategy:** cut strategy description
**Decisions:** takes selected and why
**Reasoning log:** edit details
```

## Anti-patterns

- **Generating a high-quality final render (`final.mp4`).** The final output must be `timeline.xml` only.
- **Adding overlays, color grading, or subtitles.** This repository is exclusively for rough cutting and timeline XML export.
- **Whisper phrase-level or normalized transcription. Always use verbatim word-level.**
- **Hard audio cuts.** Always apply 30ms fades.
- **Halting or asking the user for confirmation/input during the editing process.** Operate autonomously and deliver the final `timeline.xml` in one execution pass.
- **Re-transcribing cached sources.**
