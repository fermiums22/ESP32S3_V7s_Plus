@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_ha_media.ps1" %*
exit /b %ERRORLEVEL%
