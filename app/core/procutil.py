"""Shared process/network bits used by the tray, native windows, server
entry point, and resource monitor -- kept in one place so the port env var
name and the no-console-window flag can't drift between call sites."""
from __future__ import annotations

import os
import subprocess

CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
DEFAULT_PORT = 8765


def get_port() -> int:
    return int(os.environ.get("AT_LIVECAPTION_PORT", DEFAULT_PORT))


def get_base_url() -> str:
    return f"http://127.0.0.1:{get_port()}"
