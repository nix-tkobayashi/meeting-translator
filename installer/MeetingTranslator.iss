; Inno Setup script. Build: ISCC /DAppVersion=0.1.0 installer\MeetingTranslator.iss (after PyInstaller)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8717301B-8523-417C-9CDE-EED8BD9D70AB}
AppName=Meeting Translator
AppVersion={#AppVersion}
AppPublisher=nix
DefaultDirName={autopf}\Meeting Translator
DefaultGroupName=Meeting Translator
DisableProgramGroupPage=yes
; Per-user install so users can place/replace config.json without admin rights.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=MeetingTranslator-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MeetingTranslator.exe

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MeetingTranslator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Optional: config.json placed next to the setup EXE is installed alongside the app.
Source: "{src}\config.json"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist

[Icons]
Name: "{group}\Meeting Translator"; Filename: "{app}\MeetingTranslator.exe"
Name: "{autodesktop}\Meeting Translator"; Filename: "{app}\MeetingTranslator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MeetingTranslator.exe"; Description: "{cm:LaunchProgram,Meeting Translator}"; Flags: nowait postinstall skipifsilent
; In-app updates run this installer with /SILENT; relaunch the app afterwards.
Filename: "{app}\MeetingTranslator.exe"; Flags: nowait skipifnotsilent
