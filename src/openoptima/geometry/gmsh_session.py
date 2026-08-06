"""Process-wide gmsh session management.

gmsh keeps global state in a C library, so nested or overlapping
``initialize``/``finalize`` calls corrupt each other.  Every use of gmsh in
OpenOptima goes through this context manager, which also silences the terminal
banner and captures gmsh's own log so it can be attached to a failure report.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from typing import Any

_LOCK = threading.RLock()


@contextlib.contextmanager
def gmsh_session(verbosity: int = 0, capture_log: bool = True) -> Iterator[Any]:
    """Yield the initialised ``gmsh`` module, guaranteeing cleanup.

    Serialised with a lock: gmsh is not safe to drive from two threads at once.
    Real parallelism comes from separate *processes* (see
    :mod:`openoptima.scheduling`), not threads.
    """
    import gmsh  # imported lazily so the domain layer stays tool-free

    with _LOCK:
        already_running = gmsh.isInitialized()
        if not already_running:
            _initialise(gmsh)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.Verbosity", verbosity)
            if capture_log:
                gmsh.logger.start()
            yield gmsh
        finally:
            if capture_log:
                with contextlib.suppress(Exception):
                    gmsh.logger.stop()
            if not already_running:
                with contextlib.suppress(Exception):
                    gmsh.clear()
                with contextlib.suppress(Exception):
                    gmsh.finalize()


def _initialise(gmsh: Any) -> None:
    """Start gmsh in a way that works off the main thread.

    ``gmsh.initialize()`` installs a SIGINT handler so Ctrl-C interrupts a long
    mesh. Python only allows signal handlers to be installed from the main
    thread, so the call raises ``ValueError: signal only works in main thread of
    the main interpreter`` when it first runs on a worker -- which is exactly
    where the desktop app runs it, since requests are served on a thread pool.

    The failure is nastily shaped: gmsh is left partly initialised, so
    ``isInitialized()`` then returns True and every *subsequent* call succeeds.
    Only the first operation after launch fails, which made it look intermittent
    until the message was read.

    ``interruptible=False`` skips the handler. Ctrl-C still stops the process;
    it just cannot interrupt gmsh mid-operation, which is irrelevant here
    because operations are seconds long and run in a worker anyway.
    """
    try:
        gmsh.initialize(interruptible=False)
    except TypeError:  # pragma: no cover - gmsh older than 4.9
        gmsh.initialize()


@contextlib.contextmanager
def suppress_native_output() -> Iterator[None]:
    """Silence writes made directly to file descriptor 1 by native code.

    OpenCASCADE's STEP writer prints a banner straight to the C-level stdout,
    bypassing both Python's ``sys.stdout`` and gmsh's own verbosity setting.
    Across a few hundred evaluations that buries the progress output entirely.
    """
    import os

    try:
        saved = os.dup(1)
    except OSError:  # pragma: no cover - no stdout to redirect
        yield
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def drain_log(gmsh_module: Any) -> list[str]:
    """Return gmsh's messages since the session started."""
    try:
        return list(gmsh_module.logger.get())
    except Exception:  # pragma: no cover - logger not started
        return []


def log_errors(messages: list[str]) -> list[str]:
    return [m for m in messages if m.startswith(("Error", "Warning"))]
