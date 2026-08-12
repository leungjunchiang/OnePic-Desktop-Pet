#ifndef MyAppVersion
  #define MyAppVersion "0.15.0"
#endif

[Setup]
AppId={{D8A7EA20-082D-49A4-8E10-08DAB7C6894E}
AppName=Lili
AppVersion={#MyAppVersion}
AppPublisher=OnePic Desktop Pet
AppPublisherURL=https://github.com/leungjunchiang/OnePic-Desktop-Pet
DefaultDirName={localappdata}\Programs\Lili
DefaultGroupName=Lili
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=Lili-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Lili.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建 Lili 快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "..\..\dist\Lili\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lili"; Filename: "{app}\Lili.exe"
Name: "{autodesktop}\Lili"; Filename: "{app}\Lili.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lili.exe"; Description: "启动 Lili"; Flags: nowait postinstall skipifsilent
