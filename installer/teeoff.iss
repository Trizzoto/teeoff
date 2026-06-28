; TeeOff — Grandpa Golf auto-booker installer (Inno Setup).
;
; Per-user install (no admin prompt) to a FIXED location, so the scheduled task's
; baked interpreter path can never go stale by the app being moved/re-unzipped —
; the root cause of the original "flash and die" failure.
;
; Build via build_installer.py, which passes the project root in SrcRoot:
;   ISCC.exe /DSrcRoot="<project root>" installer\teeoff.iss
;
; User DATA (settings.json, logs, markers, last_run.json) lives separately in
; %LOCALAPPDATA%\TeeOff (see app/paths.py) and is intentionally NOT installed or
; removed here, so updates/uninstalls preserve grandpa's configuration.

#ifndef SrcRoot
  #define SrcRoot "."
#endif

#define MyAppName "TeeOff"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Grandpa Golf"

[Setup]
AppId={{B8E7B0A2-1C3D-4E5F-9A6B-7C8D9E0F1A2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Fixed per-user install dir — the key to never going stale.
DefaultDirName={localappdata}\Programs\TeeOff
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
SourceDir={#SrcRoot}
OutputDir=dist
OutputBaseFilename=TeeOff-Setup
SetupIconFile=app\assets\icon.ico
UninstallDisplayIcon={app}\app\assets\icon.ico
UninstallDisplayName=TeeOff Golf Booker
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The entire embeddable-python bundle produced by build_bundle.py.
Source: "dist\TeeOff\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TeeOff"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.gui"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"
Name: "{autodesktop}\TeeOff"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.gui"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; Tasks: desktopicon

[Run]
; Register the Windows scheduled task pointing at this fixed install dir.
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.scheduler register"; WorkingDir: "{app}"; StatusMsg: "Setting up the weekly booking schedule..."; Flags: runhidden waituntilterminated
; Offer to open the app at the end (also triggers the self-heal path on launch).
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app.gui"; WorkingDir: "{app}"; Description: "Open TeeOff now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Python writes __pycache__/*.pyc next to the code at runtime, and these aren't
; tracked by the installer — so force-remove the whole install dir on uninstall.
; (User data in %LOCALAPPDATA%\TeeOff is separate and deliberately preserved.)
Type: filesandordirs; Name: "{app}"

[UninstallRun]
; Remove the scheduled task BEFORE the files are deleted, so no orphan task is left
; pointing at a removed interpreter. Use PowerShell directly (NOT the installed
; python) so the app\ and booker\ folders aren't locked by a running interpreter
; and can be fully deleted.
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -WindowStyle Hidden -Command ""Unregister-ScheduledTask -TaskName 'GrandpaGolfAutoBooker' -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterTask"
