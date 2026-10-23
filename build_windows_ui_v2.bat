@echo off
rem Convenience wrapper: builds the portable Windows ZIP for the current UI stack.
rem The workspace defaults to the parent of this source folder (E:\FootballSimulator),
rem so a double-click works; extra arguments are forwarded to build_windows.ps1.
setlocal
chcp 65001 >nul
set "HERE=%~dp0"
set "PS1=%HERE%scripts\build_windows.ps1"

if not exist "%PS1%" (
  echo ERROR: missing %PS1%
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo Windows build failed with exit code %RC%. See messages above.
  exit /b 1
)

echo.
echo Windows build finished. See messages above for the ZIP path.
endlocal
exit /b 0
