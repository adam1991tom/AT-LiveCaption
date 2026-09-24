#define MyAppName "AT LiveCaption"
#define MyAppVersion "2.5.2"
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
; Microsoft's own tiny (~2MB) Evergreen Bootstrapper -- fetches the real
; WebView2 Runtime online, only actually run below if this machine doesn't
; already have it. Deliberately not the ~150MB offline "Fixed Version"
; runtime: fleet/managed machines that can reach Action1 at all can reach
; Microsoft's CDN too, and bundling the full runtime would roughly double
; this installer's size for something most machines already have anyway.
Source: "deps\MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

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
; The app's whole UI is a WebView2 window (see app/window.py) -- without
; the runtime, that window shows a "missing or invalid token" error from
; WebView2 itself the instant it opens, before any of the app's own code
; runs. Most machines already have this (it ships with Windows 10/11 and
; Edge), which is exactly why this is skipped whenever IsWebView2Installed
; already finds it -- this only actually does anything on the machines
; that need it, e.g. an older or locked-down managed image.
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."; Check: not IsWebView2Installed(); Flags: waituntilterminated
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
// Microsoft's own documented detection method for the WebView2 Runtime:
// a non-empty, non-"0.0.0.0" pv value under this exact registry path (the
// GUID is fixed, published by Microsoft for exactly this check) means it's
// already installed -- per-machine (64-bit or 32-bit OS) or per-user.
function IsWebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

// Belt-and-suspenders alongside the skipifsilent flag above: WizardSilent()
// is evaluated at runtime by whatever process actually executes the [Run]
// entries, so unlike the flag it can't be defeated by command-line switches
// getting lost across a UAC elevation re-exec.
function NotSilent(): Boolean;
begin
  Result := not WizardSilent();
end;
