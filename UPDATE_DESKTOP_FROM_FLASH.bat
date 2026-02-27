@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==============================================
echo   Update Desktop From This Folder
echo ==============================================
echo This will update program files only and keep:
echo   - RadioRecordings
echo   - Runtime
echo   - stations.txt
echo.

set "TARGET="
set /p TARGET=Enter existing install folder on this desktop: 
if "%TARGET%"=="" (
    echo No target entered. Update cancelled.
    pause
    exit /b 1
)

call "%~dp0updates\APPLY_UPDATE_SAFE_WINDOWS.bat" "%TARGET%" "%~dp0"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo Update failed with exit code %RC%.
    pause
    exit /b %RC%
)

echo.
echo Update complete. Start app from: "%TARGET%\LAUNCH_APP.bat"
pause
exit /b 0
