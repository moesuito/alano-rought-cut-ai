<!-- Quick Mode task doc. Seeded by `new-worktree.ps1 -Quick` into docs/active/<branch>/. Replaces implementation-plan.md for small, low-risk changes. Clear and precise — NOT caveman. -->

# Quick Task - <name>

Status: draft.

- Branch: <fix/... or chore/...>
- Worktree path: <.worktrees/<branch>>
- Difficulty: <N>/10

## Quick Mode guardrails (hard)

Quick Mode is the express lane: lightweight branch, no formal implementation plan or task breakdown — but **still commit + PR + merge + teardown** (the git flow never relaxes). It is allowed only when ALL hold:

- ≤ 3 files touched;
- one-sentence scope, no architectural decision, no new dependency;
- ≤ 5 obvious steps.

**Safety valve:** if reality breaks any of these (more files, a design decision, >5 steps), STOP and promote to a Standard feature — create `implementation-plan.md` + task docs. Do not power through as a quick task.

## Description

One sentence: what and why.

## Files

- `<path>` — `<what changes>`

## Verification

- [ ] `<how to confirm it works>`
- [ ] required validation passes (see `AGENTS.md`)

## Done

- Commit: `<hash>` `<conventional message>`
- Reported to ORCHESTRATOR: changed areas, validation, commit, docs to update.
