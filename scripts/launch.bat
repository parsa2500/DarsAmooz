@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
title DarsAmooz - ????
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%
