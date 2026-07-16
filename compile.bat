@echo off
call "%~dp0tools\esphome.bat" compile "%~dp0v7s-plus.yaml"
exit /b %ERRORLEVEL%
