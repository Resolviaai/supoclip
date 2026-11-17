@echo off
setlocal
cd /d "%~dp0"

echo Running SupoClip Auto-Updater Installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_auto_updater.ps1"

pause
