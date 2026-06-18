# alanocut CLI Controller for Windows PowerShell

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Split-Path -Parent $ScriptDir

function Show-Help {
    Write-Host "Alano Rough Cut AI CLI - Command Line Utility" -ForegroundColor Green
    Write-Host "Usage:" -ForegroundColor White
    Write-Host "  alanocut init        Initialize current directory as a video rough-cut workspace" -ForegroundColor White
    Write-Host "  alanocut update      Check for updates on GitHub and apply if available" -ForegroundColor White
    Write-Host "  alanocut --help      Show this help message" -ForegroundColor White
    Write-Host ""
}

function Create-Junction {
    param(
        [string]$LinkPath,
        [string]$TargetDir
    )
    if (Test-Path $LinkPath) {
        # Safely remove old junction/link without deleting target contents
        cmd /c rmdir "$LinkPath" 2>$null
        Remove-Item -Path $LinkPath -Force -ErrorAction SilentlyContinue | Out-Null
    }
    
    $Parent = Split-Path -Parent $LinkPath
    if (!(Test-Path $Parent)) {
        New-Item -ItemType Directory -Path $Parent -Force -ErrorAction SilentlyContinue | Out-Null
    }
    
    cmd /c mklink /j "$LinkPath" "$TargetDir" | Out-Null
}

function Update-System {
    param(
        [bool]$Silent = $false
    )
    
    $LocalConfigPath = Join-Path $InstallRoot "config.json"
    $LocalVersion = "v0.0.0"
    if (Test-Path $LocalConfigPath) {
        try {
            $LocalVersion = (Get-Content $LocalConfigPath | ConvertFrom-Json).version
        } catch {}
    }
    
    if (!$Silent) {
        Write-Host "Checking for updates on GitHub..." -ForegroundColor Cyan
    }
    
    # Check if gh CLI is available
    if (!(Get-Command gh -ErrorAction SilentlyContinue)) {
        if (!$Silent) {
            Write-Error "GitHub CLI (gh) is not installed or not in PATH. Please install gh to check for updates."
        }
        return $false
    }

    $LatestTag = $null
    try {
        $TagName = (gh release view -R moesuito/alano-rought-cut-ai --json tagName --jq .tagName 2>$null)
        if ($TagName -match "v\d+\.\d+\.\d+") {
            $LatestTag = $TagName.Trim()
        }
    } catch {}
    
    if (!$LatestTag) {
        if (!$Silent) {
            Write-Host "No releases found on GitHub." -ForegroundColor Yellow
        }
        return $false
    }
    
    if ($LatestTag -eq $LocalVersion) {
        if (!$Silent) {
            Write-Host "Alano Rough Cut AI is already up-to-date ($LocalVersion)." -ForegroundColor Green
        }
        return $false
    }
    
    # New version found!
    Write-Host "New version found: $LatestTag (Your version: $LocalVersion)" -ForegroundColor Yellow
    Write-Host "Updating system..." -ForegroundColor Cyan
    
    # Download and extract release zip
    $TempDir = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName())
    $TempExtractDir = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    New-Item -ItemType Directory -Path $TempExtractDir -Force | Out-Null
    
    try {
        gh release download $LatestTag -R moesuito/alano-rought-cut-ai --archive=zip --dir $TempDir
        $ZipFile = Get-ChildItem -Path $TempDir -Filter "*.zip" | Select-Object -First 1
        if (!$ZipFile) { throw "Failed to download release zip" }
        
        Expand-Archive -Path $ZipFile.FullName -DestinationPath $TempExtractDir -Force
        $SubDir = Get-ChildItem -Path $TempExtractDir -Directory | Select-Object -First 1
        if (!$SubDir) { throw "Release directory not found inside the zip archive." }
        
        # Clear target folder, keeping .venv and .env
        Get-ChildItem -Path $InstallRoot | Where-Object { $_.Name -ne ".venv" -and $_.Name -ne ".env" } | Remove-Item -Recurse -Force
        
        # Copy extracted files to target folder
        Get-ChildItem -Path $SubDir.FullName | Copy-Item -Destination $InstallRoot -Recurse -Force
        
        # Re-install package editable inside virtual environment to update dependencies if any
        $PipPath = Join-Path $InstallRoot ".venv\Scripts\pip.exe"
        if (Test-Path $PipPath) {
            if (!$Silent) { Write-Host "Updating package dependencies..." -ForegroundColor Cyan }
            & $PipPath install -e $InstallRoot | Out-Null
        }
        
        Write-Host "Successfully updated to version $LatestTag!" -ForegroundColor Green
        return $true
    }
    catch {
        Write-Error "Failed to update: $_"
        return $false
    }
    finally {
        Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
        Remove-Item -Path $TempExtractDir -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
    }
}

