@echo off
if "%~4"=="" (
  echo Usage: %~nx0 HOST TOKEN USERNAME PASSWORD [FIRMWARE.bin]
  exit /b 2
)
set "FIRMWARE=%~5"
if not defined FIRMWARE set "FIRMWARE=%~dp0..\V7s_Plus\Debug\V7s_Plus.bin"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\upload_stm32_firmware.ps1" -TargetHost "%~1" -Token "%~2" -Username "%~3" -Password "%~4" -Firmware "%FIRMWARE%"
exit /b %ERRORLEVEL%
