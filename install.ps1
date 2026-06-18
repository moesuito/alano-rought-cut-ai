# Alano Rough Cut AI Assistant Installer for Windows

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "         Installing Alano Rough Cut AI Assistant...       " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

# 1. Prerequisites Check
Write-Host "Checking prerequisites..." -ForegroundColor Cyan

if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "Git is not installed or not in PATH. Please install Git and try again."
    exit 1
}

if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.10+ and try again."
    exit 1
}

# 2. Setup directory
$InstallDir = Join-Path $env:APPDATA "alano-rought-cut-ai"
Write-Host "Target installation path: $InstallDir" -ForegroundColor White

if (Test-Path $InstallDir) {
    Write-Host "Destination folder already exists. Updating..." -ForegroundColor Cyan
    if (Test-Path (Join-Path $InstallDir ".git")) {
        Push-Location $InstallDir
        try {
            git pull --ff-only
        }
        catch {
            Write-Host "Git pull failed. Re-cloning repository..." -ForegroundColor Yellow
            Pop-Location
            Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
            git clone https://github.com/moesuito/alano-rought-cut-ai.git $InstallDir
        }
        finally {
            if ((Get-Location).Path -eq $InstallDir) {
                Pop-Location
            }
        }
    } else {
        Write-Host "Existing folder is not a git repository. Re-cloning..." -ForegroundColor Yellow
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        git clone https://github.com/moesuito/alano-rought-cut-ai.git $InstallDir
    }
} else {
    Write-Host "Cloning repository..." -ForegroundColor Cyan
    git clone https://github.com/moesuito/alano-rought-cut-ai.git $InstallDir
}

# 3. Setup Virtual Environment
$VenvDir = Join-Path $InstallDir ".venv"
Write-Host "Setting up Python virtual environment in $VenvDir..." -ForegroundColor Cyan

if (!(Test-Path $VenvDir)) {
    python -m venv $VenvDir
}

$PipPath = Join-Path $VenvDir "Scripts\pip.exe"
Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $PipPath install -e $InstallDir

# 4. Expose CLI to PATH
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

# 5. Done
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "          Installation Completed Successfully!            " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Please RESTART your terminal/IDE to load the PATH updates." -ForegroundColor Yellow
Write-Host " To initialize a video editing project anywhere, run:" -ForegroundColor White
Write-Host "   alanocut init" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
