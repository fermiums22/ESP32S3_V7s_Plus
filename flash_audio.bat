@echo off
set "PORT=%~1"
if not defined PORT set "PORT=COM23"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\flash_audio.ps1" -Port "%PORT%"
exit /b %ERRORLEVEL%
