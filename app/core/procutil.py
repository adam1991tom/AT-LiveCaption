"""Shared process/network bits used by the tray, native windows, server
entry point, and resource monitor -- kept in one place so the port env var
name and the no-console-window flag can't drift between call sites."""
from __future__ import annotations

import os
import socket
import subprocess

CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
DEFAULT_PORT = 8765

_instance_lock: socket.socket | None = None


def get_port() -> int:
    return int(os.environ.get("AT_LIVECAPTION_PORT", DEFAULT_PORT))


def get_base_url() -> str:
    return f"http://127.0.0.1:{get_port()}"


def _port_available(port: int) -> bool:
    # Must probe 0.0.0.0, not 127.0.0.1 -- the real server binds "0.0.0.0"
    # (see server_main.py) so it's reachable from other devices on the LAN
    # for the audience/overlay pages. Windows treats those two addresses as
    # separate bindable resources: probing 127.0.0.1 alone can report a port
    # "available" even while something else already holds 0.0.0.0 on it,
    # which is exactly the case this function exists to catch.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def resolve_server_port() -> int:
    """The configured port can already be permanently held by some other,
    completely unrelated app on this machine (seen in the wild: another of
    the operator's own tools, auto-starting at login, holding the same
    default port every time). Previously that meant our own server just
    failed to bind while every native window still loaded whatever *that*
    app happened to serve on the port instead, displaying its response as
    if it were ours -- e.g. a plain "Missing or invalid token" from a
    totally unrelated app, easy to mistake for a WebView2/auth problem.
    Probe upward from the configured port for the first one actually free.
    Call this once, in the tray process, after its own instance lock is
    already acquired (that lock stays tied to the fixed configured port,
    so "only one AT LiveCaption running" stays correct even when a launch
    ends up on a different actual port) and before spawning anything --
    every child (the server, every native window) inherits the resolved
    port from the environment.
    """
    start = get_port()
    for candidate in range(start, start + 50):
        if _port_available(candidate):
            return candidate
    return start  # give up gracefully -- let it fail the same way as before


def acquire_tray_instance_lock() -> bool:
    """A second tray launch (double-clicked again, or racing itself right
    at process start) would otherwise start a whole second tray+server
    fighting over the same port -- this is what "it opens twice" actually
    was. Holding a bound socket for the process's lifetime is a simple,
    dependency-free mutex. Deliberately kept import-light (stdlib only) so
    callers can check this before pulling in numpy/sherpa-onnx-adjacent
    modules -- it can't close the PyInstaller onefile bootloader's own
    extraction race (that happens in native code before any Python here
    runs), but it does close every window after that point, which is most
    of the real-world "double click twice" case.
    """
    global _instance_lock
    if _instance_lock is not None:
        return True  # this process already holds it (e.g. app_entry.py checked first)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", get_port() + 10000))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _instance_lock = sock  # kept alive for the process's lifetime
    return True


_WINDOW_LOCK_OFFSETS = {"control": 10001, "audience": 10002, "overlay": 10003}


def acquire_window_instance_lock(page: str) -> socket.socket | None:
    """Each --window=<page> launch is its own process (see app/window.py), so
    N launches racing each other (or just the tray's own window plus a stray
    extra double-click) would otherwise each open their own native window for
    the same page instead of sharing one. Same bind-a-port trick as the tray
    lock, on a different port per page. Caller keeps the returned socket alive
    for as long as its window is open; on None it should bring the existing
    window forward instead of creating a duplicate.
    """
    offset = _WINDOW_LOCK_OFFSETS.get(page, _WINDOW_LOCK_OFFSETS["control"])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", get_port() + offset))
        sock.listen(1)
    except OSError:
        sock.close()
        return None
    return sock
