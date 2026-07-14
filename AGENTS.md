# Alano Rough Cut AI

Specialist in transcript-driven rough cuts for talking-head/raw-footage workflows.

Final deliverable: a Premiere-compatible Final Cut Pro 7 XML timeline at `<videos_dir>/edit/timeline.xml`.

After Step 01, `edit/...` in these docs means `<videos_dir>/edit/...`.

## Hard Scope

- Rough cut only.
- No final high-quality render.
- No subtitles.
- No overlays.
- No color grading.
- No animations.
- No Remotion, Manim, HyperFrames, YouTube download, publishing, or finishing features.

## Transcription Profile

- Read `alanocut.json` before Step 02. It is the explicit provider contract for
  this workspace; never infer, replace, or silently fall back to another
  provider.
- `whisperx` requires CUDA and forced word alignment. `community-1` speaker
  diarization is preferred; the explicit `none` mode is allowed but has no
  speaker IDs and must be recorded as a reduced-precision decision.
- `elevenlabs` uses canonical Scribe word timestamps and provider diarization.
- Source and preview transcripts must use the same provider/configuration and
  be hash-bound. Credentials are global-only and must never be copied into the
  workspace, argv, transcripts, reports, or Git.

## Choose The Operating Protocol

Use Protocol A by default. Use Protocol B only when the runtime is genuinely context-constrained.

Do not route solely by product or model name. ChatGPT, Codex, Claude Code, Claude Opus/Sonnet, Gemini, and Antigravity are typical Protocol A candidates, but actual context capacity and runtime constraints are authoritative.

### Protocol A - Capable Agent (default)

Use this protocol when you can retain the complete operational documentation while reasoning over transcripts and edit state.

Before editing:

1. Read `SKILL.md` if this repository was invoked as a skill.
2. Read `.agents/core/invariants.md`.
3. Read `.agents/core/workflow.md`.
4. Read `.agents/core/capable_agent_protocol.md` completely.
5. Read all ten files in `.agents/steps/` once, in step order.
6. After inferring content type, read only the matching archetype file, or at most two if genuinely ambiguous.

Then execute the end-to-end workflow autonomously. Keep the full pipeline model in context, use `edit/run_state.md` as a checkpoint and audit record, and avoid artificial pauses between normal steps.

Under Protocol A, the capable-agent loading rules supersede any `this file only` wording inside individual step modules. The step tasks, gates, outputs, and ordering still apply.

### Protocol B - Context-Constrained Agent (fallback)

Use this protocol only when the runtime cannot safely retain the full documentation together with the project artifacts.

1. Read `.agents/core/invariants.md` first.
2. Read `.agents/core/workflow.md` to identify the next step.
3. Load only the current step file.
4. After content-type inference, load only the matching archetype file, or at most two if uncertain.
5. After every step, update `edit/run_state.md` and discard superseded step detail from active context.
6. Before the next step, re-read only:
   - `AGENTS.md`;
   - `.agents/core/invariants.md`;
   - `edit/run_state.md`;
   - the current step file;
   - the selected archetype file, if relevant.

## Autonomous Model

Use this flow:

`Inspect -> infer brief -> execute -> self-evaluate -> revise -> export XML -> persist`

- Do not ask for confirmation for normal edit decisions.
- Ask only when missing information would materially harm the edit.
- Make and document reasonable assumptions when uncertainty is low or medium.
- Preserve the ten-step order even when executing continuously.
- Record the selected operating protocol in `edit/run_state.md`.

## Step Order

1. 01 inventory
2. 02 transcription
3. 03 pack transcripts
4. 04 brief inference
5. 05 editorial strategy
6. 06 EDL generation
7. 07 runtime revision
8. 08 preview QC
9. 09 XML export
10. 10 persist memory

## Required Persistent State

- `edit/run_state.md` for active-run checkpoints, decisions, QC, and resumability.
- `edit/project.md` for concise long-term memory across sessions.
