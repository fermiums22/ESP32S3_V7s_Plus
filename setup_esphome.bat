@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup_esphome.ps1"
exit /b %ERRORLEVEL%
