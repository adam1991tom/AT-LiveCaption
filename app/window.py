"""Native app windows (WebView2) for the tray's page shortcuts.

Each call to this module is its own OS process (spawned by the tray with
--window=<page>), so there's no GUI-loop contention with pystray's tray
icon or the caption server -- a window crashing can't take either down.
"""
from __future__ import annotations

import webview

from app.core.procutil import get_base_url

BASE_URL = get_base_url()

PAGES = {
    "control": ("AT LiveCaption - Control", "/", 1100, 850),
    "audience": ("AT LiveCaption - Audience Preview", "/audience", 960, 540),
    "overlay": ("AT LiveCaption - Overlay Preview", "/overlay", 960, 540),
}


def show(page: str) -> None:
    title, path, width, height = PAGES.get(page, PAGES["control"])
    webview.create_window(title, BASE_URL + path, width=width, height=height)
    webview.start()
