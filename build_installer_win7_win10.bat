@echo off
setlocal

cd /d "%~dp0"

set "VERSION=0.1.0"
set "INNO_COMPILER=D:\Inno Setup 6\ISCC.exe"
set "BUILD_LOG=%~dp0logs\build-installer-win7-win10.log"

echo Windows 7 SP1 / Windows 10 x64 compatibility build
echo Python 3.8 is downloaded into the project build cache automatically when needed.
echo Build log: %BUILD_LOG%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_installer_win7_win10.ps1" -Version "%VERSION%" -InnoCompiler "%INNO_COMPILER%"

if errorlevel 1 (
    echo.
    echo Build failed. See the error output and log above.
    pause
    exit /b 1
)

echo.
echo Compatibility installer created in: %~dp0dist\installer-win7-win10
pause
