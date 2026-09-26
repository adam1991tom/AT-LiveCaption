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
from app.core.model_download import MODEL_NAME, download_and_extract, model_is_ready
from app.core.procutil import CREATIONFLAGS

GITHUB_API_TIMEOUT = 8
MODEL_RELEASE_API = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models"
MODEL_RELEASES_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"
APP_RELEASES_URL = "https://github.com/adam1991tom/AT-LiveCaption/releases"
APP_LATEST_RELEASE_API = "https://api.github.com/repos/adam1991tom/AT-LiveCaption/releases/latest"
# Generous but bounded -- the installer is tens of MB; this must never hang
# a launch indefinitely on a slow/dead connection.
APP_DOWNLOAD_TIMEOUT = 90

# Anchored at the START too, not just the end -- k2-fsa also publishes
# hardware-specific NPU builds under this same release tag with names like
# "sherpa-onnx-rk3562-streaming-zipformer-en-2023-06-26.tar.bz2", which a
# suffix-only match would also catch. Those aren't ONNX CPU models at all
# (wrong runtime entirely) and would crash the moment try_update_model()
# below tried to load one as if it were a normal candidate.
_DATE_RE = re.compile(r"^sherpa-onnx-streaming-zipformer-en-(\d{4}-\d{2}-\d{2})\.tar\.bz2$")
_CURRENT_MODEL_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


def check_model_update(current_model: str = MODEL_NAME) -> dict:
    """current_model defaults to this build's own bundled model, but callers
    that know the actual active one (it can differ once the background
    quality check in try_update_model() below has promoted a candidate)
    should pass it explicitly -- otherwise the already-active model could
    show up as its own "candidate" here."""
    req = urllib.request.Request(
        MODEL_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "AT-LiveCaption"},
    )
    with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    names = sorted({a["name"][: -len(".tar.bz2")] for a in data.get("assets", []) if _DATE_RE.search(a.get("name", ""))})
    dates = sorted({m.group(1) for a in data.get("assets", []) if (m := _DATE_RE.search(a.get("name", "")))})
    current_match = _CURRENT_MODEL_DATE_RE.search(current_model)
    current = current_match.group(1) if current_match else None

    return {
        "current_model": current_model,
        # k2-fsa publishes several streaming English zipformer snapshots
        # under this one tag side by side, not as a chronological upgrade
        # sequence -- a later date in a filename does NOT mean "better"
        # (this app's own current model was deliberately picked over one
        # with a *later* date for being the larger/more accurate one). So
        # this never claims an "update is available", only lists what
        # exists; the operator can compare and decide for themselves.
        "other_dates_available": [d for d in dates if d != current],
        # Full names, for try_update_model() below to actually download --
        # kept separate from other_dates_available so the manual "Check for
        # Updates" UI text (which only ever shows dates) doesn't change.
        "other_models_available": [n for n in names if n != current_model],
        "releases_url": MODEL_RELEASES_URL,
    }


def check_for_updates(current_model: str = MODEL_NAME) -> dict:
    result: dict = {
        "app_version": version.APP_VERSION,
        "app_releases_url": APP_RELEASES_URL,
    }
    try:
        result["model"] = check_model_update(current_model)
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


# A 3-clip sample makes small WER differences pure noise -- only a clear,
# sizeable gap should ever move the app off a model it already works with.
MODEL_PROMOTION_MARGIN = 0.05


def try_update_model() -> dict:
    """Best-effort background model-quality check, meant to be called once
    per launch from a background thread (this can take a while: a model
    download plus two full model loads and a handful of inference passes).
    Downloads a candidate model to its OWN directory -- the current model
    is never touched, deleted, or even loaded read/write, only read from --
    then benchmarks both against the current model's own bundled reference
    clips (see model_bench.py) and only swaps to the candidate if it's a
    clear, sizeable improvement, never on a marginal difference that a
    3-clip sample can't actually support. Runs the real comparison only
    once per newly-seen candidate (tracked in config.model_update_checked),
    so a rejected candidate isn't re-downloaded and re-tested every single
    future launch. Never raises.
    The caller (see tray.py) should restart the caption server whenever
    the returned dict has "promoted": True -- a running server only reads
    config.model_dir at its own startup, so nothing else makes it pick up
    a changed model.
    """
    from app.core import config as config_module
    from app.core import model_bench

    result: dict = {"promoted": False}
    cfg = config_module.load_config()
    current_model_dir = Path(cfg["model_dir"])

    try:
        check = check_model_update(current_model_dir.name)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    # Never treat the revert backup (or the current model, though
    # check_model_update() above already excludes that one) as a fresh
    # "candidate" -- re-testing it and finding it worse would otherwise
    # delete the exact backup "Revert to previous model" depends on.
    previous_name = Path(cfg.get("previous_model_dir") or "").name
    candidates = [c for c in (check.get("other_models_available") or []) if c != previous_name]
    if not candidates:
        return result
    candidate_name = candidates[-1]  # ISO-dated names sort correctly as plain strings

    if cfg.get("model_update_checked") == candidate_name:
        return result  # already tested this exact candidate before

    candidate_model_dir = current_model_dir.parent / candidate_name

    # Extra safety net alongside the previous_name filter above: whatever
    # happens below must never delete the active model, the revert
    # backup, or this build's own bundled default -- only a freshly
    # downloaded, otherwise-unreferenced candidate directory is ever
    # cleaned up.
    protected_dirs = {current_model_dir, Path(cfg.get("previous_model_dir") or ""), Path(config_module.MODEL_DIR_DEFAULT)}

    def _cleanup_candidate() -> None:
        if candidate_model_dir not in protected_dirs:
            shutil.rmtree(candidate_model_dir, ignore_errors=True)

    try:
        download_and_extract(candidate_model_dir, model_name=candidate_name)
    except Exception as exc:
        result["error"] = f"download failed: {exc}"
        _cleanup_candidate()
        return result

    if not model_is_ready(candidate_model_dir):
        result["error"] = "download incomplete"
        _cleanup_candidate()
        return result

    test_wavs_dir = current_model_dir / "test_wavs"
    trans_path = test_wavs_dir / "trans.txt"
    current_wer = model_bench.evaluate_model(current_model_dir, test_wavs_dir, trans_path)
    candidate_wer = model_bench.evaluate_model(candidate_model_dir, test_wavs_dir, trans_path)

    cfg = config_module.load_config()  # re-load: the download took a while, don't clobber concurrent edits
    cfg["model_update_checked"] = candidate_name

    if current_wer is not None and candidate_wer is not None and candidate_wer <= current_wer - MODEL_PROMOTION_MARGIN:
        cfg["previous_model_dir"] = cfg["model_dir"]
        cfg["model_dir"] = str(candidate_model_dir)
        config_module.save_config(cfg)
        result.update(
            promoted=True,
            previous_model=current_model_dir.name,
            new_model=candidate_name,
            previous_wer=current_wer,
            new_wer=candidate_wer,
        )
    else:
        config_module.save_config(cfg)
        _cleanup_candidate()
        result.update(tested_model=candidate_name, current_wer=current_wer, candidate_wer=candidate_wer)
    return result
