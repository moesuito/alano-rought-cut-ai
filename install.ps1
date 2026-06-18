# Alano Rough Cut AI Assistant Installer for Windows

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "         Installing Alano Rough Cut AI Assistant...       " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

# 1. Prerequisites Check
Write-Host "Checking prerequisites..." -ForegroundColor Cyan

if (!(Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed or not in PATH. Please install gh and try again."
    exit 1
}

# Verify gh is authenticated
$AuthStatus = gh auth status 2>&1
if ($AuthStatus -match "Logged in to github.com") {
    Write-Host "  -> GitHub CLI authenticated." -ForegroundColor Green
} else {
    Write-Error "GitHub CLI (gh) is not logged in. Please run 'gh auth login' and try again."
    exit 1
}

if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.10+ and try again."
    exit 1
}

$InstallDir = Join-Path $env:APPDATA "alano-rought-cut-ai"
Write-Host "Target installation path: $InstallDir" -ForegroundColor White

# 2. Get latest release or fallback
Write-Host "Checking for the latest release on GitHub..." -ForegroundColor Cyan

$LatestTag = $null
try {
    $TagName = (gh release view -R moesuito/alano-rought-cut-ai --json tagName --jq .tagName 2>$null)
    if ($TagName -match "v\d+\.\d+\.\d+") {
        $LatestTag = $TagName.Trim()
    }
} catch {
    # No release or error
}

if ($LatestTag) {
    Write-Host "Latest release found: $LatestTag" -ForegroundColor Green
    
    # Create temp directory
    $TempDir = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName())
    $TempExtractDir = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    New-Item -ItemType Directory -Path $TempExtractDir -Force | Out-Null
    
    try {
        Write-Host "Downloading release archive..." -ForegroundColor Cyan
        gh release download $LatestTag -R moesuito/alano-rought-cut-ai --archive=zip --output-dir $TempDir
        
        $ZipFile = Get-ChildItem -Path $TempDir -Filter "*.zip" | Select-Object -First 1
        if (!$ZipFile) {
            throw "Failed to download release zip"
        }
        
        Write-Host "Extracting files..." -ForegroundColor Cyan
        Expand-Archive -Path $ZipFile.FullName -DestinationPath $TempExtractDir -Force
        
        $SubDir = Get-ChildItem -Path $TempExtractDir -Directory | Select-Object -First 1
        if (!$SubDir) {
            throw "No directory found in extracted archive"
        }
        
        # Clean target directory preserving .env and .venv
        if (!(Test-Path $InstallDir)) {
            New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        } else {
            Get-ChildItem -Path $InstallDir | Where-Object { $_.Name -ne ".venv" -and $_.Name -ne ".env" } | Remove-Item -Recurse -Force
        }
        
        # Copy to installation folder
        Get-ChildItem -Path $SubDir.FullName | Copy-Item -Destination $InstallDir -Recurse -Force
    }
    finally {
        # Cleanup temp
        Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $TempExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "No release found on GitHub. Falling back to git clone of main branch..." -ForegroundColor Yellow
    if (Test-Path $InstallDir) {
        if (Test-Path (Join-Path $InstallDir ".git")) {
            Push-Location $InstallDir
            try {
                git pull --ff-only
            }
            catch {
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
            Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
            git clone https://github.com/moesuito/alano-rought-cut-ai.git $InstallDir
        }
    } else {
        git clone https://github.com/moesuito/alano-rought-cut-ai.git $InstallDir
    }
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

# 4. Prompt for ElevenLabs API Key
$EnvPath = Join-Path $InstallDir ".env"
$HasKey = $false
if (Test-Path $EnvPath) {
    $EnvContent = Get-Content $EnvPath
    if ($EnvContent -match "ELEVENLABS_API_KEY=.+") {
        $HasKey = $true
    }
}

if (!$HasKey) {
    Write-Host ""
    Write-Host "----------------------------------------------------------" -ForegroundColor Yellow
    Write-Host "   Configurando a API Key do ElevenLabs (Transcricao)    " -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------" -ForegroundColor Yellow
    Write-Host "Você pode encontrar ou gerar suas chaves em:" -ForegroundColor Gray
    Write-Host "  https://elevenlabs.io/app/settings/api-keys" -ForegroundColor Gray
    Write-Host ""
    $ApiKey = Read-Host "Cole a sua ElevenLabs API Key aqui"
    if (![string]::IsNullOrEmpty($ApiKey)) {
        "ELEVENLABS_API_KEY=$ApiKey" | Out-File -FilePath $EnvPath -Encoding utf8 -Force
        Write-Host "  -> Chave salva com sucesso em $EnvPath!" -ForegroundColor Green
    } else {
        Write-Host "Nenhuma chave inserida. Voce podera configurar o arquivo .env manualmente depois." -ForegroundColor Red
    }
    Write-Host "----------------------------------------------------------" -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "Chave API do ElevenLabs já configurada em $EnvPath." -ForegroundColor Green
}

# 5. Expose CLI to PATH
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

# 6. Done
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "          Installation Completed Successfully!            " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Please RESTART your terminal/IDE to load the PATH updates." -ForegroundColor Yellow
Write-Host " To initialize a video editing project anywhere, run:" -ForegroundColor White
Write-Host "   alanocut init" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
