@echo off
rem Start audiobook tool UI (delegates to start_ui.ps1)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_ui.ps1"