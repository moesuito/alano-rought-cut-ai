---
name: alano-rought-cut-ai
description: Create transcript-driven rough cuts from talking-head or raw video, self-evaluate cut boundaries and content, and export a Premiere-compatible Final Cut Pro 7 XML timeline. Use when an agent needs to inspect raw footage, resolve retakes, build an editorial EDL, run preview QC, or produce timeline.xml for Adobe Premiere Pro.
---

# Alano Rough Cut AI

Read `AGENTS.md` first and follow its capability-based protocol router.

Use the capable-agent protocol by default. Treat ChatGPT, Codex, Claude Code, Claude Opus/Sonnet, Gemini, and Antigravity as likely capable candidates, but choose based on actual context capacity rather than brand name.

- Capable/default: read `.agents/core/capable_agent_protocol.md`, core rules, and all step modules before executing the complete workflow.
- Context-constrained fallback: load one step module at a time and bridge steps through `edit/run_state.md`.

For both protocols, treat core invariants, workflow, step modules, gates, helpers, artifacts, QC, and completion criteria as one shared normative standard.

Before editorial selection, read the workspace `alanocut.json` and use its one
configured provider for both source and preview transcripts. Local WhisperX
requires CUDA and forced-aligned words; Community-1 speakers are preferred but
the explicit no-diarization profile is allowed. ElevenLabs Scribe requires
provider word timestamps and diarization. No provider may silently fall back.
After editorial selection, the mandatory product chain is: refine exact
boundaries -> render dry WAV/map -> audio QC -> required-beat semantic QC ->
force and persist a canonical provider-bound preview transcript with its WAV
hash -> join-centric preview transcript QC -> readiness exit code 0 -> XML.
Missing, stale, review, or failed evidence blocks the agent before XML.

Keep the hard scope: rough cut only. The agent-facing QA path is audio-only: no fades, video frames, visual inspection, or final render. `timeline_view.py` is a legacy manual diagnostic outside the workflow and is scheduled for removal in v0.5.0. Deliver `<videos_dir>/edit/timeline.xml`; do not add finishing features or create a final high-quality render.
