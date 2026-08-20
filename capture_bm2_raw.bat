@echo off
setlocal
title V7s Plus - BM2 raw PDM capture
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\capture_bm2_raw.py" %*
if errorlevel 1 pause
