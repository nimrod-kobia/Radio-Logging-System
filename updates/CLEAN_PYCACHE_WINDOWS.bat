@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo ==============================================
echo   Clean Python Cache Files
echo ==============================================
echo Root: %CD%
echo.

set /a DIR_COUNT=0
set /a FILE_COUNT=0

for /d /r %%D in (__pycache__) do (
    if exist "%%D" (
        rmdir /s /q "%%D"
        set /a DIR_COUNT+=1
    )
)

for /r %%F in (*.pyc) do (
    if exist "%%F" (
        del /f /q "%%F" >nul 2>nul
        set /a FILE_COUNT+=1
    )
)

for /r %%F in (*.pyo) do (
    if exist "%%F" (
        del /f /q "%%F" >nul 2>nul
        set /a FILE_COUNT+=1
    )
)

echo Removed __pycache__ folders: %DIR_COUNT%
echo Removed bytecode files: %FILE_COUNT%
echo Done.
exit /b 0
