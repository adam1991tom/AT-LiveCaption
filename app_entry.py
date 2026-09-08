"""Single-EXE dual-mode entry point (PyInstaller target).

No args: shows the tray, which launches this same exe again with --server
to run the caption server as a supervised child process, and with
--window=<page> to open a native app window for a page instead of a
browser tab.
--server: runs only the caption server (no tray, no console window).
--window=<control|audience|overlay>: shows that one page as a native
WebView2 window, in its own process.
"""
import sys


def _window_arg() -> str | None:
    for arg in sys.argv:
        if arg.startswith("--window="):
            return arg.split("=", 1)[1]
    return None


def _redirect_streams_when_windowed() -> None:
    """A windowed (console=False) PyInstaller build has sys.stdout/stderr as
    None. Anything that prints (uvicorn's own logging included) would crash
    with AttributeError the moment it tried. Send it to a log file instead so
    a failure that happens before the tray icon exists is still diagnosable.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    from app.core.config import DATA_DIR

    if "--server" in sys.argv:
        suffix = "server"
    elif _window_arg():
        suffix = "window"
    else:
        suffix = "tray"
    log_path = DATA_DIR / f"{suffix}.log"
    log_file = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file


_redirect_streams_when_windowed()


def main() -> None:
    window_page = _window_arg()
    if "--server" in sys.argv:
        from app.server_main import main as server_main
        server_main()
    elif window_page:
        from app.window import show as show_window
        show_window(window_page)
    else:
        from app.tray import main as tray_main
        tray_main()


if __name__ == "__main__":
    main()
