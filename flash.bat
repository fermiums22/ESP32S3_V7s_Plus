@echo off
set "PORT=%~1"
if not defined PORT set "PORT=COM9"
call "%~dp0tools\esphome.bat" upload "%~dp0v7s-plus.yaml" --device "%PORT%"
exit /b %ERRORLEVEL%
