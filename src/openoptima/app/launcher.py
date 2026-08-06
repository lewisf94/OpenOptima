"""Starting the desktop app.

Double-clicking the Windows executable lands here: start a local server, open
the default browser at it, and stay running until the window is closed or the
process is stopped.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .server import HOST, create_server, find_free_port


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

    parser = argparse.ArgumentParser(
        prog="openoptima-app", description="Run OpenOptima as a desktop application."
    )
    parser.add_argument("--root", help="folder to look for parts in")
    parser.add_argument("--port", type=int, default=0, help="port to listen on")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    port = args.port or find_free_port()
    url = f"http://{HOST}:{port}/"

    server = create_server(root, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"OpenOptima is running at {url}")
    print(f"Looking for parts in {root}")
    print("Close this window to stop it.")

    if not args.no_browser:
        # Give the server a moment to accept connections before the browser asks.
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
