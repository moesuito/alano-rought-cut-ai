<!-- Clear reference material. Do NOT write this doc in caveman — losing a scope boundary or acceptance criterion is unacceptable. -->

# Task - <name>

Status: draft.

- Phase/Task ID: <e.g. F1.2>
- Branch: <feature/...>
- Base branch: <main or feature/<epic>>
- Worktree path: <absolute path, e.g. C:\path\to\repo\.worktrees\<branch>>
- Depends on: <task IDs, or none>
- Next executable task: <task/subtask ID to start, e.g. F1.2>
- Difficulty: <N>/10
- Layer: <frontend | backend | fullstack | infra>
- Requirement: <PRD requirement IDs this task satisfies, e.g. AUTH-01>
- File ownership: <paths this worker owns; only when co-located with another worker on the same branch, else "whole branch">

## Goal

Describe the outcome in one or two sentences.

## Acceptance Criteria (EARS)

Testable criteria tied to the requirement IDs. Each becomes at least one test.

- WHEN `<event>` THEN system SHALL `<behavior>`.
- WHEN `<edge case>` THEN system SHALL `<graceful handling>`.

## Worker Read List

Read only:

- `<focused-doc-or-file>`

Do not read broad docs, old plans, or future task docs unless blocked.

## Scope

In scope:

- `<specific behavior>`

Out of scope:

- `<future work or adjacent feature>`

## Stop And Ask

The WORKER must stop and ask the ORCHESTRATOR if:

- implementation requires scope beyond this task;
- a product/architecture choice is missing;
- it is not in the branch/worktree named above (or node_modules is missing);
- required validation fails for a reason that is not clearly in scope;
- push, merge, conflict resolution, release, or production promotion seems necessary.

## Required Validation

```powershell
python -m compileall -q helpers
python helpers/edl_to_fcpxml.py --help
python helpers/validate_edl_boundaries.py --help
```

Add focused tests for behavior changed by this task.

## Manual Validation

- `<manual check, if needed>`

## Completion

When done:

- run required validation;
- commit locally with a Conventional Commits message (use `/caveman-commit`);
- never push, never merge, never resolve conflicts, never remove the worktree;
- report changed areas, validation results, commit hash, and the docs/status the ORCHESTRATOR should update (the WORKER does not edit docs).
