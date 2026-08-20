@echo off
setlocal
title V7s Plus - PDM microphone meter
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\mic_level_console.py" %*
if errorlevel 1 pause
