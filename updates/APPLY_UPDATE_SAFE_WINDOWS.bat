@echo off
setlocal EnableExtensions EnableDelayedExpansion
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

set "MANIFEST=%SOURCE%\update_manifest.json"
if exist "%MANIFEST%" (
    echo Found update manifest. Verifying payload integrity...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0VERIFY_UPDATE_MANIFEST.ps1" -SourceDir "%SOURCE%" -ManifestFile "%MANIFEST%"
    set "VERIFY_RC=!errorlevel!"
    if not "!VERIFY_RC!"=="0" (
        echo.
        echo Update validation failed. Aborting update.
        echo Manifest file: "%MANIFEST%"
        exit /b !VERIFY_RC!
    )
) else (
    echo No update manifest found. Proceeding in legacy mode without checksum validation.
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
set "RC=!errorlevel!"
if !RC! GEQ 8 (
    echo Update failed. robocopy exit code: !RC!
    exit /b !RC!
)

echo Update applied successfully.
if exist "%TARGET%\update_manifest.json" (
    del /f /q "%TARGET%\update_manifest.json" >nul 2>nul
    if exist "%TARGET%\update_manifest.json" (
        echo Warning: Could not remove "%TARGET%\update_manifest.json" automatically.
    ) else (
        echo Removed target update_manifest.json.
    )
)
if exist "%TARGET%\dist" (
    rmdir /s /q "%TARGET%\dist" >nul 2>nul
    if exist "%TARGET%\dist" (
        echo Warning: Could not remove "%TARGET%\dist" automatically.
    ) else (
        echo Cleaned up target dist folder.
    )
)
set "LAUNCH_BAT=%TARGET%\LAUNCH_APP.bat"
if exist "%LAUNCH_BAT%" (
    choice /C YN /N /M "Start app now? [Y/N]: "
    if errorlevel 2 (
        echo You can restart later with "%LAUNCH_BAT%"
    ) else (
        start "" "%LAUNCH_BAT%"
        echo App launch requested.
    )
) else (
    echo Launch file not found: "%LAUNCH_BAT%"
)
exit /b 0
