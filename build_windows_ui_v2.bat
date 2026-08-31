@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows.ps1" %*

if errorlevel 1 (
  echo.
  echo Windows build failed. See messages above.
  exit /b 1
)
endlocal
