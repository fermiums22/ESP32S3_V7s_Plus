@echo off
setlocal
title V7s Plus - 10 second microphone recording
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\record_mics_10s.py" %*
pause
