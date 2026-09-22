@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Backup ZettelLocal Data

if not exist "data" (
    echo The data folder was not found.
    pause
    exit /b 1
)

if exist "data_backup" (
    echo The data_backup folder already exists.
    echo Rename or remove it before creating a new backup.
    pause
    exit /b 1
)

xcopy "data" "data_backup\" /E /I /H /K /Y >NUL
if errorlevel 1 (
    echo Backup failed.
    pause
    exit /b 1
)

echo Backup created: data_backup
pause
exit /b 0
