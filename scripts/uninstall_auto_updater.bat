@echo off
setlocal

set TASK_NAME=SupoClip_AutoUpdater

echo ========================================================
echo Removing SupoClip Auto-Updater Background Task
echo ========================================================

schtasks /delete /tn "%TASK_NAME%" /f

if %ERRORLEVEL% equ 0 (
    echo.
    echo [SUCCESS] Auto-updater task "%TASK_NAME%" removed.
) else (
    echo.
    echo [INFO] Task not found or already removed.
)

pause
