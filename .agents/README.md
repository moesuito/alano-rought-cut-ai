# Agent Instructions

Start with root `AGENTS.md`, which selects one of two operating protocols.

## Default: capable agent

1. Read `.agents/core/invariants.md`.
2. Read `.agents/core/workflow.md`.
3. Read `.agents/core/capable_agent_protocol.md`.
4. Read all ten `.agents/steps/` modules once in order.
5. Infer the content type, then load only the matching archetype, or at most two if genuinely ambiguous.
6. Execute continuously and persist checkpoints to `<videos_dir>/edit/run_state.md`.

## Fallback: context-constrained agent

1. Read root `AGENTS.md`.
2. Read `.agents/core/invariants.md`.
3. Read `.agents/core/workflow.md`.
4. Load only the current step under `.agents/steps/`.
5. Load at most one matching archetype, or two if genuinely ambiguous.
6. Persist state before moving to the next step.

The capable path optimizes global reasoning and speed. The fallback path preserves reliability when context is limited. Core invariants, workflow, step modules, gates, helpers, artifacts, QC, and completion criteria are normative for both paths.
