#requires -Version 5.1
<#
.SYNOPSIS
  Teardown a feature worktree after merge: remove the worktree, delete the branch, prune.
.DESCRIPTION
  Run only after the branch is merged (merge-prep done, docs archived). Refuses a dirty
  worktree or an unmerged branch unless -Force. This is step 9 of the lifecycle; it is what
  guarantees no orphan worktrees or branches are left behind.
.EXAMPLE
  scripts/remove-worktree.ps1 -Branch feature/login-ui
.EXAMPLE
  scripts/remove-worktree.ps1 -Branch feature/spike -Force   # discard an abandoned worktree
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorktreeRoot = Join-Path $RepoRoot ".worktrees"
$RelPath = $Branch -replace "/", "\"
$WorktreePath = Join-Path $WorktreeRoot $RelPath

if (-not (Test-Path $WorktreePath)) {
    Write-Host "Worktree path not found (already removed?): $WorktreePath"
}
else {
    if (-not $Force) {
        $dirty = git -C $WorktreePath status --porcelain
        if ($dirty) {
            Write-Warning "Worktree has uncommitted changes. Commit/discard them, or re-run with -Force."
            $dirty | ForEach-Object { Write-Host "  $_" }
            exit 1
        }
    }
    Write-Host "Removing worktree: $WorktreePath"
    if ($Force) { git -C $RepoRoot worktree remove --force $WorktreePath }
    else { git -C $RepoRoot worktree remove $WorktreePath }
    if ($LASTEXITCODE -ne 0) { throw "git worktree remove failed" }
}

Write-Host "Deleting branch: $Branch"
if ($Force) { git -C $RepoRoot branch -D $Branch }
else {
    git -C $RepoRoot branch -d $Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Branch '$Branch' is not fully merged. Re-run with -Force to delete it anyway."
    }
}

git -C $RepoRoot worktree prune
Write-Host "Done. Update orchestrator-state.md (remove from Open Worktrees)."
