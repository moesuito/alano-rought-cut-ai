# Step 01 - Inventory

Goal: identify the source footage, determine `<videos_dir>`, and initialize run state.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `.agents/core/workflow.md`
- this file only

## Tasks

1. Determine `<videos_dir>` as the directory containing the raw video files.
2. Identify raw source files. Do not modify them.
3. Verify local execution prerequisites:
   - `ffmpeg` is available;
   - `ffprobe` is available;
   - Python dependencies are installed in the workspace environment;
   - no Node.js, npm, Remotion, Manim, or HyperFrames packages are required for this workflow.
4. Probe each source with `ffprobe` for duration, streams, frame rate, resolution, and audio presence.
5. Create `<videos_dir>/edit/` if missing.
6. Initialize `<videos_dir>/edit/run_state.md` from `.agents/state/run_state.template.md` if missing.
7. Record:
   - videos dir;
   - edit dir;
   - source list;
   - basic metadata;
   - current step status.

## Output

- `<videos_dir>/edit/`
- updated `edit/run_state.md`

Do not perform transcription or editorial decisions in this step.
