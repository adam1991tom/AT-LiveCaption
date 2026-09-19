"""Windows recording-device enumeration. No audio is ever written to disk here."""
from __future__ import annotations

import re
from typing import Any

import sounddevice as sd

# WDM-KS in particular tends to expose the same physical mic under its own
# raw driver-pin name (e.g. "Microphone (Realtek HD Audio Mic input)")
# rather than the friendly endpoint name other APIs use ("Microphone
# (Realtek(R) Audio)") -- an exact-string dedup misses that. Stripping these
# generic words leaves just the identifying part (brand/product), which is
# what actually tells two *different* devices apart.
_NORMALIZE_STOPWORDS = re.compile(r"\b(hd|audio|input|output|mic|microphone)\b")


def _normalize_name(name: str) -> str:
    n = name.lower()
    n = n.replace("(r)", "").replace("(tm)", "")
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = _NORMALIZE_STOPWORDS.sub(" ", n)
    return " ".join(n.split())


# Some Bluetooth hands-free profiles report an unresolved Windows MUI string
# reference instead of a friendly name, e.g. literally
# "Headset (@System32\drivers\bthhfenum.sys,#2;%1 Hands-Free%0;(Pixel Buds A-Series))"
# -- the driver path was never meant to reach a user, so pull out the
# trailing "(...)" that actually names the device instead of showing that.
_UNRESOLVED_MUI_RE = re.compile(r"\(([^()]+)\)\)?\s*$")


def _clean_display_name(name: str) -> str:
    if "@System32" not in name and "@system32" not in name.lower():
        return name
    match = _UNRESOLVED_MUI_RE.search(name)
    if not match:
        return name
    prefix = name.split("(", 1)[0].strip()
    return f"{prefix} ({match.group(1)})" if prefix else match.group(1)

# PortAudio enumerates a Windows box once *per audio API* (MME, DirectSound,
# WASAPI, WDM-KS), so a single physical microphone shows up 3-4 times under
# near-identical names -- this is what made the picker look like it had way
# more devices than the machine actually has. Lower number = preferred when
# the same name shows up under more than one API.
_HOST_API_PRIORITY = ["Windows WASAPI", "Windows DirectSound", "MME", "Windows WDM-KS"]

# These are PortAudio's own "route to whatever the current default is"
# aliases, not physical devices -- the real default device already gets
# flagged via is_default below, so listing these too is just noise/confusion.
_EXCLUDED_NAMES = {"Microsoft Sound Mapper - Input", "Primary Sound Capture Driver"}


def _host_api_rank(name: str) -> int:
    try:
        return _HOST_API_PRIORITY.index(name)
    except ValueError:
        return len(_HOST_API_PRIORITY)


def _better(candidate: dict, existing: dict) -> bool:
    if candidate["is_default"] != existing["is_default"]:
        return candidate["is_default"]
    return _host_api_rank(candidate["_hostapi_name"]) < _host_api_rank(existing["_hostapi_name"])


def list_input_devices() -> list[dict[str, Any]]:
    try:
        default_input = sd.default.device[0]
    except Exception:
        default_input = -1

    hostapis = sd.query_hostapis()
    best_by_name: dict[str, dict] = {}
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) <= 0:
            continue
        name = info["name"]
        if name in _EXCLUDED_NAMES:
            continue
        name = _clean_display_name(name)
        candidate = {
            "index": index,
            "name": name,
            "default_samplerate": info.get("default_samplerate", 16000),
            "is_default": index == default_input,
            "_hostapi_name": hostapis[info["hostapi"]]["name"],
        }
        key = _normalize_name(name)
        existing = best_by_name.get(key)
        # The same normalized key appearing twice is (almost always) the
        # same physical mic exposed under a different Windows audio API,
        # not two different mics -- collapse to the one worth keeping.
        if existing is None or _better(candidate, existing):
            best_by_name[key] = candidate

    devices = sorted(best_by_name.values(), key=lambda d: d["index"])
    for d in devices:
        d.pop("_hostapi_name")
    return devices


def device_exists(index: int) -> bool:
    try:
        info = sd.query_devices(index)
        return info.get("max_input_channels", 0) > 0
    except Exception:
        return False


def rescan_devices() -> None:
    """PortAudio builds its device table once at init and does not notice a
    USB mic/interface being plugged or unplugged afterwards -- the
    documented fix is to tear down and reinitialize it. Unsafe to call while
    any sd.InputStream in this process is open; callers must stop capture
    first (see CaptionEngine.refresh_devices)."""
    sd._terminate()
    sd._initialize()
