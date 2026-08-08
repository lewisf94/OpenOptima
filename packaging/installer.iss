; Inno Setup script for the OpenOptima Windows installer.
; Build dist\OpenOptima first with packaging\build_windows.ps1, then:
;   iscc packaging\installer.iss

#define AppName "OpenOptima"
#define AppVersion "0.1.0"

[Setup]
; A fixed AppId is what makes the next version replace this one instead of
; installing alongside it. Never change it; changing it strands every existing
; installation with no way to upgrade or uninstall cleanly.
AppId={{8E4C9A21-3F6D-4B58-9C7E-2A1D5F0B7E43}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OpenOptima contributors
AppPublisherURL=https://github.com/lewisf94/OpenOptima
AppSupportURL=https://github.com/lewisf94/OpenOptima/issues
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
; The icon on the installer itself, and the one shown in Add or remove
; programs. Without these Windows falls back to a generic box.
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\OpenOptima.exe
UninstallDisplayName={#AppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Files]
Source: "..\dist\OpenOptima\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\docs\plain-english-guide.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; The Start menu entry. This is what Windows search finds, and what the user
; right-clicks to pin to the taskbar.
Name: "{group}\{#AppName}"; Filename: "{app}\OpenOptima.exe"; \
    Comment: "Find the best size for a part"
Name: "{group}\Plain-English guide"; Filename: "{app}\docs\plain-english-guide.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\OpenOptima.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\OpenOptima.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
