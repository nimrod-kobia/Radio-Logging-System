@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==============================================
echo   Radio Control - Flash Drive Update
echo ==============================================
echo This updates app files only and preserves:
echo   - RadioRecordings
echo   - Runtime
echo   - stations.txt
echo.

set "TARGET="
set /p TARGET=Enter existing install folder path: 
if "%TARGET%"=="" (
    echo No path entered. Update cancelled.
    pause
    exit /b 1
)

call "%~dp0APPLY_UPDATE_SAFE_WINDOWS.bat" "%TARGET%"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo Update failed with exit code %RC%.
    pause
    exit /b %RC%
)

echo.
echo Update complete. You can start the app from the target PC now.
pause
exit /b 0
