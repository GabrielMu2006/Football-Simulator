@echo off
rem ===========================================================================
rem  Football Simulator - foreground acceptance (Windows, portable build)
rem
rem  Use this when the build was verified over a remote/SSH session and you want
rem  the same checks on a real interactive desktop, plus a normal manual run.
rem
rem  What it does:
rem    1. finds the extracted portable app (E:\FootballSimulator\app\...)
rem    2. creates an isolated data root under E:\FootballSimulator\.tmp\foreground-*
rem       (your real %APPDATA%\Football Simulator saves are NOT touched)
rem    3. runs a scripted route tour with QT_QPA_PLATFORM=windows and saves
rem       screenshots + JSON under E:\FootballSimulator\artifacts\windows
rem    4. launches the app so you can click through it yourself
rem
rem  Everything stays inside E:\FootballSimulator.
rem ===========================================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
set "WS=E:\FootballSimulator"
set "PY=%WS%\.runtime\python-3.13.5\python.exe"
set "TOOL=%WS%\source\packaging\windows\accept_package.py"
set "ART=%WS%\artifacts\windows"

echo.
echo === Football Simulator foreground acceptance ===
echo Workspace: %WS%
echo.

rem --- 1. locate the newest extracted app -----------------------------------
set "APP="
for /f "delims=" %%D in ('dir /b /ad /o-d "%WS%\app" 2^>nul') do (
    if not defined APP if exist "%WS%\app\%%D\Football Simulator.exe" set "APP=%WS%\app\%%D"
)
if not defined APP (
    for /f "delims=" %%D in ('dir /b /ad /o-d "%WS%\build\staging" 2^>nul') do (
        if not defined APP if exist "%WS%\build\staging\%%D\Football Simulator.exe" set "APP=%WS%\build\staging\%%D"
    )
)
if not defined APP (
    echo ERROR: no extracted app found under "%WS%\app".
    echo        Extract the ZIP first, then run this script again.
    pause
    exit /b 1
)
echo App: !APP!

rem --- 2. isolated data root ------------------------------------------------
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "STAMP=%%I"
set "STAMP=%STAMP:~0,14%"
if "%STAMP%"=="" set "STAMP=manual"
set "DATA=%WS%\.tmp\foreground-%STAMP%"
if not exist "%DATA%" mkdir "%DATA%"
echo Data root: !DATA!
echo.

rem --- 3. scripted route tour ----------------------------------------------
if not exist "%PY%" goto no_tour
if not exist "%TOOL%" goto no_tour
echo Running scripted route tour (native Qt, screenshots)...
"%PY%" "%TOOL%" --workspace "%WS%" --app "!APP!" --data-root "!DATA%\tour" --result "%ART%\foreground-accept.json" --platform windows --routes "dashboard,matches,competitions,teams,players,history,records,honors,transfers,draft,saves,season,weekly:7" --sizes "1280x800,1440x900,1680x980" --exit-ms 3000 --screenshot-dir "%ART%\shots-foreground" --prepare-save accept --prepare-weeks 6 --reuse-data-root --source "%WS%\source"
if errorlevel 1 (
    echo WARNING: scripted tour reported failures - see "%ART%\foreground-accept.json".
) else (
    echo Tour OK. JSON: "%ART%\foreground-accept.json"
)
echo Screenshots: "%ART%\shots-foreground"
goto launch

:no_tour
echo NOTE: clean runtime or acceptance tool missing; skipping the scripted tour.

:launch
rem --- 4. interactive run ---------------------------------------------------
echo.
echo Launching the app with the isolated data root above.
echo Check: 40 badges, 8 trophies, Chinese/English titles, Ctrl+K, Alt+Left/Right,
echo        F5, table click/double-click, dialogs, import/export file pickers.
echo Close the app when finished.
echo.
set "FS_DATA_ROOT=!DATA!\app"
if not exist "!FS_DATA_ROOT!" mkdir "!FS_DATA_ROOT!"
start "" /wait "!APP!\Football Simulator.exe"
echo.
echo Finished. Evidence is in "%ART%".
pause
endlocal
exit /b 0
