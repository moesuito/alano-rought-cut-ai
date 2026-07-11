#requires -Version 5.1
<#
.SYNOPSIS
  Start a feature: create a branch + git worktree and seed docs/active/<branch>/.
.DESCRIPTION
  Branch + worktree model: main = prod, each branch = its own worktree (dev).
  Worktrees live in the git-ignored .worktrees/ folder inside the repo workspace.
  This Python/FFmpeg project does not wire Node.js dependencies.
.EXAMPLE
  scripts/new-worktree.ps1 -Branch feature/login-ui
.EXAMPLE
  scripts/new-worktree.ps1 -Branch feature/checkout-pix -Base feature/novo-checkout
.EXAMPLE
  scripts/new-worktree.ps1 -Branch fix/login-401 -Quick   # quick mode: seeds quick-task.md, no formal plan
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [string]$Base = "main",
    [switch]$Quick
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorktreeRoot = Join-Path $RepoRoot ".worktrees"
$RelPath = $Branch -replace "/", "\"
$WorktreePath = Join-Path $WorktreeRoot $RelPath

if (Test-Path $WorktreePath) {
    throw "Worktree path already exists: $WorktreePath"
}
$existing = git -C $RepoRoot branch --list $Branch
if ($existing) {
    throw "Branch already exists: $Branch (use it via 'git worktree add' manually, or pick a new name)"
}

New-Item -ItemType Directory -Path (Split-Path $WorktreePath -Parent) -Force | Out-Null

Write-Host "Creating worktree: $Branch  (base $Base)  ->  $WorktreePath"
git -C $RepoRoot worktree add -b $Branch $WorktreePath $Base
if ($LASTEXITCODE -ne 0) { throw "git worktree add failed" }

# --- seed docs/active/<branch>/ from the project templates ---
$ActiveDir = Join-Path $WorktreePath ("docs\active\" + $RelPath)
New-Item -ItemType Directory -Path $ActiveDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ActiveDir "stints") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ActiveDir "known-issues") -Force | Out-Null
# WORKER report zone (git-ignored): one fx.x-report.md per task/subtask
New-Item -ItemType Directory -Path (Join-Path $WorktreePath "docs\scratches") -Force | Out-Null

if ($Quick) {
    # Quick mode: lightweight task doc, no formal implementation plan.
    $quickTpl = Join-Path $RepoRoot "docs\tasks\QUICK_TASK_TEMPLATE.md"
    if (Test-Path $quickTpl) { Copy-Item $quickTpl (Join-Path $ActiveDir "quick-task.md") }
}
else {
    $planTpl = Join-Path $RepoRoot "docs\tasks\IMPLEMENTATION_PLAN_TEMPLATE.md"
    $doneTpl = Join-Path $RepoRoot "docs\tasks\COMPLETED_TASKS_TEMPLATE.md"
    if (Test-Path $planTpl) { Copy-Item $planTpl (Join-Path $ActiveDir "implementation-plan.md") }
    if (Test-Path $doneTpl) { Copy-Item $doneTpl (Join-Path $ActiveDir "completed_tasks.md") }
}

$seedDoc = if ($Quick) { "quick-task.md" } else { "implementation-plan.md + task docs" }
Write-Host ""
Write-Host "Worktree ready. Next:"
Write-Host "  1. ORCHESTRATOR: fill docs/active/$Branch/$seedDoc."
Write-Host "  2. Point each WORKER chat at this folder: $WorktreePath"
Write-Host "  3. Record the worktree in orchestrator-state.md (Open Worktrees)."
