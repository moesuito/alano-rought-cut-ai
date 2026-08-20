@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%APPDATA%\alano-rought-cut-ai\bin\alanocut.ps1' %*"
