@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Reset ZettelLocal

echo This removes only the local Python environment.
echo Your notes in the data folder will not be deleted.
echo.

if exist ".venv" rmdir /s /q ".venv"
if exist ".venv" (
    echo Could not remove .venv. Close ZettelLocal and try again.
    pause
    exit /b 1
)

call "START_WINDOWS.bat"
exit /b %ERRORLEVEL%
