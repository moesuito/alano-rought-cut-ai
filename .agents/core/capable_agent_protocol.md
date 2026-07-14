# Capable Agent Protocol

Use this protocol to operate the existing Alano workflow with full operational context and continuous end-to-end reasoning.

## Authority And Parity

This file controls context loading and orchestration only. It does not define a separate editing standard.

The normative rules for every agent are:

1. `.agents/core/invariants.md`;
2. `.agents/core/workflow.md`, including shared completion criteria;
3. `.agents/steps/01_inventory.md` through `10_persist_memory.md`;
4. the selected archetype file, when relevant.

Protocol A and Protocol B must execute the same tasks, gates, helpers, artifacts, QC loop, and completion criteria. If this overview ever conflicts with a normative file, follow the normative file and correct this overview.

## Full-Context Loading

Before editing:

1. Read `SKILL.md` and `AGENTS.md` when this repository is invoked as a skill.
2. Read `.agents/core/invariants.md` and `.agents/core/workflow.md` completely.
3. Read all ten step modules once in order.
4. Infer the content type from project material.
5. Read only the matching archetype, or at most two when the material is genuinely ambiguous.

The individual modules may say `this file only`; under Protocol A that wording describes the constrained loading path and does not require discarding already loaded workflow context.

## Execution Model

Execute the ten steps in order without artificial approval pauses between normal decisions:

| Step | Global purpose | Primary outcome |
|---|---|---|
| 01 | Establish sources, paths, prerequisites, and state | inventoried workspace |
| 02 | Create reusable word-level source transcripts | `edit/transcripts/*.json` |
| 03 | Build the compact reading map | `edit/takes_packed.md` |
| 04 | Resolve explicit or inferred editorial intent | brief and naming state |
| 05 | Form the story strategy | narrative plan and pre-scan |
| 06 | Select source ranges using editorial judgment | `edit/edl.json` |
| 07 | Reconcile duration with meaning | runtime-checked EDL |
| 08 | Test boundaries and rendered content, then revise | passed QC artifacts |
| 09 | Translate the EDL into an editable Premiere timeline | `edit/timeline.xml` |
| 10 | Preserve continuity and hand off the result | `edit/project.md` and final report |

Keep downstream constraints visible while making upstream decisions. For example, anticipate boundary QC while selecting ranges and anticipate XML source references while building the EDL. Do not skip or reorder the normative steps.

## Checkpointing And Recovery

- Update `edit/run_state.md` after material decisions and step transitions even when executing continuously.
- Treat state as an audit and recovery surface, not as a replacement for the full workflow model.
- On interruption, resume from the first incomplete gate recorded in state rather than restarting completed work.
- Read large source artifacts on demand: packed transcript for overview, canonical provider JSON for exact timing/speakers, and audio QC evidence for ambiguity.
- Complete only when the shared completion criteria in `.agents/core/workflow.md` are satisfied.
