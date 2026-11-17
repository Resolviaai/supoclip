@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo SupoClip Auto-Updater
echo ========================================================
python "%~dp0auto_updater.py" %*
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% neq 0 (
    echo [ERROR] Auto-updater exited with code %EXIT_CODE%. Check auto_updater.log for details.
) else (
    echo [OK] Auto-updater finished successfully.
)
exit /b %EXIT_CODE%
