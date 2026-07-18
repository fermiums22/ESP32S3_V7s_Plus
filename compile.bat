@echo off
call "%~dp0..\V7s_Plus\build.bat"
if errorlevel 1 exit /b 1
call "%~dp0tools\esphome.bat" compile "%~dp0v7s-plus.yaml"
exit /b %ERRORLEVEL%
