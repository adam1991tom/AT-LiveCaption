"""On-demand update check -- the app is offline-by-default (see the project
brief); this is the only place that reaches the network, and only when the
operator explicitly clicks "Check for Updates" in the control panel. Never
runs automatically, and never downloads or installs anything by itself.

The app's own repo is private, so there is no credential bundled here that
could check it unattended -- that would mean shipping a GitHub token inside
a distributed binary. Instead the app just links to the releases page for
the operator's own already-authenticated browser to open. The ASR model
release (k2-fsa/sherpa-onnx) is public, so that one really is checked here.
"""
from __future__ import annotations

import json
import re
import urllib.request

from app import version
from app.core.model_download import MODEL_NAME

GITHUB_API_TIMEOUT = 8
MODEL_RELEASE_API = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models"
MODEL_RELEASES_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"
APP_RELEASES_URL = "https://github.com/adam1991tom/AT-LiveCaption/releases"

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
