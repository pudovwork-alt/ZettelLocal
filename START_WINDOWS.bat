@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title ZettelLocal

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
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

echo.
echo Python 3 was not found.
echo Install Python 3.11 or newer from python.org.
echo During setup, enable: Add Python to PATH.
echo Then run START_WINDOWS.bat again.
echo.
pause
exit /b 1

:python_ready
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating local Python environment...
    "%PY_EXE%" %PY_ARGS% -m venv ".venv"
    if errorlevel 1 goto :error
) else (
    echo [1/3] Local Python environment found.
)

echo [2/3] Checking dependencies...
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, numpy, httpx, multipart, pypdf, docx" >NUL 2>&1
if errorlevel 1 (
    echo Installing dependencies. Internet is needed only for this step...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
    if errorlevel 1 goto :error
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
    if errorlevel 1 goto :error
)

echo [3/3] Starting ZettelLocal...
".venv\Scripts\python.exe" "run.py"
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" goto :error
exit /b 0

:error
echo.
echo ZettelLocal could not start.
echo Check the messages above, internet access for the first setup,
echo available disk space, and antivirus restrictions.
echo You can run RESET_AND_START_WINDOWS.bat to rebuild the environment.
echo.
pause
exit /b 1
