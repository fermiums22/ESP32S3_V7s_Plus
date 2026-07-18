@echo off
set "PORT=%~1"
if not defined PORT set "PORT=COM23"
call "%~dp0tools\esphome.bat" run "%~dp0v7s-plus.yaml" --device "%PORT%" --no-logs
exit /b %ERRORLEVEL%
