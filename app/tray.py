"""System tray supervisor: starts the caption server as a child process, restarts
it if it dies unexpectedly, and gives the operator quick links + control.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from app.core import autostart, update_check
from app.core.procutil import (
    CREATIONFLAGS,
    acquire_tray_instance_lock,
    assign_to_job,
    create_kill_on_close_job,
    resolve_server_port,
)

POLL_SECONDS = 2.0

# Set once in main(), before anything is spawned -- see create_kill_on_close_job()
# for why every child process (the server, every native window, however it
# was opened) needs to land in this same job.
_job: int | None = None


def _window_argv(page: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--window={page}"]
    entry = Path(__file__).resolve().parent.parent / "app_entry.py"
    return [sys.executable, str(entry), f"--window={page}"]


def open_window(page: str) -> None:
    proc = subprocess.Popen(_window_argv(page), creationflags=CREATIONFLAGS)
    assign_to_job(_job, proc)


def _make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill="#1f2229")
    draw.ellipse((18, 18, 46, 46), fill=color)
    return img


ICON_STARTING = _make_icon("#f5a623")
ICON_RUNNING = _make_icon("#35d07f")
ICON_STOPPED = _make_icon("#e5484d")


class ServerSupervisor:
    """Owns the child server process. wanted_running tracks operator intent
    so a deliberate Exit/Restart doesn't get treated as a crash to recover from."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.wanted_running = True
        self._lock = threading.Lock()

    def _server_argv(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--server"]
        return [sys.executable, "-m", "app.server_main"]

    def start(self) -> None:
        with self._lock:
            self.wanted_running = True
            if self.process and self.process.poll() is None:
                return
            self.process = subprocess.Popen(self._server_argv(), creationflags=CREATIONFLAGS)
            assign_to_job(_job, self.process)

    def stop(self) -> None:
        with self._lock:
            self.wanted_running = False
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def is_running(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def supervise_forever(self, on_change) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            with self._lock:
                crashed = self.wanted_running and self.process is not None and self.process.poll() is not None
            if crashed:
                self.start()
            on_change()


def open_control(icon=None, item=None):
    open_window("control")


def main() -> None:
    if not acquire_tray_instance_lock():
        # Already running somewhere -- just give the operator a window onto
        # it instead of starting a second tray+server that would only fight
        # the first one over the port.
        open_control()
        return

    # Checked before anything else starts, every launch. A newer version's
    # installer is now launching itself in the background (it needs this
    # exe's file lock released to overwrite it, and will relaunch the app
    # itself once done) -- this process's only job from here is to get out
    # of the way. Any failure along the way (no internet, GitHub hiccup) is
    # already swallowed inside try_auto_update() -- it only ever returns
    # True when a real update is genuinely already underway.
    if update_check.try_auto_update():
        return

    # See create_kill_on_close_job() -- every process spawned from here on
    # (the server, every native window, however it's opened) lands in this
    # same job, so nothing can outlive this tray process as an orphan.
    global _job
    _job = create_kill_on_close_job()

    # Resolve the real working port now, after the instance lock above is
    # already ours -- see resolve_server_port() for why this has to happen
    # here rather than relying on the configured default. Every child
    # process spawned from here on (the server, every native window)
    # inherits it via the environment.
    os.environ["AT_LIVECAPTION_PORT"] = str(resolve_server_port())

    supervisor = ServerSupervisor()
    supervisor.start()

    def open_audience(icon=None, item=None):
        open_window("audience")

    def open_overlay(icon=None, item=None):
        open_window("overlay")

    def restart_server(icon=None, item=None):
        supervisor.restart()

    def toggle_startup(icon=None, item=None):
        try:
            autostart.set_enabled(not autostart.is_enabled())
        except RuntimeError:
            pass  # dev run, not the installed exe -- silently a no-op

    def exit_app(icon=None, item=None):
        supervisor.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("AT LiveCaption", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Control Page", open_control),
        pystray.MenuItem("Open Audience Screen", open_audience),
        pystray.MenuItem("Open Camera Overlay", open_overlay),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart Caption Server", restart_server),
        pystray.MenuItem("Start with Windows", toggle_startup, checked=lambda item: autostart.is_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit AT LiveCaption", exit_app),
    )

    icon = pystray.Icon("AT LiveCaption", ICON_STARTING, "AT LiveCaption - starting...", menu)

    def refresh_icon_state():
        if supervisor.is_running():
            icon.icon = ICON_RUNNING
            icon.title = "AT LiveCaption - LIVE"
        else:
            icon.icon = ICON_STOPPED
            icon.title = "AT LiveCaption - stopped"

    threading.Thread(target=supervisor.supervise_forever, args=(refresh_icon_state,), daemon=True).start()

    def setup(ic: pystray.Icon) -> None:
        ic.visible = True
        threading.Timer(1.0, open_control).start()

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
