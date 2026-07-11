<!-- Phase-as-one-task template (Phase-Sequence Exception). The ORCHESTRATOR copies this to the worktree-root task.md ONLY when the user explicitly approves running a whole phase as one active task. Clear and precise — NOT caveman. -->

# Active Phase Task - <FX phase name>

Updated: 2026-07-11
Status: active; user-approved ordered phase sequence.

- Phase/Task ID: <FX (FX.0-FX.N)>
- Branch: `feature/fX-<name>`
- Base branch: `main`
- Worktree path: `<absolute path, e.g. C:\path\to\repo\.worktrees\feature\fX-<name>>`
- Depends on: <approved prior baseline, e.g. F0>
- Difficulty: <N>/10
- Layer: <fullstack | ...>
- Requirement: <PRD IDs, e.g. PROJ-01, SEC-01>
- File ownership: whole branch
- Next executable task: <FX.Y>

## Goal

Execute the complete <FX> phase in this worktree, working the ordered subtasks one at a time without switching branches or requesting a new handoff while each dependency and required validation passes.

## Branch And Worktree Contract

- Operate only in branch `feature/fX-<name>`, only inside the absolute worktree path above.
- The worktree contains the tracked `main` baseline plus accepted phase work. Do not read or modify the main workspace or sibling worktrees.
- Subtasks marked completed are done — do not reimplement or amend their commits.
- Execute the queued subtasks in order. Before each, read only its matching versioned task document.
- One local Conventional Commit per subtask. After a subtask passes its validation, write `docs/scratches/<id>-report.md` and **wait for the ORCHESTRATOR to confirm** before the next.
- Never edit documentation (except your own `docs/scratches/<id>-report.md`), push, merge, resolve conflicts, or manage worktrees.

## Ordered Individual Tasks

| ID | Task | Depends on | Status | Versioned task document |
| --- | --- | --- | --- | --- |
| FX.0 | <preflight> | <baseline> | completed | `docs/active/feature/fX-<name>/fX.0-*.md` |
| FX.1 | <task> | FX.0 | active | `docs/active/feature/fX-<name>/fX.1-*.md` |
| FX.2 | <task> | FX.1 | queued | `docs/active/feature/fX-<name>/fX.2-*.md` |

## Worker Read List

Read first: `AGENTS.md`, `WORKER.md`, this file, `docs/active/feature/fX-<name>/implementation-plan.md`. Then read only the matching subtask document immediately before implementing it. Do not read future-phase plans, archives, memory files, the main workspace, or sibling worktrees.

## Scope

In scope:

- behavior specified by the queued FX.Y task documents;
- focused tests and validation each subtask requires;
- small tactical refactors needed to connect consecutive subtasks.

Out of scope:

- later-phase behavior; documentation edits (except your scratch report); architecture changes; release/distribution;
- push, PR, merge, conflict resolution, or worktree management.

## Stop And Ask

Stop the sequence and report if: git does not report the branch/worktree above; a dependency is incomplete or validation fails after in-scope fixes; implementation needs an approved-architecture or scope change; or a merge conflict / push / PR / merge / sibling-worktree access is required.

## Required Validation

Before starting the next subtask:

```powershell
git status --short --branch
git worktree list
```

Run every command and focused test each subtask document requires before committing and continuing. After the final subtask, run the full phase gate:

```powershell
python -m compileall -q helpers
python helpers/edl_to_fcpxml.py --help
python helpers/validate_edl_boundaries.py --help
```

## Completion

- One local commit per implemented subtask; one `docs/scratches/<id>-report.md` per subtask; wait for ORCHESTRATOR confirmation between subtasks.
- Never edit other docs, push, merge, resolve conflicts, or remove the worktree.
- After the final subtask or a blocking stop, send one `WORKER:` report for the ORCHESTRATOR to read directly (changed areas, requirement IDs, validation facts, every commit hash, blockers, docs/status the ORCHESTRATOR should update).
