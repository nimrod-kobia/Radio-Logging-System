@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0app\radio_control_app.py" (
    where pythonw >nul 2>nul
    if %errorlevel%==0 (
        start "" pythonw "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    where pyw >nul 2>nul
    if %errorlevel%==0 (
        start "" pyw "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    where py >nul 2>nul
    if %errorlevel%==0 (
        start "" py "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    where python >nul 2>nul
    if %errorlevel%==0 (
        start "" python "%~dp0app\radio_control_app.py"
        exit /b 0
    )

    echo Python interpreter not found in PATH.
    pause
) else (
    echo App file not found: %~dp0app\radio_control_app.py
    pause
)
