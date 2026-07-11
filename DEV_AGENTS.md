# Development Agent Router

This file governs development of the Alano Cut repository. The existing `AGENTS.md` is a product artifact copied into editing workspaces; do not replace or reinterpret it as a development harness.

## Roles

- **ORCHESTRATOR:** read `ORCHESTRATOR.md`, then `orchestrator-state.md`; own plans, task docs, worktrees, review, integration, and release authority.
- **WORKER:** read `WORKER.md`, then the active `task.md`; implement only that task in its assigned worktree, test, commit locally, and never push or merge.

## Shared Development Rules

- `main` is production. Product work happens only in an isolated branch/worktree.
- The project is Python plus FFmpeg; do not create Node package management or `node_modules` wiring.
- Preserve private inputs: `audio/`, `.env`, raw media, generated edit outputs, and personal paths must never be staged, copied into fixtures, or sent to public CI.
- Run the task's validation and preserve test integrity. Never weaken or skip a test to obtain green output.
- Before using an unfamiliar external interface, verify it from the codebase or official documentation. Do not invent flags or APIs.
- Push, PR, merge, tags, releases, and conflict resolution require explicit user approval.

## Baseline Validation

```powershell
python -m compileall -q helpers
python helpers\edl_to_fcpxml.py --help
python helpers\validate_edl_boundaries.py --help
```
