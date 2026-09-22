#define MyAppName "ZettelLocal"
#define MyAppVersion "2.12.1"
#define MyAppPublisher "ZettelLocal"
#define MyAppExeName "ZettelLocal.exe"

[Setup]
AppId={{A62F07EA-6BE6-4F70-BA68-2E09E211C789}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ZettelLocal
DefaultGroupName=ZettelLocal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=ZettelLocal_Setup_2.12.1
SetupIconFile=..\assets\zettellocal.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion=2.12.1.0.0
VersionInfoProductName=ZettelLocal
VersionInfoProductVersion=2.12.1
VersionInfoDescription=ZettelLocal local knowledge base

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
Source: "..\dist\ZettelLocal.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ZettelLocal"; Filename: "{app}\ZettelLocal.exe"; WorkingDir: "{app}"; IconFilename: "{app}\ZettelLocal.exe"
Name: "{autodesktop}\ZettelLocal"; Filename: "{app}\ZettelLocal.exe"; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\ZettelLocal.exe"

[Run]
Filename: "{app}\ZettelLocal.exe"; Description: "Запустить ZettelLocal"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Намеренно НЕ удаляем %LOCALAPPDATA%\ZettelLocal\data.
; База заметок и модель пользователя должны переживать обновление/переустановку.
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
