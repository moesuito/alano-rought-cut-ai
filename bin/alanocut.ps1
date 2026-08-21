# Alano Cut interactive CLI controller for Windows PowerShell

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Split-Path -Parent $ScriptDir
$env:PYTHONPATH = "$InstallRoot;$InstallRoot\helpers;$env:PYTHONPATH"

if ($args.Count -gt 0) {
    Write-Error "Alano Cut no longer accepts subcommands or arguments. Run only: alanocut"
    exit 2
}

$PythonPath = Join-Path $InstallRoot ".venv\Scripts\python.exe"
if (!(Test-Path $PythonPath)) {
    Write-Error "The global Alano Cut runtime is incomplete. Re-run install.ps1."
    exit 1
}

$CliPath = Join-Path $InstallRoot "helpers\interactive_cli.py"
if (!(Test-Path $CliPath)) {
    Write-Error "The Alano Cut interactive CLI is missing: $CliPath"
    exit 1
}

& $PythonPath $CliPath
exit $LASTEXITCODE
