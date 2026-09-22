@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Build ZettelLocal Windows App

echo ============================================================
echo  ZettelLocal - build one-file Windows application
echo ============================================================

set "PY_EXE="
set "PY_ARGS="
where py.exe >NUL 2>&1
if not errorlevel 1 (
  set "PY_EXE=py.exe"
  set "PY_ARGS=-3"
  goto :python_ready
)
where python.exe >NUL 2>&1
if not errorlevel 1 (
  set "PY_EXE=python.exe"
  goto :python_ready
)

echo Python 3.11+ is required only to BUILD the EXE.
echo The finished ZettelLocal.exe will not require Python.
echo Install Python from python.org, then run this file again.
if /I not "%~1"=="--no-pause" pause
exit /b 1

:python_ready
if not exist ".buildvenv\Scripts\python.exe" (
  echo [1/4] Creating isolated build environment...
  "%PY_EXE%" %PY_ARGS% -m venv ".buildvenv"
  if errorlevel 1 goto :error
)

echo [2/4] Installing build dependencies...
".buildvenv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip >NUL
".buildvenv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller pywebview
if errorlevel 1 goto :error

echo [3/4] Building standalone ZettelLocal.exe...
".buildvenv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "ZettelLocal" ^
  --icon "assets\zettellocal.ico" ^
  --add-data "app\static;app\static" ^
  --hidden-import app.main ^
  --hidden-import app.database ^
  --hidden-import app.ai_engine ^
  --hidden-import app.smart_tools ^
  --hidden-import pypdf ^
  --hidden-import docx ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --collect-all webview ^
  desktop_launcher.py
if errorlevel 1 goto :error

echo [4/4] Done.
echo.
echo EXE: %CD%\dist\ZettelLocal.exe
echo User data: %%LOCALAPPDATA%%\ZettelLocal\data\zettel.db
echo.
if /I not "%~1"=="--no-pause" pause
exit /b 0

:error
echo.
echo Build failed. Review the messages above.
if /I not "%~1"=="--no-pause" pause
exit /b 1
