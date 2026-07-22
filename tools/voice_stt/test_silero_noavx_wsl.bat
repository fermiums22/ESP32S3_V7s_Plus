@echo off
chcp 65001 >nul
setlocal

for /f "delims=" %%I in ('wsl.exe -d Ubuntu -- wslpath -u "%~dp0."') do set "WSL_DIR=%%I"
if not defined WSL_DIR (
    echo Не удалось открыть WSL-дистрибутив Ubuntu.
    pause
    exit /b 1
)

wsl.exe -d Ubuntu -- bash "%WSL_DIR%/test_silero_noavx_wsl.sh"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Тест завершился с ошибкой %RESULT%.
pause
exit /b %RESULT%
