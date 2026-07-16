@echo off
set "ROOT=%~dp0.."
if not exist "%ROOT%\.venv\Scripts\esphome.exe" (
  echo ESPHome Core is not installed. Run setup_esphome.bat first.
  exit /b 1
)
"%ROOT%\.venv\Scripts\esphome.exe" %*
exit /b %ERRORLEVEL%
