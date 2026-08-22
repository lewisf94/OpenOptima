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
import json
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
from typing import Any

from ..config import settings_directory
from .server import HOST, AppServer, create_server, find_free_port

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
    _seed_profile(profile)
    return profile


def _seed_profile(profile: Path) -> None:
    """Mark a brand-new profile as already set up.

    A profile Edge has never seen makes it run its welcome flow: accept terms,
    choose a theme, sign in, set as default. That flow opened in an ordinary
    browser window with tabs and an address bar -- so the first thing a new
    user saw was not the application at all, but a browser asking them
    questions. ``--no-first-run`` does not prevent it and neither does the
    ``First Run`` sentinel file on its own.

    Writing these settings before the first launch skips all of it, and the
    user gets the application window straight away.

    Only ever written once. If the file already exists the profile has been
    used, and whatever the user has changed since is theirs to keep.
    """
    sentinel = profile / "First Run"
    if sentinel.exists():
        return
    defaults = profile / "Default"
    try:
        defaults.mkdir(parents=True, exist_ok=True)
        (profile / "Local State").write_text(
            json.dumps({"browser": {"first_run_finished": True, "has_seen_welcome_page": True}}),
            encoding="utf-8",
        )
        (defaults / "Preferences").write_text(
            json.dumps(
                {
                    "browser": {
                        "has_seen_welcome_page": True,
                        "should_reset_check_default_browser": False,
                    },
                    "profile": {"exit_type": "Normal", "exited_cleanly": True},
                    "distribution": {
                        "make_chrome_default": False,
                        "make_chrome_default_for_user": False,
                        "skip_first_run_ui": True,
                        "suppress_first_run_bubble": True,
                        "import_history": False,
                        "import_search_engine": False,
                        "import_bookmarks": False,
                        "do_not_create_desktop_shortcut": True,
                        "do_not_create_quick_launch_shortcut": True,
                        "do_not_create_taskbar_shortcut": True,
                        "do_not_launch_chrome": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        sentinel.write_text("")
    except OSError:
        # A profile we could not prepare still works; the user just sees the
        # welcome flow once. Not a reason to refuse to start.
        pass


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
        "--no-service-autorun",
        "--disable-sync",
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


#: Everything the application only reaches once somebody actually uses it.
#: Each entry is a label and something to run that must not raise.
def _self_check_steps() -> list[tuple[str, Any]]:
    def geometry() -> object:
        from ..geometry.occ.provider import OccGeometryProvider

        return OccGeometryProvider

    def solver() -> object:
        from ..domain.model import SolverSpecification
        from ..solvers import create_solver

        return create_solver(SolverSpecification(name="calculix"))

    def optimiser() -> object:
        # The exact chain that shipped broken: pymoo imports moocore for its
        # hypervolume indicator, and moocore asks importlib.metadata what
        # version of itself is installed. A frozen build without the .dist-info
        # folder raises PackageNotFoundError here -- and not before.
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.operators.sampling.lhs import LHS
        from pymoo.optimize import minimize
        from pymoo.termination.default import DefaultMultiObjectiveTermination

        return (NSGA2, LHS, minimize, DefaultMultiObjectiveTermination)

    def mesher() -> object:
        import gmsh

        return gmsh

    def problem() -> object:
        from ..optimisation.problem import StructuralProblem

        return StructuralProblem

    def surface_route() -> object:
        # How a topology result gets analysed: measure the faces of a triangle
        # mesh, then mesh a solid from it. Imported only when somebody runs a
        # topology optimisation, so a frozen build could ship it broken.
        from ..meshing.sources import load_surface
        from ..regions.discrete import measure_discrete_surface

        return (measure_discrete_surface, load_surface)

    def printability() -> object:
        # Trap 9, and this one is worse than an import. The wall check calls
        # trimesh, which reaches every spatial query through `rtree` -- a
        # compiled extension shipping its own libspatialindex. `import trimesh`
        # succeeds without it and the call then raises, so importing the module
        # proves nothing. This runs a real measurement on a two-triangle-thick
        # box, which is the only thing that proves the whole chain is there.
        import numpy as np
        import trimesh

        from ..printing.overhang import measure_min_wall

        box = trimesh.creation.box(extents=(20.0, 15.0, 2.0))
        thickness = measure_min_wall(box)
        if not np.isclose(thickness, 2.0, rtol=1e-6):
            raise RuntimeError(
                f"a 2 mm box measured {thickness:.4f} mm thick, so the wall "
                f"check is present but wrong."
            )
        return thickness

    def stress_range() -> object:
        # Trap 9 again, and like the wall check this runs the arithmetic
        # rather than only importing it. pyLife imports pandas at module
        # scope, and a frozen build that missed pandas would import
        # `openoptima.results.fatigue` perfectly well and then fail on the
        # first project that mentions a load cycle.
        #
        # The number checked is the one the whole capability exists for: a
        # fully reversed cycle must swing by the whole stress, not by nothing.
        import numpy as np

        from ..domain.fatigue import FatigueSettings, LoadCycle
        from ..domain.model import StressEvaluation
        from ..results.fatigue import cycle_stress
        from ..solvers.base import LoadCaseFields

        pull = np.array([[100.0, 0.0, 0.0, 0.0, 0.0, 0.0]])

        def side(name: str, tensor: np.ndarray) -> LoadCaseFields:
            return LoadCaseFields(
                load_case_id=name,
                node_tags=np.array([1]),
                displacement=np.zeros((1, 3)),
                von_mises=np.zeros(1),
                reaction_force=(0.0, 0.0, 0.0),
                stress_tensor=tensor,
            )

        cycle = LoadCycle(name="check", between=("up", "down"))
        measured = cycle_stress(
            cycle,
            {"up": side("up", pull), "down": side("down", -pull)},
            FatigueSettings(enabled=True, cycles=(cycle,)),
            StressEvaluation(measure="raw_max"),
        )
        if not np.isclose(measured.amplitude_max, 100.0, rtol=1e-9):
            raise RuntimeError(
                f"a fully reversed 100 MPa cycle measured a swing of "
                f"{measured.amplitude_max:.4f} MPa, so the stress range is "
                f"present but wrong."
            )

        # The life reaches different pyLife submodules again -- the Woehler
        # curve and the mean-stress correction -- so importing the first one
        # proves nothing about these. Checked against the closed form
        # N = ND * (SD / S) ** k, which for 100 MPa on this curve is 312 500.
        from ..domain.fatigue import FatigueCurve
        from ..results.fatigue import cycle_life

        life = cycle_life(
            measured,
            FatigueCurve(
                endurance_stress=50.0,
                endurance_cycles=1.0e7,
                slope=5.0,
                mean_stress_sensitivity=0.3,
            ),
        )
        if not np.isclose(life, 1.0e7 * (50.0 / 100.0) ** 5.0, rtol=1e-9):
            raise RuntimeError(
                f"a 100 MPa swing on a curve that should give 312 500 cycles "
                f"reported {life:.4g}, so the fatigue life is present but wrong."
            )
        return measured.amplitude_max

    return [
        ("geometry", geometry),
        ("mesher", mesher),
        ("solver adapter", solver),
        ("optimiser", optimiser),
        ("optimiser problem", problem),
        ("surface route", surface_route),
        ("printability", printability),
        ("stress range", stress_range),
    ]


def self_check() -> int:
    """Prove the parts that are only imported once the app is really working.

    A frozen build that starts and then dies on first use is the normal failure
    mode for this application, and it is very hard to diagnose on somebody
    else's machine. Starting the server proves almost nothing: the optimiser,
    the mesher, the solver adapter and the geometry provider are all imported
    lazily, so without this the first person to find out one is missing is a
    user, an hour into a study.

    Exactly that happened. The build shipped with the optimiser unable to
    import, and the smoke test -- which only asked the server for its status --
    walked straight past it.
    """
    failures = 0
    for label, step in _self_check_steps():
        try:
            step()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {label}")
    if failures:
        print(f"\n{failures} of {len(_self_check_steps())} checks failed.")
        return 1
    print("\nEverything the app needs at runtime imports correctly.")
    return 0


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


#: How long the page may go quiet before the app decides the window is gone.
#: It pings every two seconds, so this tolerates a few missed ones -- a laptop
#: waking from sleep, or a browser throttling a background tab.
_QUIET_SECONDS = 12.0

#: How long to wait for the page to appear at all before giving up. Generous
#: on purpose: on a brand-new profile the user has to click through Edge's
#: welcome screens first, and that is not a fast job.
_FIRST_CONTACT_SECONDS = 600.0


def _supervise(server: AppServer, window: subprocess.Popen[bytes] | None) -> None:
    """Stay running while the page is alive, then stop.

    **Waiting on the browser process does not work**, which cost a release to
    find out. Launching Edge with a fresh profile directory starts a process
    that sets the profile up, hands the actual window to a *different* process
    and exits about half a second later -- reliably, not occasionally. Waiting
    on it therefore returned almost immediately, the server shut down, and the
    window that then appeared showed "127.0.0.1 refused to connect". The user
    saw the app fail on the very first launch, which is the worst possible
    time. The `First Run` sentinel file does not prevent the hand-off either.

    So the page itself says whether it is still there: it pings `/api/alive`
    every couple of seconds, and any other request counts too. When the pings
    stop, the window has been closed and the application exits with it.
    """
    state = server.app_state
    started = time.monotonic()
    while True:
        time.sleep(0.5)
        if state.ever_seen:
            if time.monotonic() - state.last_seen > _QUIET_SECONDS:
                return
        elif time.monotonic() - started > _FIRST_CONTACT_SECONDS:
            print("the page never loaded; giving up")
            return
        elif window is not None and window.poll() is not None:
            # The browser is gone and nothing ever loaded. Keep waiting: this
            # is the normal hand-off described above, not a failure.
            window = None


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
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="check that everything needed at runtime can be imported, then exit",
    )
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

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
        if not args.no_browser:
            print("Close the OpenOptima window to stop it.")
            _supervise(server, window)
        elif windowless:
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
