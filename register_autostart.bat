@echo off
cd /d "%~dp0"

set BOT_DIR=%~dp0
set BOT_DIR=%BOT_DIR:~0,-1%

schtasks /delete /tn "APEX_BOT" /f > nul 2>&1

schtasks /create /tn "APEX_BOT" /tr ""%BOT_DIR%\start_apex.bat"" /sc ONLOGON /rl HIGHEST /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS: Auto-start registered.
    echo APEX BOT will start automatically on Windows login.
    echo.
    echo To remove: schtasks /delete /tn APEX_BOT /f
) else (
    echo.
    echo FAILED: Run this file as Administrator.
    echo Right-click - Run as administrator
)

echo.
pause
