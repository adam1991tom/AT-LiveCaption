"""Update checks -- the app is offline-by-default (see the project brief);
this is the only place that reaches the network. The model-update check
only ever runs when the operator explicitly clicks "Check for Updates" in
the control panel. The app-update check (below) is the one exception: it
runs automatically at every launch, and can silently install a newer
version on its own -- see try_auto_update().

The app's own repo is public specifically so this can work without any
credential baked into the distributed binary -- shipping a token that way
would mean every fleet install carries something that grants read access
to the repo for as long as it's valid, which is a real leak risk with no
real fix (a background app on someone else's machine can't be handed a
token that safely expires and gets refreshed the way a logged-in browser
session can). The tradeoff -- source and release binaries visible to
anyone -- was a deliberate choice given there's nothing secret in either.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from app import version
from app.core.model_download import MODEL_NAME
from app.core.procutil import CREATIONFLAGS

GITHUB_API_TIMEOUT = 8
MODEL_RELEASE_API = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models"
MODEL_RELEASES_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"
APP_RELEASES_URL = "https://github.com/adam1991tom/AT-LiveCaption/releases"
APP_LATEST_RELEASE_API = "https://api.github.com/repos/adam1991tom/AT-LiveCaption/releases/latest"
# Generous but bounded -- the installer is tens of MB; this must never hang
# a launch indefinitely on a slow/dead connection.
APP_DOWNLOAD_TIMEOUT = 90

_DATE_RE = re.compile(r"streaming-zipformer-en-(\d{4}-\d{2}-\d{2})\.tar\.bz2$")
_CURRENT_MODEL_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


def check_model_update() -> dict:
    req = urllib.request.Request(
        MODEL_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "AT-LiveCaption"},
    )
    with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    dates = sorted({m.group(1) for a in data.get("assets", []) if (m := _DATE_RE.search(a.get("name", "")))})
    current_match = _CURRENT_MODEL_DATE_RE.search(MODEL_NAME)
    current = current_match.group(1) if current_match else None

    return {
        "current_model": MODEL_NAME,
        # k2-fsa publishes several streaming English zipformer snapshots
        # under this one tag side by side, not as a chronological upgrade
        # sequence -- a later date in a filename does NOT mean "better"
        # (this app's own current model was deliberately picked over one
        # with a *later* date for being the larger/more accurate one). So
        # this never claims an "update is available", only lists what
        # exists; the operator can compare and decide for themselves.
        "other_dates_available": [d for d in dates if d != current],
        "releases_url": MODEL_RELEASES_URL,
    }


def check_for_updates() -> dict:
    result: dict = {
        "app_version": version.APP_VERSION,
        "app_releases_url": APP_RELEASES_URL,
    }
    try:
        result["model"] = check_model_update()
    except Exception as exc:
        result["model_error"] = str(exc)
    return result


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.strip().lstrip("v").split("."))


def check_app_update() -> dict | None:
    """None if no update is available or the check failed for any reason
    (no internet, GitHub hiccup, rate limit) -- a failed check must never
    be able to stop the app from starting, so nothing here ever raises."""
    try:
        req = urllib.request.Request(
            APP_LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "AT-LiveCaption"},
        )
        with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        latest = data.get("tag_name", "")
        if not latest or _version_tuple(latest) <= _version_tuple(version.APP_VERSION):
            return None

        asset = next((a for a in data.get("assets", []) if a.get("name", "").endswith(".exe")), None)
        if not asset:
            return None

        return {
            "version": latest.lstrip("v"),
            "download_url": asset["browser_download_url"],
            "filename": asset["name"],
        }
    except Exception:
        return None


def _download_installer(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AT-LiveCaption"})
        with urllib.request.urlopen(req, timeout=APP_DOWNLOAD_TIMEOUT) as resp:
            with dest.open("wb") as f:
                shutil.copyfileobj(resp, f)
        # A genuine installer is tens of MB -- anything tiny means a
        # truncated download or an error page served as if it were the
        # file, not something safe to execute.
        return dest.is_file() and dest.stat().st_size > 5_000_000
    except Exception:
        return False


def try_auto_update() -> bool:
    """Best-effort silent self-update, meant to be called once at tray
    startup before the server starts. True means a newer version's
    installer was just launched and is taking over from here (the caller
    should exit immediately, releasing this exe so the installer can
    overwrite it -- the installer relaunches the app itself once done).
    False means either no update was available or something went wrong at
    any step -- the caller should just continue starting the current
    version normally. Never raises, and never partially applies anything:
    every step either fully succeeds or the whole attempt is abandoned.
    """
    if not getattr(sys, "frozen", False):
        return False  # dev run -- only the packaged app self-updates

    info = check_app_update()
    if info is None:
        return False

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="atlivecaption_update_"))
        installer_path = tmp_dir / info["filename"]
        if not _download_installer(info["download_url"], installer_path):
            return False

        # Fire-and-forget, then this process exits immediately (see
        # tray.py) -- the installer needs this exe's file lock released
        # to overwrite it, and /AUTORELAUNCH (see the .iss) is what tells
        # this one specific silent run to launch the app afterward, unlike
        # a normal silent fleet deployment which deliberately never
        # auto-launches on its own.
        subprocess.Popen(
            [str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/AUTORELAUNCH"],
            creationflags=CREATIONFLAGS,
        )
        return True
    except Exception:
        return False
