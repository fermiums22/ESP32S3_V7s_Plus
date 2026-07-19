@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_stm32.ps1"
exit /b %ERRORLEVEL%
