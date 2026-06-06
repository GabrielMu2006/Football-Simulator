@echo off
setlocal

cd /d "%~dp0"

set APP_NAME=Football Simulator UI v2
set DIST_DIR=dist-windows-ui-v2

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements-ui-v2.txt

if exist "build-windows-ui-v2" rmdir /s /q "build-windows-ui-v2"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"

pyinstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_DIR%" ^
  --workpath "build-windows-ui-v2" ^
  "Football Simulator UI v2 Windows.spec"

echo.
echo Windows build completed:
echo %DIST_DIR%\%APP_NAME%\%APP_NAME%.exe

endlocal
