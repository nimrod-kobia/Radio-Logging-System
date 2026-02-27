@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

if not exist "dist\portable\RadioControlApp.exe" (
    echo Portable build not found.
    echo Run updates\BUILD_NO_PYTHON_WINDOWS.bat first.
    exit /b 1
)

echo Preparing update package...
if exist "dist\update-package" rmdir /s /q "dist\update-package"
mkdir "dist\update-package\payload"

robocopy "dist\portable" "dist\update-package\payload" /E /R:2 /W:1 /XD "RadioRecordings" "Runtime" /XF "stations.txt" >nul
set "RC=%errorlevel%"
if %RC% GEQ 8 (
    echo Failed while preparing payload. robocopy exit code: %RC%
    exit /b %RC%
)

copy /Y "updates\APPLY_UPDATE_SAFE_WINDOWS.bat" "dist\update-package\APPLY_UPDATE_SAFE_WINDOWS.bat" >nul
copy /Y "updates\FLASH_DRIVE_UPDATE.bat" "dist\update-package\FLASH_DRIVE_UPDATE.bat" >nul
if exist "VERSION.txt" copy /Y "VERSION.txt" "dist\update-package\VERSION.txt" >nul

if exist "dist\update-package.zip" del /f /q "dist\update-package.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\update-package\*' -DestinationPath 'dist\update-package.zip' -Force"

echo Update package ready:
echo   dist\update-package
echo   dist\update-package.zip
echo.
echo On target device:
echo   1) Extract update-package.zip
echo   2) Double-click FLASH_DRIVE_UPDATE.bat (recommended)
echo      or run APPLY_UPDATE_SAFE_WINDOWS.bat ^<existing install folder^>
exit /b 0
