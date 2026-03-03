@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0app\LAUNCH_APP.vbs" (
    wscript //nologo "%~dp0app\LAUNCH_APP.vbs"
    exit /b 0
)

if not exist "%~dp0RadioRecordings" mkdir "%~dp0RadioRecordings"
if not exist "%~dp0Runtime" mkdir "%~dp0Runtime"

if exist "%~dp0RadioControlApp.exe" (
    start "" "%~dp0RadioControlApp.exe"
    exit /b 0
)

if exist "%~dp0app\radio_control_app.py" (
    where py >nul 2>nul
    if not errorlevel 1 (
        start "" py "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    where python >nul 2>nul
    if not errorlevel 1 (
        start "" python "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    where python3 >nul 2>nul
    if not errorlevel 1 (
        start "" python3 "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    echo Python interpreter not found in PATH.
    echo Install Python 3 from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
) else (
    echo.
    echo ERROR: App files not found.
    echo Expected: %~dp0RadioControlApp.exe
    echo      or: %~dp0app\radio_control_app.py
    echo.
    echo Copy the full project folder to this machine and try again.
    echo Keep existing RadioRecordings, Runtime, and stations.txt.
    echo.
    pause
)
