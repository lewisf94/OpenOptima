"""Starting the desktop app.

Double-clicking the Windows executable lands here: start a local server, open
a window at it, and stay running until that window is closed.

**The window is not a browser tab.** Chromium-based browsers accept an
``--app=URL`` flag that opens a single window with no address bar, no tabs and
its own taskbar button. Given its own profile directory it is also a separate
process from whatever the user already has open, so closing it closes only
this application, and the server can shut down with it.

This is deliberately not pywebview or Electron. ``packaging/README.md``
explains why: every extra dependency is another way the frozen Windows build
fails on a machine nobody can debug, and Edge is present on every supported
Windows install. If neither Edge nor Chrome is found, the plain browser is
still used, so the app degrades rather than refusing to start.
"""

from __future__ import annotations

import argparse
import contextlib
import multiprocessing
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

from ..config import settings_directory
from .server import HOST, create_server, find_free_port

#: Opened at a size that shows the whole interface without scrolling.
_WINDOW_SIZE = (1180, 820)


def log_path() -> Path:
    directory = settings_directory()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "openoptima.log"


def _redirect_output_to_log() -> Path | None:
    """Give a windowed build somewhere to print. Returns the file, or None.

    The frozen application has no console, which means PyInstaller leaves
    ``sys.stdout`` and ``sys.stderr`` set to ``None`` and every print and every
    traceback goes nowhere at all. Without this, an app that fails on startup
    shows the user precisely nothing: no window, no error, no clue.

    Does nothing when a console is present, so running from a terminal still
    behaves normally.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        path = log_path()
        handle = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        # Nowhere writable. Carry on silently rather than refusing to start
        # over a log file.
        return None
    sys.stdout = handle
    sys.stderr = handle
    print(f"\n--- OpenOptima started {datetime.now():%Y-%m-%d %H:%M:%S} ---")
    return path


def _show_error(message: str) -> None:
    """Report a startup failure when there is no console to report it in."""
    if os.name != "nt":
        return
    # A message box that will not open must not mask the error it was trying to
    # report, which is already in the log by this point.
    with contextlib.suppress(Exception):  # pragma: no cover - Windows only
        import ctypes

        # 0x10 is MB_ICONERROR.
        ctypes.windll.user32.MessageBoxW(None, message, "OpenOptima", 0x10)  # type: ignore[attr-defined]


def _window_browser() -> str | None:
    """A Chromium-based browser that can open a chromeless application window.

    Edge first: it is present on every supported Windows install, so it is the
    one that will actually be there on a user's machine.
    """
    if os.name == "nt":
        # Windows environment names are case-insensitive; upper case keeps the
        # linter happy without changing what is looked up.
        program_files = (
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("LOCALAPPDATA"),
        )
        relative = (
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
        )
        for suffix in relative:
            for root in program_files:
                if not root:
                    continue
                candidate = Path(root) / suffix
                if candidate.is_file():
                    return str(candidate)
        return None

    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _profile_directory() -> Path:
    """A private browser profile, so the window is its own process.

    Without this the ``--app`` window can be adopted by an already-running
    browser, which means closing it does not tell us anything and the user is
    left with a server running invisibly.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    profile = base / "OpenOptima" / "window"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def open_window(url: str) -> subprocess.Popen[bytes] | None:
    """Open the app in its own window. Returns the process, or None if unavailable."""
    browser = _window_browser()
    if browser is None:
        return None
    width, height = _WINDOW_SIZE
    command = [
        browser,
        f"--app={url}",
        f"--user-data-dir={_profile_directory()}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        # Argument list, never a shell string: an install path contains spaces.
        return subprocess.Popen(  # noqa: S603 - fixed argument list
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def default_root() -> Path:
    """Where to look for parts.

    A frozen build sits in Program Files, which is read-only and not where
    anyone keeps their work, so use the user's Documents folder instead.
    """
    if getattr(sys, "frozen", False):
        documents = Path.home() / "Documents" / "OpenOptima"
        documents.mkdir(parents=True, exist_ok=True)
        return documents
    return Path.cwd()


def main(argv: list[str] | None = None) -> int:
    # Required for the frozen Windows build: without it, every worker process
    # the optimiser spawns would relaunch the whole application.
    multiprocessing.freeze_support()

    log = _redirect_output_to_log()
    try:
        return _run(argv, windowless=log is not None)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        if log is not None:
            _show_error(
                f"OpenOptima could not start.\n\nWhat went wrong has been written to:\n{log}"
            )
        raise


def _wait_until_dismissed(url: str) -> None:
    """Keep a windowless build alive with something the user can actually close.

    Reached only when neither Edge nor Chrome was found and OpenOptima has
    fallen back to the default browser. That tab is not ours, so it gives us
    nothing to wait on, and the frozen build has no console window to close
    either. Without this the server would keep running invisibly after the user
    believes they have finished, stoppable only through Task Manager.
    """
    if os.name != "nt":  # pragma: no cover - Windows is the frozen target
        while True:
            time.sleep(0.5)
    import ctypes

    ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
        None,
        f"OpenOptima is running in your browser at\n{url}\n\n"
        "Leave this message open while you use it.\n"
        "Click OK to shut OpenOptima down.",
        "OpenOptima",
        0x40,  # MB_ICONINFORMATION
    )


def _run(argv: list[str] | None, *, windowless: bool = False) -> int:
    parser = argparse.ArgumentParser(
        prog="openoptima-app", description="Run OpenOptima as a desktop application."
    )
    parser.add_argument("--root", help="folder to look for parts in")
    parser.add_argument("--port", type=int, default=0, help="port to listen on")
    parser.add_argument("--no-browser", action="store_true", help="do not open a window")
    parser.add_argument(
        "--in-browser",
        action="store_true",
        help="open in the default browser instead of its own window",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    port = args.port or find_free_port()
    url = f"http://{HOST}:{port}/"

    server = create_server(root, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"OpenOptima is running at {url}")
    print(f"Looking for parts in {root}")

    window: subprocess.Popen[bytes] | None = None
    if not args.no_browser:
        # Let the server accept connections before anything asks it for a page.
        time.sleep(0.4)
        if not args.in_browser:
            window = open_window(url)
        if window is None:
            webbrowser.open(url)

    try:
        if window is not None:
            print("Close the OpenOptima window to stop it.")
            window.wait()
        elif windowless and not args.no_browser:
            _wait_until_dismissed(url)
        else:
            print("Close this window to stop it.")
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()
        if window is not None and window.poll() is None:
            window.terminate()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
