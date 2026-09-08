"""Windows recording-device enumeration. No audio is ever written to disk here."""
from __future__ import annotations

from typing import Any

import sounddevice as sd


def list_input_devices() -> list[dict[str, Any]]:
    devices = []
    try:
        default_input = sd.default.device[0]
    except Exception:
        default_input = -1

    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            devices.append(
                {
                    "index": index,
                    "name": info["name"],
                    "default_samplerate": info.get("default_samplerate", 16000),
                    "is_default": index == default_input,
                }
            )
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
