<!-- Stable explainer of the branch + worktree git flow. Clear and precise — NOT caveman. -->

# Git Worktree Flow - codex-chore-dev-harness

`main` = production = source of truth (clean). Each unit of work is a feature branch in its own git worktree (= dev) inside the git-ignored `.worktrees/` folder in this workspace. The ORCHESTRATOR works in the main workspace (it sees `main` + all worktrees); each WORKER is opened directly inside its own `.worktrees/<branch>` and sees nothing else. This document is the canonical reference for the lifecycle; the ORCHESTRATOR owns it.

## Why worktrees

- Branches are physically separated on disk, so multiple WORKERs (in separate chats) can work in parallel without stepping on each other.
- `node_modules` does not bloat the disk: node_modules (npm): the worktree's `node_modules` is a Windows junction to the main worktree's `node_modules` (created by `scripts/new-worktree.ps1`, no admin needed). Keep lockfiles identical across worktrees; a worktree never shares node_modules automatically.
- One branch is checked out in exactly one worktree (git branch-lock), which makes parallel work collision-free.

## Layout

```
<repo>/                       # main worktree = main = prod; ORCHESTRATOR works here
  .git/
  .gitignore                  # ignores .worktrees/, task.md, orchestrator-state.md, docs/scratches/
  .worktrees/          # git-ignored; holds the linked worktrees
    feature/<name>/           # one worktree per branch = dev; the WORKER is opened here
    feature/<epic>/           # epic mother (multi-worker)
    feature/<epic>-api/       # sub-branch worktree, based on the epic
```

## Branch naming

- `feature/f0-foundation` — F0 planning / documentation / harness (no product code).
- `feature/f1-<name>`, `feature/f2-<name>` … — real implementation phases.
- `fix/<slug>`, `chore/<slug>` — quick-mode and maintenance branches.

Reserve `feature/f1-*` for F1 work; never label F0 work `f1`.

## Access model

- **ORCHESTRATOR** is opened in `<repo>` (main): full access to `main` and every worktree under `.worktrees/`. It creates branches/worktrees, writes and commits the working docs + worktree `task.md`, reviews, and merges.
- **WORKER** is opened inside `.worktrees/<branch>`: it sees only that worktree — not `main`, not sibling worktrees.

## Lifecycle (every start has this end)

1. **Start** — `scripts/new-worktree.ps1 -Branch feature/<name>` (epic sub-branch: add `-Base feature/<epic>`). Creates the branch, the worktree, wires node_modules, and seeds `docs/active/feature/<name>/`.
2. **Plan + handoff** — ORCHESTRATOR writes `implementation-plan.md` + dependency-ordered task docs inside the worktree (and commits them to the branch), then writes the worktree-root `task.md` declaring task name, branch, base branch, **absolute** worktree path, next executable task, and the Worker Read List. Only then is the WORKER opened in the worktree.
3. **Implement** — WORKER(s) pinned to this branch/worktree; local commits only.
4. **Review** — ORCHESTRATOR reviews each commit (loop, with the 3-cycle escalation rule).
5. **Stint** — user runs a scoped manual stint.
6. **Merge-prep** — fold the feature into the master docs; `git mv docs/active/<branch>` -> `docs/closed/<branch>`; append `docs/closed/README.md`; reset local state. (See ORCHESTRATOR.md.)
7. **Push + PR** — after approval: `git push -u origin feature/<name>` then `gh pr create`.
8. **Merge** — ORCHESTRATOR merges (epic: sub -> mother -> `main`), resolving conflicts.
9. **Teardown** — `scripts/remove-worktree.ps1 -Branch feature/<name>`. No orphan worktrees or branches.

## Parallelism (hybrid)

- Default: each WORKER on its own (sub-)branch and worktree. Parallelism is across branches.
- Exception (shared environment): multiple WORKER chats may share ONE worktree only when their tasks are file-disjoint; each task declares `File ownership` and the ORCHESTRATOR guarantees no overlap. This is the "pair-on-one-machine" case — avoid it for multi-day parallel work.

## Multi-worker = epic

```powershell
# 1. mother branch + worktree, based on production
scripts/new-worktree.ps1 -Branch feature/<epic>

# 2. one sub-branch worktree per worker, based on the epic
scripts/new-worktree.ps1 -Branch feature/<epic>-api -Base feature/<epic>
scripts/new-worktree.ps1 -Branch feature/<epic>-ui  -Base feature/<epic>

# 3. each sub-branch is merged into the epic (ORCHESTRATOR resolves conflicts),
#    then its worktree is removed. Finally the epic merges into main.
```

## node_modules strategy (npm)

This project's strategy: `junction`.

- **pnpm:** each worktree runs `npm install`; packages are hardlinked from the global store, so disk cost is near-zero. A worktree still has its own `node_modules` folder — it is just cheap.
- **npm / yarn:** `new-worktree.ps1` creates `node_modules` as a Windows **junction** to the main worktree's `node_modules` (`New-Item -ItemType Junction`, no admin needed). Keep lockfiles identical across worktrees. A worktree never shares node_modules automatically — the junction is what shares it.

## Manual commands (what the helpers wrap)

```powershell
# create
git worktree add -b feature/<name> ".worktrees\feature\<name>" main

# list / inspect (run on ORCHESTRATOR startup)
git worktree list

# remove after merge
git worktree remove ".worktrees\feature\<name>"
git branch -d feature/<name>
git worktree prune
```

Do not run `git gc` / `git repack` while other worktrees are mid-work (they share `.git/objects`). If a worktree folder is moved or deleted by hand, run `git worktree prune` (and `git worktree repair` if needed).
