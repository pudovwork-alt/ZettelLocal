@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Build ZettelLocal Setup

echo ============================================================
echo  ZettelLocal 2.12.1 - Windows Setup builder
echo ============================================================
echo.

call BUILD_SINGLE_EXE.bat --no-pause
if errorlevel 1 goto :error

set "ISCC="
for %%P in (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do (
  if exist "%%~P" set "ISCC=%%~P"
)

if not defined ISCC (
  where winget.exe >NUL 2>&1
  if not errorlevel 1 (
    echo.
    echo Inno Setup not found. Installing it with winget...
    winget install --id JRSoftware.InnoSetup -e --source winget --accept-source-agreements --accept-package-agreements
    for %%P in (
      "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
      "%ProgramFiles%\Inno Setup 6\ISCC.exe"
      "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    ) do (
      if exist "%%~P" set "ISCC=%%~P"
    )
  )
)

if not defined ISCC (
  echo.
  echo ERROR: Inno Setup 6 was not found.
  echo Install Inno Setup 6 and run BUILD_INSTALLER.bat again.
  goto :error
)

echo.
echo Building Setup.exe...
"%ISCC%" "installer\ZettelLocal.iss"
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  DONE
echo ============================================================
echo Installer:
echo   %CD%\dist-installer\ZettelLocal_Setup_2.12.1.exe
echo.
echo User database remains here after updates/uninstall:
echo   %%LOCALAPPDATA%%\ZettelLocal\data\zettel.db
echo.
pause
exit /b 0

:error
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
