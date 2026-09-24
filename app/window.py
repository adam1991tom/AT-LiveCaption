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


def _disable_webview2_sso() -> None:
    """This app never asks for any Microsoft/Windows-account sign-in, but
    WebView2 can still probe the OS account broker (WAM) for single sign-on
    on its own. Seen in the wild: a broken WAM logon session on one
    machine (dsregcmd showed WamDefaultSet: ERROR 0x80070520, unrelated to
    any Azure AD/domain join -- this laptop was joined to neither) made
    that probe fail with WebView2's own "missing or invalid token" error
    the instant the window opened, before any of this app's own code ran.
    A stale Azure AD Primary Refresh Token on a domain-joined machine can
    trip the same failure. pywebview doesn't expose a setting for this, so
    this patches its EdgeChrome class to append the one Chromium flag that
    turns the probe off entirely -- safe regardless of cause, since this
    app never wanted that integration in the first place.
    """
    try:
        from webview.platforms import edgechromium
    except ImportError:
        return  # not on the edgechromium backend -- nothing to patch

    original_init = edgechromium.EdgeChrome.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.webview.CreationProperties.AdditionalBrowserArguments += (
            " --disable-features=msSingleSignOn"
        )

    edgechromium.EdgeChrome.__init__ = patched_init


_disable_webview2_sso()

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
