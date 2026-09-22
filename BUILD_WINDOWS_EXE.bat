@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Build ZettelLocal EXE

if not exist ".venv\Scripts\python.exe" (
    echo Run START_WINDOWS.bat once before building the EXE.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --name ZettelLocal --onedir --console ^
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
  "run.py"
if errorlevel 1 goto :error

if not exist "dist\ZettelLocal\data" mkdir "dist\ZettelLocal\data"
copy /Y "requirements.txt" "dist\ZettelLocal\requirements.txt" >NUL

echo.
echo Build complete: dist\ZettelLocal\ZettelLocal.exe
pause
exit /b 0

:error
echo.
echo EXE build failed. Review the messages above.
pause
exit /b 1
