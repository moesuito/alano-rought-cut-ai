# Context Policy

Use the capability router in `SKILL.md`. Protocol A is the default; Protocol B is a fallback for genuinely constrained runtimes. Root `AGENTS.md` contains repository engineering constraints shared by both protocols.

## Protocol A - Full Operational Context

- Read `.agents/core/invariants.md`, `.agents/core/workflow.md`, `.agents/core/capable_agent_protocol.md`, and all ten step modules before editing.
- Retain the end-to-end pipeline model so upstream editorial decisions account for downstream runtime, QC, and XML constraints.
- Load only the selected archetype after content-type inference; do not preload every archetype.
- Use `edit/run_state.md` as a checkpoint, audit record, and recovery surface, not as a substitute for understanding the complete workflow.
- Read source artifacts on demand: `takes_packed.md` for overview, canonical provider JSON for exact word boundaries/speakers, and audio QC/join reports for boundary evidence. The normative agent path does not inspect video frames.

## Protocol B - Progressive Context

- Never load the entire `.agents/` directory.
- Never load all archetypes.
- Never keep superseded step details in active context once the step is complete.
- Persist conclusions to `edit/run_state.md` before moving on.
- Use `edit/run_state.md` as the working-memory bridge between steps.
- Use `edit/project.md` as long-term memory across sessions.
- When context feels crowded, summarize current decisions into `edit/run_state.md`, then continue from the next step.
- Prefer reading source artifacts on demand:
  - `takes_packed.md` for transcript overview;
  - canonical provider JSON only when exact word timestamps or speakers are needed;
  - `preview_timeline.json`, audio QC, and timed preview transcript evidence for cut-boundary QA.

`timeline_view.py` is a legacy manual diagnostic outside both protocols and is pending dependency-aware removal.

## Shared Rule

Do not choose a protocol solely from a model-name allowlist. Model families and product labels change. Use Protocol A unless the runtime exposes a real context limitation or full loading would crowd out the project artifacts required to edit accurately.
