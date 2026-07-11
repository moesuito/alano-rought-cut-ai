# Agent Operating Model

Status: active.

This project uses an ORCHESTRATOR/WORKER workflow on a branch + worktree git flow to keep planning and execution separate and the documentation as the source of truth.

## Roles

- ORCHESTRATOR: plans with the user, writes focused docs, reviews code, runs deeper validation, owns the feature lifecycle, resolves merge conflicts, and pushes/merges only with explicit approval.
- WORKER: executes the approved task inside one assigned worktree, tests, commits locally, and never pushes. The WORKER does not edit docs and never resolves conflicts; it reports what changed for the ORCHESTRATOR to document.
- When available, `antigravity-subagent` is the default direct transport for a WORKER: Codex stays the ORCHESTRATOR, runs a monitored `agy` session in the assigned worktree, reviews it directly, and sends correction deltas without using the user as a bridge.

Roles are set by the prompt, not by which model runs them — pick the model per task (the task's `Difficulty: N/10` helps you choose).

## Branch + Worktree

`main` = production = source of truth (clean). Each unit of work is a feature branch in its own git worktree at `.worktrees/<branch>` inside the workspace (git-ignored) = dev. The ORCHESTRATOR works in main and sees everything (main + all worktrees); each WORKER is opened inside its own worktree and sees only that.

- One branch per worktree (git branch-lock). Parallelism is across branches.
- Multi-worker features use an epic (mother) branch with sub-branches: sub-branches merge into the epic, the epic merges into `main`.
- Hybrid exception: multiple WORKER chats may share one worktree only when their tasks are file-disjoint (declared `File ownership`).
- `node_modules` is per-worktree (pnpm hardlinks, or a Windows junction for npm/yarn). See `docs/git-worktree-flow.md`.

## Phases And Tasks

- Phases: F0 = always planning (greenfield: build master docs on `main`, no product code); F1, F2... = development. `docs/ROADMAP.md` lists phases and the branches (incl. epics) each spawns.
- A phase can spawn several branches; each branch carries its own ordered task list (F1.1, F1.2...), with `Depends on` so later tasks build on earlier ones.
- Naming: F0 planning branch = `feature/f0-foundation` (no product code); `feature/f1-*` for F1; `fix/<slug>` / `chore/<slug>` for quick/maintenance.
- Phase-sequence exception: when the user explicitly approves, one phase = one active task (`PHASE_TASK_TEMPLATE`); the WORKER makes one commit + one `docs/scratches/<id>-report.md` per subtask and waits for confirmation between them.
- Every task gets `Difficulty: N/10` (0-1 trivial ... 9-10 architecture) and a `Layer`. The harness never names a model; the user picks the agent from the score.
- **Task sizing:** Quick (≤3 files, `-Quick` worktree, no formal plan), Standard (one feature, full plan + tasks), Epic (multi-worker, mother + sub-branches). The git flow is constant; only planning depth scales. See `ORCHESTRATOR.md` → Task Sizing.
- **Traceability:** requirements carry IDs (`AUTH-01`) + EARS acceptance (`WHEN/THEN/SHALL`) in `PRD.md`; tasks reference them; the trail spec → task → commit lives in the implementation plan.
- **Durable memory:** lessons learned and deferred (out-of-scope) ideas go to `docs/MEMORY.md` (versioned), distinct from the transient `orchestrator-state.md` cockpit.

## Modularity / Clean Code

Program modularly: separate features/frontend/backend into folders, market-standard clean code. Rationale: a single vertical file is easy for an AI on a small project, but on a large project it hurts more than it helps. The concrete module/folder contract lives in `docs/ARCHITECTURE.md`. Steady-state tasks are vertical full-stack slices; at project start, split frontend/backend and add a final integration task.

## Communication

caveman (full) is the default chat mode for all roles, including planning with the user, and `/caveman-commit` / `/caveman-review` for commits and reviews. Generated docs/specs and the WORKER's final report stay clear and precise (never caveman). See `AGENTS.md` -> Communication.

## Standard Prompts

ORCHESTRATOR:

```text
Leia o AGENTS.md e trabalhe como ORCHESTRATOR.
```

WORKER:

```text
Leia o AGENTS.md e trabalhe como WORKER. Prossiga com a proxima tarefa ativa.
```

## Worker Economy Rules

The WORKER should read the minimum needed to execute:

1. `AGENTS.md`
2. `WORKER.md`
3. `task.md`
4. only the files listed in `task.md` under Worker Read List
5. targeted code files found through search

The WORKER should not read broad docs, ADRs, old phase history, memory files, or accumulated implementation plans unless `task.md` explicitly says so.

### Context Discipline

Keep loaded context lean so most of the window stays free for reasoning and output.

- **Never load simultaneously:** multiple feature plans, multiple archived (`docs/closed/`) docs, or the full history at once.
- **Keep docs lean:** master docs are summaries, not logs — if one grows past ~a couple of pages, split it or move detail into the branch's working docs, `MEMORY.md`, or the archive.
- **On demand only:** load `ARCHITECTURE.md`, a spec, or a plan when the current step needs it, then drop it.

## Task Handoff

Before assigning a WORKER, the ORCHESTRATOR makes `task.md` the launch point after `AGENTS.md` and `WORKER.md`. Each active task includes its ID/branch/worktree, difficulty, scope boundaries, required tests, stop-and-ask conditions for the ORCHESTRATOR, and closeout instructions.

## Validation Contract

Baseline commands:

```powershell
python -m compileall -q helpers
python helpers/edl_to_fcpxml.py --help
python helpers/validate_edl_boundaries.py --help
```

- `python -m compileall -q helpers`
- `python helpers/edl_to_fcpxml.py --help`
- `python helpers/validate_edl_boundaries.py --help`

If no smoke/e2e suite exists, create a task to add one before relying on the harness for high-risk work.

## Approval Closeout

When the user says a task/phase is approved, the WORKER stops servers it started unless asked otherwise, reports final facts and which docs need updating, and never pushes/merges. The WORKER does not edit docs — the ORCHESTRATOR owns all doc/task/completed-tasks/known-issue/manual-testing updates, archiving, merge, and worktree teardown.

Push, PR, merge, release tags, and production promotion are ORCHESTRATOR actions and require explicit user approval.
