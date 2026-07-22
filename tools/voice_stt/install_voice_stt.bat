@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements-stt.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" install_speaker_model.py
if errorlevel 1 goto :error
".venv\Scripts\python.exe" install_silero_vad.py
if errorlevel 1 goto :error

echo.
echo Установка завершена. Создай audio_secrets.txt и запусти start_voice_stt.bat
exit /b 0

:error
echo.
echo Ошибка установки.
pause
exit /b 1
