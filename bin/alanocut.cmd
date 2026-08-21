@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
if not "%~1"=="" (
    echo Alano Cut no longer accepts subcommands or arguments. Run only: alanocut 1>&2
    exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDATA%\alano-rought-cut-ai\bin\alanocut.ps1"
exit /b %ERRORLEVEL%
