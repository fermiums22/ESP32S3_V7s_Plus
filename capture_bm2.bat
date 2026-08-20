@echo off
setlocal
title V7s Plus - BM2 raw PCM capture
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\capture_bm2_pcm.py" %*
if errorlevel 1 pause
