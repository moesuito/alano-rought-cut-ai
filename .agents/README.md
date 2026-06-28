# Modular Agent Instructions

This directory replaces the old monolithic `SKILL.md`.

Use it incrementally:

1. Read root `AGENTS.md`.
2. Read `.agents/core/invariants.md`.
3. Read `.agents/core/workflow.md`.
4. Load only the current step under `.agents/steps/`.
5. Load at most one matching archetype under `.agents/archetypes/`, or at most two if the material is genuinely ambiguous.
6. Persist state to `<videos_dir>/edit/run_state.md` before moving to the next step.

Do not load this entire directory into context. The design goal is to keep local/small LLM runs stable by moving detailed instructions into step-sized modules.
