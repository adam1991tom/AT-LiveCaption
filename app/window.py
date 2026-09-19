"""Native app windows (WebView2) for the tray's page shortcuts.

Each call to this module is its own OS process (spawned by the tray with
--window=<page>), so there's no GUI-loop contention with pystray's tray
icon or the caption server -- a window crashing can't take either down.
"""
from __future__ import annotations

import ctypes

import webview

from app.core.procutil import acquire_window_instance_lock, get_base_url

BASE_URL = get_base_url()

PAGES = {
    "control": ("AT LiveCaption - Control", "/", 1100, 850),
    "audience": ("AT LiveCaption - Audience Preview", "/audience", 960, 540),
    "overlay": ("AT LiveCaption - Overlay Preview", "/overlay", 960, 540),
}

SW_RESTORE = 9


def _focus_existing(title: str) -> None:
    """Best-effort: another process already holds this page's window lock,
    so bring its actual window to the front instead of leaving the user with
    nothing after they double-clicked a shortcut for a page that's already
    open. Silently does nothing if the window can't be found (e.g. it's
    still mid-launch) -- that's still strictly better than opening a duplicate.
    """
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return
    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def show(page: str) -> None:
    title, path, width, height = PAGES.get(page, PAGES["control"])
    lock = acquire_window_instance_lock(page)
    if lock is None:
        _focus_existing(title)
        return
    webview.create_window(title, BASE_URL + path, width=width, height=height)
    webview.start()
