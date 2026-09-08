# AT LiveCaption (v0.5.1)

Local, offline, real-time conference captioning. Audio in -> Sherpa-ONNX
streaming Zipformer -> live captions on the control page, audience screen,
and a transparent camera overlay, all over one WebSocket. No cloud calls,
no API keys, no audio ever written to disk.

## Install (recommended)

Run `FINAL-INSTALLER\AT-LiveCaption-Setup-v0.5.1.exe`. It installs a single
self-contained EXE (Python/ASR runtime all bundled, no separate Python
install needed), adds a Windows Firewall rule for TCP 8765, and creates
Start Menu shortcuts for the control page, audience screen and camera
overlay. Leaving "Launch AT LiveCaption" checked on the finish page starts
it; unchecking it (or a silent/unattended install) does not.

AT LiveCaption lives in the Windows tray after that. Config, the
downloaded model, and any saved transcripts live in
`%LOCALAPPDATA%\ATLiveCaption\`.

Note: AT LiveCaption defaults to port 8765, same as AT-LiveOverlay's
Companion API. Don't run both on the same machine without changing one of
their ports.

## Run from source (dev)

```
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

First run downloads the fixed English streaming model once (~internet
required that one time); every run after that is fully offline. You can
also pre-fetch it explicitly:

```
python scripts\download_model.py
```

Then open:
- Control: http://127.0.0.1:8765/
- Audience screen: http://127.0.0.1:8765/audience
- Camera overlay (transparent, for OBS/vMix Browser Source): http://127.0.0.1:8765/overlay

Pick your input device on the control page - it's remembered in `config.json`.

Set `AT_LIVECAPTION_PORT` to run on a different port (e.g. for testing
alongside another app already using 8765).

## Rebuilding the EXE / installer

```
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean at_livecaption.spec
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\at_livecaption.iss
```

Output: `dist\ATLiveCaption.exe` (single EXE, tray by default, `--server`
for server-only) and `FINAL-INSTALLER\AT-LiveCaption-Setup-v0.5.1.exe`.

If you ever need to test the installer's silent-install flags yourself
(`/VERYSILENT` etc.), invoke it via PowerShell's
`Start-Process -Verb RunAs -ArgumentList ...` rather than a plain shell
call — a plain elevation can silently drop the command-line arguments
before Setup ever sees them, which looks like a silent-install bug but
isn't one.

## What's not built yet

Built: capture -> ASR -> WebSocket -> three pages, tray app with crash
supervision, single-EXE packaging, Inno Setup installer with firewall rule
and correct launch-checkbox behavior. Not yet built: session/event
management, custom vocabulary, confidence-based commit tuning, Companion
integration, OnTime integration. See the full v0.5.1 project brief for the
target scope.
