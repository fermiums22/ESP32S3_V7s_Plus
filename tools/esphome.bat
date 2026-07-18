@echo off
set "ROOT=%~dp0.."
if not exist "%ROOT%\.venv\Scripts\esphome.exe" (
  echo ESPHome Core is not installed. Run setup_esphome.bat first.
  exit /b 1
)
set "ESPHOME_IDF_DEFAULT_TARGETS=esp32s3"
set "IDF_GITHUB_ASSETS=dl.espressif.com/github_assets"
"%ROOT%\.venv\Scripts\esphome.exe" %*
exit /b %ERRORLEVEL%
