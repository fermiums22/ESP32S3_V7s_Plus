@echo off
setlocal
if "%SOKOL9_HA_TOKEN%"=="" (
  echo Set SOKOL9_HA_TOKEN in this cmd window before running probe_home.bat.
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\ha_sokol9_probe.ps1" %*