$SubCommand = $args[0]

if ($SubCommand -eq "init") {
    # Run a silent update check before starting init
    $Updated = Update-System -Silent $true
    if ($Updated) {
        # Reload and re-execute the init command using the updated script
        $NewScriptPath = Join-Path $InstallRoot "bin\alanocut.ps1"
        if (Test-Path $NewScriptPath) {
            Write-Host "Restarting command with the updated version..." -ForegroundColor Cyan
            & $NewScriptPath init
            exit
        }
    }

    $CurrentDir = (Get-Location).Path
    Write-Host "Initializing rough cut workspace in: $CurrentDir..." -ForegroundColor Cyan

    # 1. Copy helpers directory
    $HelpersDir = Join-Path $InstallRoot "helpers"
    if (Test-Path $HelpersDir) {
        $DestHelpers = Join-Path $CurrentDir "helpers"
        Copy-Item -Path $HelpersDir -Destination $CurrentDir -Recurse -Force
        Write-Host "  -> Copied helper scripts to $DestHelpers" -ForegroundColor Gray
    } else {
        Write-Error "Could not find helpers directory at $HelpersDir"
        exit 1
    }

    # 2. Copy skill and configuration files
    $FilesToCopy = @("SKILL.md", "install.md", "pyproject.toml", ".gitignore", ".env.example", "config.json")
    foreach ($File in $FilesToCopy) {
        $Src = Join-Path $InstallRoot $File
        if (Test-Path $Src) {
            Copy-Item -Path $Src -Destination $CurrentDir -Force
            Write-Host "  -> Copied $File" -ForegroundColor Gray
        }
    }

    # 3. Create raw_video and edit folders
    $RawVideoDir = Join-Path $CurrentDir "raw_video"
    $EditDir = Join-Path $RawVideoDir "edit"
    New-Item -ItemType Directory -Path $RawVideoDir -Force -ErrorAction SilentlyContinue | Out-Null
    New-Item -ItemType Directory -Path $EditDir -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  -> Created raw_video/ and raw_video/edit/ folders" -ForegroundColor Gray

    # 4. Copy .env if not exists (preferring the global configured one)
    $EnvFile = Join-Path $CurrentDir ".env"
    $GlobalEnv = Join-Path $InstallRoot ".env"
    if (!(Test-Path $EnvFile)) {
        if (Test-Path $GlobalEnv) {
            Copy-Item -Path $GlobalEnv -Destination $EnvFile -Force
            Write-Host "  -> Copied configured .env file from global install" -ForegroundColor Gray
        } elseif (Test-Path (Join-Path $InstallRoot ".env.example")) {
            Copy-Item -Path (Join-Path $InstallRoot ".env.example") -Destination $EnvFile -Force
            Write-Host "  -> Created template .env file" -ForegroundColor Gray
        }
    }

    # 5. Register junctions
    $ClaudeLink = Join-Path $Home ".claude\skills\video-use"
    Create-Junction -LinkPath $ClaudeLink -TargetDir $CurrentDir
    Write-Host "  -> Linked skill with Claude Code" -ForegroundColor Gray

    $GeminiLink = Join-Path $Home ".gemini\config\skills\video-use"
    Create-Junction -LinkPath $GeminiLink -TargetDir $CurrentDir
    Write-Host "  -> Linked skill with Antigravity / Gemini" -ForegroundColor Gray

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "       Alano Rough Cut AI Initialized Successfully!         " -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host " Workspace: $CurrentDir" -ForegroundColor Cyan
    Write-Host " Next Steps:" -ForegroundColor White
    Write-Host "   1. Put your raw files inside 'raw_video/'" -ForegroundColor White
    Write-Host "   2. Configure your ELEVENLABS_API_KEY in the '.env' file" -ForegroundColor White
    Write-Host "   3. Open your AI agent (claude, etc.) and type: 'edit these clips'" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
}
elseif ($SubCommand -eq "update") {
    $Updated = Update-System -Silent $false
}
elseif ($SubCommand -eq "-h" -or $SubCommand -eq "--help" -or $SubCommand -eq "help" -or [string]::IsNullOrEmpty($SubCommand)) {
    Show-Help
}
else {
    Write-Host "Unknown command: $SubCommand" -ForegroundColor Red
    Show-Help
    exit 1
}
