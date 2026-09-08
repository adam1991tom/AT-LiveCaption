"""Native app windows (WebView2) for the tray's page shortcuts.

Each call to this module is its own OS process (spawned by the tray with
--window=<page>), so there's no GUI-loop contention with pystray's tray
icon or the caption server -- a window crashing can't take either down.
"""
from __future__ import annotations

import os

import webview

PORT = int(os.environ.get("AT_LIVECAPTION_PORT", 8765))
BASE_URL = f"http://127.0.0.1:{PORT}"

PAGES = {
    "control": ("AT LiveCaption - Control", "/", 1100, 850),
    "audience": ("AT LiveCaption - Audience Preview", "/audience", 960, 540),
    "overlay": ("AT LiveCaption - Overlay Preview", "/overlay", 960, 540),
}


def show(page: str) -> None:
    title, path, width, height = PAGES.get(page, PAGES["control"])
    webview.create_window(title, BASE_URL + path, width=width, height=height)
    webview.start()
