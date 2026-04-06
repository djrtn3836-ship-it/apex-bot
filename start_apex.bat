@echo off
cd /d "%~dp0"

:LOOP
echo.
echo ====================================================
echo   APEX BOT v2.0.0 - Auto Restart Launcher
echo ====================================================
echo   Start: %date% %time%
echo.

if not exist ".env" (
    echo ERROR: .env file not found
    echo Run: python main.py --setup
    pause
    exit /b 1
)

python main.py --mode paper

set EXIT=%errorlevel%
if %EXIT% neq 0 (
    echo.
    echo WARNING: Abnormal exit (code: %EXIT%)
    echo Restarting in 5 seconds... Press Ctrl+C to cancel
    timeout /t 5 /nobreak > nul
    goto LOOP
) else (
    echo.
    echo APEX BOT stopped normally.
)
