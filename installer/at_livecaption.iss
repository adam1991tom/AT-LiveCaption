#define MyAppName "AT LiveCaption"
#define MyAppVersion "2.2.2"
#define MyAppPublisher "AT LiveCaption"
#define MyAppExeName "ATLiveCaption.exe"
#define MyAppPort "8765"

[Setup]
AppId={{B8F1E2A0-6C3D-4E7B-9F2A-3D8C4E5F6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AT LiveCaption
DefaultGroupName=AT LiveCaption
DisableProgramGroupPage=yes
OutputDir=..\FINAL-INSTALLER
OutputBaseFilename=AT-LiveCaption-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; Deployment tools (Action1, Intune, etc.) normally push this already
; elevated as SYSTEM, so this never actually prompts for UAC consent in
; that context -- SYSTEM's token isn't subject to UAC filtering. Left as
; "commandline dialog" (the default) rather than restricted, purely so a
; fleet admin retains the option of an /ALLUSERS or /CURRENTUSER override
; if their specific deployment context ever needs it.
PrivilegesRequiredOverridesAllowed=commandline dialog
; Always writes a diagnostic log to %TEMP%\Setup Log <app> <date>.txt, silent
; or not -- with no interactive session to watch (a fleet deployment tool),
; this is the only way to see what actually happened on a given machine.
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
; Installer/uninstaller chrome is baked in at compile time, unlike the app's
; own runtime-switchable theme -- this fixes it to the default (blue) theme.
SetupIconFile=..\branding\ico\at_livecaption_blue.ico
WizardImageFile=wizard\wizard_large_blue.bmp
WizardSmallImageFile=wizard\wizard_small_blue.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "startupicon"; Description: "Start AT LiveCaption automatically when Windows starts"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
; onedir build: dist\ATLiveCaption\ is a whole folder (the exe plus its
; supporting DLLs/pyc files), not a single file -- see at_livecaption.spec
; for why onefile was dropped.
Source: "..\dist\ATLiveCaption\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AT LiveCaption"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Open Control Page"; Filename: "http://127.0.0.1:{#MyAppPort}/"
Name: "{group}\Open Audience Screen"; Filename: "http://127.0.0.1:{#MyAppPort}/audience"
Name: "{group}\Open Camera Overlay"; Filename: "http://127.0.0.1:{#MyAppPort}/overlay"
Name: "{group}\Uninstall AT LiveCaption"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AT LiveCaption"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AT LiveCaption"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=""AT LiveCaption"" dir=in action=allow protocol=TCP localport={#MyAppPort} profile=private,domain"; Flags: runhidden; StatusMsg: "Configuring Windows Firewall..."
; Only ever runs when the operator leaves "Launch AT LiveCaption" checked on
; the finish page -- there is no separate dependency/runtime install step
; here to accidentally launch the app regardless of that checkbox.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,AT LiveCaption}"; Flags: nowait postinstall skipifsilent; Check: NotSilent

[UninstallRun]
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""AT LiveCaption"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"

; Deliberately NOT deleting {localappdata}\ATLiveCaption on uninstall --
; that folder holds the operator's config and any saved transcripts, which
; an uninstall should never silently destroy.

[Code]
// Belt-and-suspenders alongside the skipifsilent flag above: WizardSilent()
// is evaluated at runtime by whatever process actually executes the [Run]
// entries, so unlike the flag it can't be defeated by command-line switches
// getting lost across a UAC elevation re-exec.
function NotSilent(): Boolean;
begin
  Result := not WizardSilent();
end;
