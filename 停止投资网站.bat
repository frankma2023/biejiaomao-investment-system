@echo off
rem Double-click launcher to stop the investment system.
rem Stops: Web front door (8772) + Flask API (8788) + Pinggy tunnel.
cd /d "%~dp0"
title Investment System - Stop
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_all.ps1"
echo.
echo ------------------------------------------------------------
echo  Done. Press any key to close this window.
echo ------------------------------------------------------------
pause >nul
