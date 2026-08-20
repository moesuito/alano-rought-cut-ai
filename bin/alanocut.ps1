# alanocut CLI Controller for Windows PowerShell

$env:PYTHONIOENCODING = "utf-8"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Split-Path -Parent $ScriptDir

function Show-Help {
    Write-Host "Alano Rough Cut AI CLI - Command Line Utility (v0.5.0)" -ForegroundColor Green
    Write-Host "Usage:" -ForegroundColor White
    Write-Host "  alanocut             Launch Interactive Terminal UI in current folder (Default)" -ForegroundColor Cyan
    Write-Host "  alanocut cut         Execute autonomous rough cut non-interactively" -ForegroundColor White
    Write-Host "  alanocut clean       Clean AppData cache to free disk space" -ForegroundColor White
    Write-Host "  alanocut sessions    List previous editing sessions saved in AppData" -ForegroundColor White
    Write-Host "  alanocut configure   Configure ElevenLabs, AssemblyAI, or local Whisper transcription" -ForegroundColor White
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

function Convert-VersionTag {
    param(
        [string]$Tag
    )

    if ([string]::IsNullOrWhiteSpace($Tag)) {
        return [version]"0.0.0"
    }

    $Clean = $Tag.Trim()
    if ($Clean.StartsWith("v", [System.StringComparison]::OrdinalIgnoreCase)) {
        $Clean = $Clean.Substring(1)
    }

    try {
        return [version]$Clean
    } catch {
        return [version]"0.0.0"
    }
}

function Test-RemoteVersionIsNewer {
    param(
        [string]$LatestTag,
        [string]$LocalVersion
    )

    $Latest = Convert-VersionTag $LatestTag
    $Local = Convert-VersionTag $LocalVersion
    return ($Latest.CompareTo($Local) -gt 0)
}

function Get-AlanoPython {
    $PythonPath = Join-Path $InstallRoot ".venv\Scripts\python.exe"
    if (Test-Path $PythonPath) { return $PythonPath }
    return "python"
}

function Invoke-SetupWizard {
    param([string[]]$WizardArguments)
    $WizardPath = Join-Path $InstallRoot "helpers\setup_wizard.py"
    if (!(Test-Path $WizardPath)) {
        Write-Error "Setup wizard is missing: $WizardPath"
        return 1
    }
    $PythonPath = Get-AlanoPython
    # Send the wizard's normal output to the terminal instead of this function's
    # success stream. Callers capture only the numeric exit code.
    & $PythonPath $WizardPath @WizardArguments | Out-Host
    return $LASTEXITCODE
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

    if (!(Test-RemoteVersionIsNewer -LatestTag $LatestTag -LocalVersion $LocalVersion)) {
        if (!$Silent) {
            Write-Host "Local version $LocalVersion is newer than the latest release $LatestTag. Skipping update." -ForegroundColor Yellow
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
        
        # Clear target folder, keeping credentials, light environment, and preference.
        Get-ChildItem -Path $InstallRoot | Where-Object { $_.Name -ne ".venv" -and $_.Name -ne ".env" -and $_.Name -ne "user-settings.json" } | Remove-Item -Recurse -Force
        
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
            & $NewScriptPath @args
            exit
        }
    }

    $CurrentDir = (Get-Location).Path
    Write-Host "Initializing rough cut workspace in: $CurrentDir..." -ForegroundColor Cyan

    # Resolve and provision the provider before writing any workspace artifact.
    $InitProvider = $null
    $InitDiarization = $null
    $InitNonInteractive = $false
    for ($ArgumentIndex = 1; $ArgumentIndex -lt $args.Count; $ArgumentIndex++) {
        switch ($args[$ArgumentIndex]) {
            "--provider" {
                $ArgumentIndex++
                if ($ArgumentIndex -ge $args.Count) { Write-Error "--provider requires a value"; exit 1 }
                $InitProvider = $args[$ArgumentIndex]
            }
            "--diarization" {
                $ArgumentIndex++
                if ($ArgumentIndex -ge $args.Count) { Write-Error "--diarization requires a value"; exit 1 }
                $InitDiarization = $args[$ArgumentIndex]
            }
            "--non-interactive" { $InitNonInteractive = $true }
            default { Write-Error "Unknown init option: $($args[$ArgumentIndex])"; exit 1 }
        }
    }
    $SettingsTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("alanocut-settings-" + [System.Guid]::NewGuid().ToString() + ".json")
    try {
        $WizardArguments = @("init", "--workspace", $CurrentDir, "--settings-output", $SettingsTemp)
        if ($InitProvider) { $WizardArguments += @("--provider", $InitProvider) }
        if ($InitDiarization) { $WizardArguments += @("--diarization", $InitDiarization) }
        if ($InitNonInteractive) { $WizardArguments += "--non-interactive" }
        $WizardExit = Invoke-SetupWizard -WizardArguments $WizardArguments
        if ($WizardExit -ne 0 -or !(Test-Path $SettingsTemp)) {
            Write-Error "Workspace was not changed because provider setup did not complete."
            exit 1
        }
    } catch {
        Remove-Item -LiteralPath $SettingsTemp -Force -ErrorAction SilentlyContinue
        throw
    }

    # 1. Copy helpers directory
    $HelpersDir = Join-Path $InstallRoot "helpers"
    if (Test-Path $HelpersDir) {
        $DestHelpers = Join-Path $CurrentDir "helpers"
        Copy-Item -Path $HelpersDir -Destination $CurrentDir -Recurse -Force
        Get-ChildItem -Path $DestHelpers -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
        Write-Host "  -> Copied helper scripts to $DestHelpers" -ForegroundColor Gray
    } else {
        Write-Error "Could not find helpers directory at $HelpersDir"
        exit 1
    }

    # 2. Copy agent instructions and configuration files
    $FilesToCopy = @("AGENTS.md", "SKILL.md", "pyproject.toml", ".gitignore", "config.json")
    foreach ($File in $FilesToCopy) {
        $Src = Join-Path $InstallRoot $File
        if (Test-Path $Src) {
            Copy-Item -Path $Src -Destination $CurrentDir -Force
            Write-Host "  -> Copied $File" -ForegroundColor Gray
        }
    }

    $AgentsDir = Join-Path $InstallRoot ".agents"
    if (Test-Path $AgentsDir) {
        Copy-Item -Path $AgentsDir -Destination $CurrentDir -Recurse -Force
        Write-Host "  -> Copied modular agent instructions (.agents/)" -ForegroundColor Gray
    }

    # 3. Create raw_video and edit folders
    $RawVideoDir = Join-Path $CurrentDir "raw_video"
    $EditDir = Join-Path $RawVideoDir "edit"
    New-Item -ItemType Directory -Path $RawVideoDir -Force -ErrorAction SilentlyContinue | Out-Null
    New-Item -ItemType Directory -Path $EditDir -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  -> Created raw_video/ and raw_video/edit/ folders" -ForegroundColor Gray

    # 4. Persist provider choice without copying global credentials into the project.
    $WorkspaceSettings = Join-Path $CurrentDir "alanocut.json"
    Copy-Item -LiteralPath $SettingsTemp -Destination $WorkspaceSettings -Force
    Remove-Item -LiteralPath $SettingsTemp -Force -ErrorAction SilentlyContinue
    Write-Host "  -> Saved workspace transcription provider in alanocut.json" -ForegroundColor Gray

    # 5. Register junctions
    $ClaudeLink = Join-Path $Home ".claude\skills\video-use"
    Create-Junction -LinkPath $ClaudeLink -TargetDir $CurrentDir
    Write-Host "  -> Linked skill with Claude Code" -ForegroundColor Gray

    $GeminiLink = Join-Path $Home ".gemini\config\skills\video-use"
    Create-Junction -LinkPath $GeminiLink -TargetDir $CurrentDir
    Write-Host "  -> Linked skill with Antigravity / Gemini" -ForegroundColor Gray

    # 6. Link shared Python runtime (.venv) into workspace
    $SharedVenv = Join-Path $InstallRoot ".venv"
    $WorkspaceVenv = Join-Path $CurrentDir ".venv"
    
    if (!(Test-Path $SharedVenv)) {
        Write-Host "Shared runtime missing. Initializing in $SharedVenv..." -ForegroundColor Cyan
        try {
            python -m venv $SharedVenv
            $SharedPip = Join-Path $SharedVenv "Scripts\pip.exe"
            & $SharedPip install -e $InstallRoot | Out-Null
            Write-Host "  -> Shared runtime created successfully!" -ForegroundColor Gray
        } catch {
            Write-Error "Failed to initialize shared runtime: $_"
        }
    }

    $NormalizedCurrent = [System.IO.Path]::GetFullPath($CurrentDir).TrimEnd('\')
    $NormalizedInstall = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    if ([string]::Compare($NormalizedCurrent, $NormalizedInstall, [System.StringComparison]::OrdinalIgnoreCase) -ne 0) {
        Create-Junction -LinkPath $WorkspaceVenv -TargetDir $SharedVenv
        Write-Host "  -> Linked shared Python runtime (.venv)" -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "       Alano Rough Cut AI Initialized Successfully!         " -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host " Workspace: $CurrentDir" -ForegroundColor Cyan
    Write-Host " Next Steps:" -ForegroundColor White
    Write-Host "   1. Put your raw files inside 'raw_video/'" -ForegroundColor White
    Write-Host "   2. Open your AI agent, read AGENTS.md, and type: 'edit these clips'" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
}
elseif ($SubCommand -eq "cut") {
    $PythonPath = Get-AlanoPython
    $OrchestratorPath = Join-Path $InstallRoot "helpers\orchestrator.py"
    $CutArgs = @()
    for ($i = 1; $i -lt $args.Count; $i++) {
        $CutArgs += $args[$i]
    }
    & $PythonPath $OrchestratorPath @CutArgs
    exit $LASTEXITCODE
}
elseif ($SubCommand -eq "clean") {
    $PythonPath = Get-AlanoPython
    $CliPath = Join-Path $InstallRoot "helpers\interactive_cli.py"
    & $PythonPath $CliPath "clean"
    exit $LASTEXITCODE
}
elseif ($SubCommand -eq "sessions") {
    $PythonPath = Get-AlanoPython
    $CliPath = Join-Path $InstallRoot "helpers\interactive_cli.py"
    & $PythonPath $CliPath "sessions"
    exit $LASTEXITCODE
}
elseif ($SubCommand -eq "update") {
    $Updated = Update-System -Silent $false
    if ($Updated) {
        $WizardExit = Invoke-SetupWizard -WizardArguments @("migrate")
        if ($WizardExit -ne 0) { exit $WizardExit }
    }
}
elseif ($SubCommand -eq "configure") {
    $WizardExit = Invoke-SetupWizard -WizardArguments @("configure")
    exit $WizardExit
}
elseif ($SubCommand -eq "setup-transcription") {
    $WizardExit = Invoke-SetupWizard -WizardArguments @("setup")
    exit $WizardExit
}
elseif ($SubCommand -eq "transcription-doctor") {
    $WizardExit = Invoke-SetupWizard -WizardArguments @("doctor")
    exit $WizardExit
}
elseif ($SubCommand -eq "-h" -or $SubCommand -eq "--help" -or $SubCommand -eq "help") {
    Show-Help
}
elseif ([string]::IsNullOrEmpty($SubCommand)) {
    # Default behavior: Launch Interactive Terminal UI
    $PythonPath = Get-AlanoPython
    $CliPath = Join-Path $InstallRoot "helpers\interactive_cli.py"
    & $PythonPath $CliPath
    exit $LASTEXITCODE
}
else {
    Write-Host "Unknown command: $SubCommand" -ForegroundColor Red
    Show-Help
    exit 1
}
