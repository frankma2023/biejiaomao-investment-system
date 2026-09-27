@echo off
rem Double-click launcher for the investment system.
rem Starts: Flask API (8788) + Web front door (8772) + Pinggy tunnel.
cd /d "%~dp0"
title Investment System - Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_all.ps1"
echo.
echo ------------------------------------------------------------
echo  Script exited. Press any key to close this window.
echo ------------------------------------------------------------
pause >nul
