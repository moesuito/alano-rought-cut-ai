# Alano Rough Cut AI Assistant Installer for Windows

param(
    [ValidateSet("whisper-vulkan", "elevenlabs", "assemblyai")][string]$Provider = "whisper-vulkan",
    [ValidateSet("community-1", "none")][string]$Diarization = "community-1",
    [switch]$NonInteractive,
    [switch]$RuntimeSyncOnly
)

$ErrorActionPreference = "Stop"

$RuntimeDirectories = @("agent_knowledge", "bin", "helpers")
$RuntimeFiles = @(".env.example", "config.json", "LICENSE", "pyproject.toml", "README.md")
$PreservedInstallEntries = @(".env", ".venv", "user-settings.json")

function Assert-PathInsideRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowRoot
    )

    $ResolvedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $ResolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $Comparison = [System.StringComparison]::OrdinalIgnoreCase
    $Separator = [System.IO.Path]::DirectorySeparatorChar
    $IsRoot = [string]::Equals($ResolvedPath, $ResolvedRoot, $Comparison)
    $IsChild = $ResolvedPath.StartsWith("$ResolvedRoot$Separator", $Comparison)
    if ((!$AllowRoot -and $IsRoot) -or (!$IsRoot -and !$IsChild)) {
        throw "RUNTIME_SYNC_UNSAFE_PATH: $Label is outside its expected root."
    }
    return $ResolvedPath
}

function Assert-NotReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    try {
        $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    } catch [System.Management.Automation.ItemNotFoundException] {
        return
    }
    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "RUNTIME_SYNC_REPARSE_POINT: $Label cannot be a junction or symbolic link."
    }
}

function Assert-TreeHasNoReparsePoints {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-NotReparsePoint -Path $Root -Label $Label
    Get-ChildItem -LiteralPath $Root -Force -Recurse | ForEach-Object {
        if (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "RUNTIME_SYNC_REPARSE_POINT: $Label contains a junction or symbolic link."
        }
    }
}

