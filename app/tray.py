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

PORT = int(os.environ.get("AT_LIVECAPTION_PORT", 8765))
BASE_URL = f"http://127.0.0.1:{PORT}"
POLL_SECONDS = 2.0
CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _window_argv(page: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--window={page}"]
    entry = Path(__file__).resolve().parent.parent / "app_entry.py"
    return [sys.executable, str(entry), f"--window={page}"]


def open_window(page: str) -> None:
    subprocess.Popen(_window_argv(page), creationflags=CREATIONFLAGS)


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


def main() -> None:
    supervisor = ServerSupervisor()
    supervisor.start()

    def open_control(icon=None, item=None):
        open_window("control")

    def open_audience(icon=None, item=None):
        open_window("audience")

    def open_overlay(icon=None, item=None):
        open_window("overlay")

    def restart_server(icon=None, item=None):
        supervisor.restart()

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
