@echo off
cd /d "%~dp0"

:MENU
echo.
echo ====================================================
echo   APEX BOT - Status Check Menu
echo ====================================================
echo   [1] Report - Last 24 hours
echo   [2] Report - Last 7 days
echo   [3] News sentiment check
echo   [4] GPU status
echo   [5] Walk-Forward optimization (now)
echo   [6] PPO training (now)
echo   [0] Exit
echo.
set /p CHOICE=Select: 

if "%CHOICE%"=="1" python main.py --mode report --hours 24   & goto MENU
if "%CHOICE%"=="2" python main.py --mode report --hours 168  & goto MENU
if "%CHOICE%"=="3" python main.py --mode news-check          & goto MENU
if "%CHOICE%"=="4" python main.py --gpu-check                & goto MENU
if "%CHOICE%"=="5" python main.py --mode walk-forward        & goto MENU
if "%CHOICE%"=="6" python main.py --mode ppo-train           & goto MENU
if "%CHOICE%"=="0" exit /b 0

goto MENU
