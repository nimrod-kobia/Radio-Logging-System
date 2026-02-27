@echo off
setlocal
cd /d "%~dp0.."

echo [1/5] Checking Python...
where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto HAVE_PY
)
where python >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=python"
    goto HAVE_PY
)
echo Python not found on this build machine.
echo Install Python 3 first, then rerun this script.
pause
exit /b 1

:HAVE_PY
echo [2/5] Installing/Updating PyInstaller...
%PY_CMD% -m pip install --upgrade pip pyinstaller
if errorlevel 1 (
    echo Failed to install PyInstaller.
    pause
    exit /b 1
)

echo [3/5] Building executables...
%PY_CMD% -m PyInstaller --noconfirm --clean --windowed --name RadioControlApp app\radio_control_app.py
if errorlevel 1 (
    echo Failed to build RadioControlApp.exe
    pause
    exit /b 1
)

%PY_CMD% -m PyInstaller --noconfirm --clean --name rc_backend_service app\rc_backend_service.py
if errorlevel 1 (
    echo Failed to build rc_backend_service.exe
    pause
    exit /b 1
)

echo [4/5] Preparing portable output folder...
if exist "dist\portable" rmdir /s /q "dist\portable"
mkdir "dist\portable"

copy /Y "dist\RadioControlApp\RadioControlApp.exe" "dist\portable\" >nul
copy /Y "dist\rc_backend_service\rc_backend_service.exe" "dist\portable\" >nul
copy /Y "LAUNCH_APP.bat" "dist\portable\" >nul
if exist "stations.txt" copy /Y "stations.txt" "dist\portable\" >nul

if not exist "dist\portable\Runtime" mkdir "dist\portable\Runtime"
if not exist "dist\portable\RadioRecordings" mkdir "dist\portable\RadioRecordings"

echo [5/5] Creating zip bundle...
if exist "dist\portable.zip" del /f /q "dist\portable.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\portable\*' -DestinationPath 'dist\portable.zip' -Force"

echo.
echo Build complete.
echo Portable folder: dist\portable
echo Portable zip: dist\portable.zip
echo.
echo NOTE: Target machine also needs ffmpeg at C:\ffmpeg\bin\ffmpeg.exe
pause
