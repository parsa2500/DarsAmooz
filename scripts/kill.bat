@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
title DarsAmooz - ????
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill.ps1" %*
echo.
pause
exit /b 0
