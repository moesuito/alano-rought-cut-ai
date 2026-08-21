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

    foreach ($RequiredPath in @(
        "agent_knowledge\manifest.json",
        "bin\alanocut.ps1",
        "helpers\interactive_cli.py",
        "config.json",
        "pyproject.toml"
    )) {
        if (!(Test-Path -LiteralPath (Join-Path $SourceRoot $RequiredPath))) {
            throw "Runtime source is incomplete; missing $RequiredPath"
        }
    }

    if (!(Test-Path -LiteralPath $DestinationRoot)) {
        New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    }

    Get-ChildItem -LiteralPath $DestinationRoot -Force |
        Where-Object { $PreservedInstallEntries -notcontains $_.Name } |
        Remove-Item -Recurse -Force

    foreach ($Directory in $RuntimeDirectories) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot $Directory) -Destination $DestinationRoot -Recurse -Force
    }
    foreach ($File in $RuntimeFiles) {
        $SourceFile = Join-Path $SourceRoot $File
        if (Test-Path -LiteralPath $SourceFile) {
            Copy-Item -LiteralPath $SourceFile -Destination $DestinationRoot -Force
        }
    }

    # Defense in depth for installations upgraded from a development checkout.
    foreach ($DevelopmentTree in @(".agents", ".squad")) {
        $DevelopmentPath = Join-Path $DestinationRoot $DevelopmentTree
        if (Test-Path -LiteralPath $DevelopmentPath) {
            Remove-Item -LiteralPath $DevelopmentPath -Recurse -Force
        }
    }
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
