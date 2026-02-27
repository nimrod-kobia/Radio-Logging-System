@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: %~nx0 ^<target_install_folder^> [source_folder]
    echo Example: %~nx0 "C:\RadioControl"
    echo Example: %~nx0 "C:\RadioControl" "E:\RadioControl_NewVersion"
    exit /b 1
)

set "TARGET=%~1"
for %%I in ("%TARGET%") do set "TARGET=%%~fI"

set "SOURCE="
if not "%~2"=="" (
    set "SOURCE=%~2"
    for %%I in ("%SOURCE%") do set "SOURCE=%%~fI"
) else (
    if exist "%~dp0payload" (
        set "SOURCE=%~dp0payload"
    ) else (
        if exist "%~dp0..\LAUNCH_APP.bat" (
            set "SOURCE=%~dp0.."
        )
    )
)

if "%SOURCE%"=="" (
    echo Could not resolve source folder.
    echo Expected one of:
    echo   - "%~dp0payload"   ^(update package mode^)
    echo   - parent app folder containing LAUNCH_APP.bat ^(flash copy mode^)
    echo   - explicit 2nd argument source folder
    exit /b 1
)

if not exist "%SOURCE%" (
    echo Source folder not found: "%SOURCE%"
    exit /b 1
)

if not exist "%TARGET%" (
    echo Target folder not found: "%TARGET%"
    exit /b 1
)

set "RUNNING=0"
tasklist /FI "IMAGENAME eq RadioControlApp.exe" | find /I "RadioControlApp.exe" >nul && set "RUNNING=1"
tasklist /FI "IMAGENAME eq rc_backend_service.exe" | find /I "rc_backend_service.exe" >nul && set "RUNNING=1"

if "%RUNNING%"=="1" (
    echo.
    echo Detected running app/backend processes.
    echo Choose when to stop and update:
    echo   [N] Now
    echo   [W] Wait until next top-of-hour, then update
    echo   [C] Cancel
    choice /C NWC /N /M "Select option [N/W/C]: "

    if errorlevel 3 (
        echo Update cancelled.
        exit /b 1
    )

    if errorlevel 2 (
        echo Waiting until next top-of-hour before stopping processes...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$n=Get-Date; $next=$n.Date.AddHours($n.Hour+1); $s=[int][math]::Ceiling(($next-$n).TotalSeconds); if($s -gt 0){ Start-Sleep -Seconds $s }"
    )

    taskkill /F /T /IM RadioControlApp.exe >nul 2>nul
    taskkill /F /T /IM rc_backend_service.exe >nul 2>nul

    if exist "%TARGET%\app\monitor.pid" (
        for /f "usebackq delims=" %%P in ("%TARGET%\app\monitor.pid") do (
            if not "%%P"=="" taskkill /F /T /PID %%P >nul 2>nul
        )
    )

    timeout /t 2 /nobreak >nul
)

echo Applying update to: "%TARGET%"
echo Source: "%SOURCE%"
echo Preserving: RadioRecordings, Runtime, stations.txt

robocopy "%SOURCE%" "%TARGET%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD "RadioRecordings" "Runtime" ".git" "dist" /XF "stations.txt"
set "RC=%errorlevel%"
if %RC% GEQ 8 (
    echo Update failed. robocopy exit code: %RC%
    exit /b %RC%
)

echo Update applied successfully.
echo You can now restart with "%TARGET%\LAUNCH_APP.bat"
exit /b 0
