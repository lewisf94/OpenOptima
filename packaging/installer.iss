; Inno Setup script for the OpenOptima Windows installer.
; Build dist\OpenOptima first with packaging\build_windows.ps1, then:
;   iscc packaging\installer.iss

#define AppName "OpenOptima"
#define AppVersion "0.1.0"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OpenOptima contributors
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=OpenOptima-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
; Per-user install by default: no administrator prompt, and the app only ever
; writes to the user's own Documents folder anyway.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
WizardStyle=modern

[Files]
Source: "..\dist\OpenOptima\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\docs\plain-english-guide.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\OpenOptima.exe"
Name: "{group}\Plain-English guide"; Filename: "{app}\docs\plain-english-guide.md"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\OpenOptima.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\OpenOptima.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
