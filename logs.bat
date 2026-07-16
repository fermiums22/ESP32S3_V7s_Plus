@echo off
set "DEVICE=%~1"
if not defined DEVICE set "DEVICE=v7s-plus.local"
call "%~dp0tools\esphome.bat" logs "%~dp0v7s-plus.yaml" --device "%DEVICE%"
exit /b %ERRORLEVEL%
