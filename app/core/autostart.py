"""'Start with Windows' toggle, backed by a per-user registry Run key.

Uses HKEY_CURRENT_USER (not HKLM) deliberately: the tray process runs
unelevated, and HKCU\\...\\Run needs no admin rights to read or write --
unlike the installer's own optional HKLM entry (all-users, set once at
install time), this one can be flipped at any time from the tray menu.
"""
from __future__ import annotations

import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AT LiveCaption"


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    if enabled and not getattr(sys, "frozen", False):
        raise RuntimeError("Start-with-Windows only makes sense for the installed app, not a dev run")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
