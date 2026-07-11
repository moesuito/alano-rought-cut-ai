<!-- Per-branch implementation plan. Lives in docs/active/<branch>/; archived to docs/closed/<branch>/ at merge. Clear and precise — NOT caveman. -->

# Implementation Plan - <branch>

Status: planning.
Phase: <F1>
Base branch: <main or feature/<epic>>
Worktree: <.worktrees/<branch>>

## Goal

What this branch delivers, and how it maps to `PRD.md` / `ARCHITECTURE.md`.

## Task List (ordered, dependency-aware)

Later tasks depend on earlier ones. Each task gets its own doc and a row here.

| ID | Task | Layer | Difficulty | Requirement | Depends on | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F1.1 | `<task>` | backend | <N>/10 | AUTH-01 | - | planned |
| F1.2 | `<task>` | frontend | <N>/10 | AUTH-01 | - | planned |
| F1.x | Integration: wire it together | fullstack | <N>/10 | AUTH-01 | F1.1, F1.2 | planned |

## Requirement Traceability

Trail from PRD requirement → task → status. Every requirement in scope must map to at least one task.

| Requirement | Tasks | Status |
| --- | --- | --- |
| AUTH-01 | F1.1, F1.2 | Pending / In Tasks / Implementing / Verified |

## Multi-Worker Layout (if any)

- Epic mother branch: `<feature/epic, or n/a>`
- Sub-branches: `<feature/part-a, feature/part-b>`
- Or hybrid co-location with `File ownership` per task.

## Acceptance

- `<how we know this branch is done>`

## Risks

- `<risk>` — `<mitigation>`