function Assert-AlanoRuntimeFiles {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    foreach ($RequiredPath in @(
        "agent_knowledge\manifest.json",
        "bin\alanocut.cmd",
        "bin\alanocut.ps1",
        "helpers\agent_artifacts.py",
        "helpers\agent_telemetry.py",
        "helpers\agent_tools.py",
        "helpers\artifact_agent.py",
        "helpers\editorial_edl_bridge.py",
        "helpers\interactive_cli.py",
        "helpers\knowledge_loader.py",
        "helpers\llm_client.py",
        "helpers\orchestrator.py",
        "config.json",
        "pyproject.toml"
    )) {
        $Candidate = Join-Path $RuntimeRoot $RequiredPath
        Assert-PathInsideRoot -Path $Candidate -Root $RuntimeRoot -Label $RequiredPath | Out-Null
        Assert-NotReparsePoint -Path $Candidate -Label $RequiredPath
        if (!(Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            throw "RUNTIME_SOURCE_INCOMPLETE: missing $RequiredPath"
        }
    }
}

function Invoke-AlanoRuntimeImportSmoke {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $VenvRoot = Join-Path $RuntimeRoot ".venv"
    Assert-PathInsideRoot -Path $VenvRoot -Root $RuntimeRoot -Label ".venv" | Out-Null
    Assert-NotReparsePoint -Path $VenvRoot -Label "Global virtual environment"
    $PythonPath = Join-Path $VenvRoot "Scripts\python.exe"
    Assert-PathInsideRoot -Path $PythonPath -Root $VenvRoot -Label "Global Python" | Out-Null
    Assert-NotReparsePoint -Path $PythonPath -Label "Global Python"
    if (!(Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "RUNTIME_IMPORT_SMOKE_FAILED: global Python is missing."
    }

    $SmokeScript = @'
import importlib
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(root))
os.environ["ALANOCUT_KNOWLEDGE_DIR"] = str(root / "agent_knowledge")
for name in (
    "helpers.agent_artifacts",
    "helpers.agent_telemetry",
    "helpers.agent_tools",
    "helpers.artifact_agent",
    "helpers.editorial_edl_bridge",
    "helpers.knowledge_loader",
    "helpers.llm_client",
    "helpers.orchestrator",
    "helpers.interactive_cli",
):
    module = importlib.import_module(name)
    Path(module.__file__).resolve(strict=True).relative_to(root)

from helpers.knowledge_loader import load_artifact_schema_catalog
catalog = load_artifact_schema_catalog()
if catalog.root != (root / "agent_knowledge").resolve(strict=True) or not catalog.schemas:
    raise RuntimeError("installed knowledge catalog is unavailable")
'@

    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 rewrites embedded quotes in multiline native
        # `-c` arguments.  Base64 keeps the Python source opaque to the shell
        # while still avoiding a persistent temporary script on disk.
        $SmokeBase64 = [Convert]::ToBase64String(
            [System.Text.Encoding]::UTF8.GetBytes($SmokeScript)
        )
        $SmokeBootstrap = "import base64;exec(base64.b64decode('$SmokeBase64'))"
        $ErrorActionPreference = "Continue"
        & $PythonPath -I -c $SmokeBootstrap $RuntimeRoot 1>$null 2>$null
        $SmokeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($SmokeExitCode -ne 0) {
        throw "RUNTIME_IMPORT_SMOKE_FAILED: installed modules or dependencies are invalid."
    }
}

function Sync-AlanoRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $ExpectedRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $env:APPDATA "alano-rought-cut-ai")
    ).TrimEnd('\')
    $ResolvedSource = [System.IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
    $ResolvedDestination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\')
    if (![string]::Equals($ExpectedRoot, $ResolvedDestination, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to synchronize an unexpected installation path: $ResolvedDestination"
    }
    $Separator = [System.IO.Path]::DirectorySeparatorChar
    if (
        [string]::Equals($ResolvedSource, $ResolvedDestination, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ResolvedSource.StartsWith("$ResolvedDestination$Separator", [System.StringComparison]::OrdinalIgnoreCase) -or
        $ResolvedDestination.StartsWith("$ResolvedSource$Separator", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to synchronize overlapping source and destination paths."
    }

    Assert-NotReparsePoint -Path $ResolvedSource -Label "Runtime source root"
    Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"

    Assert-AlanoRuntimeFiles -RuntimeRoot $ResolvedSource

    if (!(Test-Path -LiteralPath $DestinationRoot)) {
        New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    }
    Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"

    Get-ChildItem -LiteralPath $ResolvedDestination -Force |
        Where-Object { $PreservedInstallEntries -notcontains $_.Name } |
        ForEach-Object {
            $RemovalPath = Assert-PathInsideRoot -Path $_.FullName -Root $ResolvedDestination -Label $_.Name
            Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"
            if ($_.PSIsContainer) {
                Assert-TreeHasNoReparsePoints -Root $RemovalPath -Label "Runtime entry '$($_.Name)'"
            } else {
                Assert-NotReparsePoint -Path $RemovalPath -Label "Runtime entry '$($_.Name)'"
            }
            Remove-Item -LiteralPath $RemovalPath -Recurse -Force
        }

    foreach ($Directory in $RuntimeDirectories) {
        $SourceDirectory = Assert-PathInsideRoot -Path (Join-Path $ResolvedSource $Directory) -Root $ResolvedSource -Label $Directory
        $DestinationDirectory = Assert-PathInsideRoot -Path (Join-Path $ResolvedDestination $Directory) -Root $ResolvedDestination -Label $Directory
        Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"
        Assert-NotReparsePoint -Path $DestinationDirectory -Label "Runtime destination '$Directory'"
        if (Test-Path -LiteralPath $DestinationDirectory) {
            throw "RUNTIME_SYNC_CONFLICT: destination entry already exists for $Directory"
        }
        Assert-TreeHasNoReparsePoints -Root $SourceDirectory -Label "Runtime source '$Directory'"
        Copy-Item -LiteralPath $SourceDirectory -Destination $ResolvedDestination -Recurse -Force
    }
    foreach ($File in $RuntimeFiles) {
        $SourceFile = Assert-PathInsideRoot -Path (Join-Path $ResolvedSource $File) -Root $ResolvedSource -Label $File
        if (Test-Path -LiteralPath $SourceFile) {
            $DestinationFile = Assert-PathInsideRoot -Path (Join-Path $ResolvedDestination $File) -Root $ResolvedDestination -Label $File
            Assert-NotReparsePoint -Path $ResolvedSource -Label "Runtime source root"
            Assert-NotReparsePoint -Path $SourceFile -Label "Runtime source '$File'"
            Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"
            Assert-NotReparsePoint -Path $DestinationFile -Label "Runtime destination '$File'"
            if (Test-Path -LiteralPath $DestinationFile) {
                throw "RUNTIME_SYNC_CONFLICT: destination entry already exists for $File"
            }
            Copy-Item -LiteralPath $SourceFile -Destination $DestinationFile
        }
    }

    # Defense in depth for installations upgraded from a development checkout.
    foreach ($DevelopmentTree in @(".agents", ".squad")) {
        $DevelopmentPath = Assert-PathInsideRoot -Path (Join-Path $ResolvedDestination $DevelopmentTree) -Root $ResolvedDestination -Label $DevelopmentTree
        if (Test-Path -LiteralPath $DevelopmentPath) {
            Assert-NotReparsePoint -Path $ResolvedDestination -Label "Runtime destination root"
            Assert-NotReparsePoint -Path $DevelopmentPath -Label "Development tree '$DevelopmentTree'"
            Remove-Item -LiteralPath $DevelopmentPath -Recurse -Force
        }
    }

    Assert-AlanoRuntimeFiles -RuntimeRoot $ResolvedDestination
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "         Installing Alano Rough Cut AI Assistant...       " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

$InstallDir = Join-Path $env:APPDATA "alano-rought-cut-ai"
$SourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
Write-Host "Target installation path: $InstallDir" -ForegroundColor White
Write-Host "Runtime source: $SourceRoot" -ForegroundColor White

# 1. Synchronize the allowlisted runtime from this checkout. This must happen
# before dependency setup so the installed code always matches the reviewed
# source tree that invoked the installer.
Sync-AlanoRuntime -SourceRoot $SourceRoot -DestinationRoot $InstallDir

# Internal, non-public hook for validating runtime packaging without creating a
# virtual environment, downloading models, changing PATH or running setup.
if ($RuntimeSyncOnly) {
    $ExistingVenv = Join-Path $InstallDir ".venv"
    if (Test-Path -LiteralPath $ExistingVenv) {
        Invoke-AlanoRuntimeImportSmoke -RuntimeRoot $InstallDir
    }
    Write-Host "Runtime synchronization completed." -ForegroundColor Green
    exit 0
}

# 2. Prerequisites Check
Write-Host "Checking prerequisites..." -ForegroundColor Cyan

if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.10+ and try again."
    exit 1
}

# 3. Setup Shared Virtual Environment
$VenvDir = Join-Path $InstallDir ".venv"
Write-Host "Setting up shared Python virtual environment in $VenvDir..." -ForegroundColor Cyan

if (!(Test-Path $VenvDir)) {
    python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Virtual environment creation failed with exit code $LASTEXITCODE."
    }
}

$PipPath = Join-Path $VenvDir "Scripts\pip.exe"
if (!(Test-Path -LiteralPath $PipPath)) {
    throw "The shared virtual environment is incomplete; pip is missing at $PipPath."
}
Write-Host "Installing project dependencies (TUI, PyTorch, DeepFilterNet 3, ONNX/DirectML, Transformers, SciPy, Pillow, NumPy, Requests)..." -ForegroundColor Cyan
& $PipPath install -e $InstallDir
if ($LASTEXITCODE -ne 0) {
    throw "Project dependency installation failed with exit code $LASTEXITCODE."
}

$PythonPath = Join-Path $VenvDir "Scripts\python.exe"
if (!(Test-Path -LiteralPath $PythonPath)) {
    throw "The shared virtual environment is incomplete; Python is missing at $PythonPath."
}

Invoke-AlanoRuntimeImportSmoke -RuntimeRoot $InstallDir

# 4. Prefetch and validate DeepFilterNet 3 neural model
Write-Host "Prefetching and validating DeepFilterNet 3 neural denoiser..." -ForegroundColor Cyan
& $PythonPath -c "from df.enhance import init_df; init_df(); print('DeepFilterNet 3 neural denoiser successfully verified!')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Note: DeepFilterNet 3 model cache will initialize on first run." -ForegroundColor Yellow
}

# 5. Provider setup
$WizardPath = Join-Path $InstallDir "helpers\setup_wizard.py"
if (!(Test-Path $WizardPath)) {
    Write-Error "Setup wizard is missing: $WizardPath"
    exit 1
}
$WizardArgs = @($WizardPath, "configure", "--provider", $Provider)
if ($Diarization) { $WizardArgs += @("--diarization", $Diarization) }
if ($NonInteractive) { $WizardArgs += "--non-interactive" }
& $PythonPath @WizardArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Provider setup failed. Re-run install.ps1 to retry configuration."
    exit 1
}

# 6. Expose CLI to PATH
$BinDir = Join-Path $InstallDir "bin"
Write-Host "Adding $BinDir to PATH..." -ForegroundColor Cyan

$UserPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
$PathList = $UserPath -split ";"

if ($PathList -notcontains $BinDir) {
    $NewUserPath = $UserPath + ";" + $BinDir
    $NewUserPath = $NewUserPath.Replace(";;", ";") # Clean up empty sections
    [System.Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    Write-Host "Successfully added to User PATH!" -ForegroundColor Green
} else {
    Write-Host "Path is already configured." -ForegroundColor Gray
}

# 7. Done
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "          Installation Completed Successfully!            " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Please RESTART your terminal/IDE to load the PATH updates." -ForegroundColor Yellow
Write-Host " To edit your videos, open any folder with raw video clips and run:" -ForegroundColor White
Write-Host "   alanocut" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
