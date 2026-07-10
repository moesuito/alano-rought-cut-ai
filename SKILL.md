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

Keep the hard scope: rough cut only. Deliver `<videos_dir>/edit/timeline.xml`; do not add finishing features or create a final high-quality render.
