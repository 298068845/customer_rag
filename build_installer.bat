@echo off
setlocal

cd /d "%~dp0"

set "VERSION=0.1.0"
set "INNO_COMPILER=D:\Inno Setup 6\ISCC.exe"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_installer.ps1" -Version "%VERSION%" -InnoCompiler "%INNO_COMPILER%"

if errorlevel 1 (
    echo.
    echo Build failed. See the error output above.
    pause
    exit /b 1
)

echo.
echo Build completed successfully.
echo Installer output: %~dp0dist\installer
pause
