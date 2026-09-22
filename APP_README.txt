ZettelLocal 2.12.1 — Windows application build

BUILD_SINGLE_EXE.bat creates:
    dist\ZettelLocal.exe

The resulting EXE:
- opens ZettelLocal in its own Windows application window (WebView2);
- does not require Python on the computer where it is run;
- starts its local FastAPI server internally;
- keeps the database outside the EXE at:
  %LOCALAPPDATA%\ZettelLocal\data\zettel.db
- keeps notes when ZettelLocal.exe is replaced by a newer version.

Windows 10/11 normally includes Microsoft Edge WebView2 Runtime.
Python and Internet are needed only on the computer used to BUILD the EXE.
