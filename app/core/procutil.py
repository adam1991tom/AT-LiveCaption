"""Shared process/network bits used by the tray, native windows, server
entry point, and resource monitor -- kept in one place so the port env var
name and the no-console-window flag can't drift between call sites."""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
from ctypes import wintypes

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


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def create_kill_on_close_job() -> int | None:
    """Every process this app spawns -- the caption server, and every native
    Control/Audience/Overlay window, whether opened directly by the tray or
    (via /api/open-window) by the server process on the tray's behalf -- used
    to be a plain, untracked fire-and-forget child. Closing the tray only
    ever stopped its own direct server child; any window left open (or a
    server child if the tray itself was killed some other way, e.g. Task
    Manager) was orphaned, still running, still holding the exe's file lock.
    That's what made reinstalling or even just fully quitting the app show
    Windows' "file in use, Retry?" indefinitely -- there was always
    something still alive to hold the lock.
    A Windows Job Object with KILL_ON_JOB_CLOSE fixes this at the OS level
    instead of trying to track every process by hand: put the tray's own
    direct children in the job, and every process THEY spawn automatically
    joins the same job too (Windows' default behavior for child processes).
    The job's only handle lives in the tray process, so the instant that
    process ends -- cleanly via Exit, or forcibly via Task Manager/taskkill,
    or a crash -- Windows closes the handle and kills every process in the
    job together, no orphans possible. Returns None if anything about this
    fails (older/locked-down Windows); callers should treat that as "no
    extra safety net" and keep working exactly as before.
    """
    job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = ctypes.windll.kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        ctypes.windll.kernel32.CloseHandle(job)
        return None
    return job


def assign_to_job(job: int | None, process: subprocess.Popen) -> None:
    """Best-effort: a failure here just means that one process falls back to
    the old untracked behavior, not a reason to disrupt the caller."""
    if not job:
        return
    try:
        ctypes.windll.kernel32.AssignProcessToJobObject(job, int(process._handle))
    except (AttributeError, OSError):
        pass
