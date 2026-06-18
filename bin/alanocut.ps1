# alanocut CLI Controller for Windows PowerShell

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Split-Path -Parent $ScriptDir

function Show-Help {
    Write-Host "Alano Rough Cut AI CLI - Command Line Utility" -ForegroundColor Green
    Write-Host "Usage:" -ForegroundColor White
    Write-Host "  alanocut init        Initialize current directory as a video rough-cut workspace" -ForegroundColor White
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

$SubCommand = $args[0]

if ($SubCommand -eq "init") {
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
    $FilesToCopy = @("SKILL.md", "install.md", "pyproject.toml", ".gitignore", ".env.example")
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

    # 4. Copy .env if not exists
    $EnvFile = Join-Path $CurrentDir ".env"
    $EnvExample = Join-Path $CurrentDir ".env.example"
    if (!(Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
        Copy-Item -Path $EnvExample -Destination $EnvFile -Force
        Write-Host "  -> Created template .env file" -ForegroundColor Gray
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
elseif ($SubCommand -eq "-h" -or $SubCommand -eq "--help" -or $SubCommand -eq "help" -or [string]::IsNullOrEmpty($SubCommand)) {
    Show-Help
}
else {
    Write-Host "Unknown command: $SubCommand" -ForegroundColor Red
    Show-Help
    exit 1
}
