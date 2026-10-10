@echo off
rem Stop audiobook tool UI (delegates to stop_ui.ps1)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_ui.ps1"