@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHON_EXE=D:\cltkfq_tts\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
cd /d "%~dp0"
"%PYTHON_EXE%" "%~dp0install_speaker_model.py"
if errorlevel 1 pause
