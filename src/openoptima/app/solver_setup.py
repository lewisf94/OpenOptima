"""Fetching a stress solver for a user who does not have one.

OpenOptima builds the part and the mesh itself, but the stress calculation is
done by CalculiX, a separate program. Without it the app starts and can do
nothing, which is the first-run experience recorded in ``docs/known-issues.md``.

This downloads CalculiX to the user's own machine, from its own official home.
OpenOptima is not redistributing it: the file comes straight from the CalculiX
project, exactly as it would if the user fetched it by hand. That distinction
is the reason this module exists instead of a solver inside the installer --
shipping the binary ourselves would oblige us to also ship the matching source
code, and doing that properly is a decision for a human, not something to slip
into a build script. ``packaging/README.md`` has the detail.

Three things are pinned rather than discovered, and all three matter:

* **A commit, not a branch.** A ``master`` URL serves whatever is there today.
  A commit hash serves the same bytes forever, so an upstream change cannot
  silently alter what gets downloaded and run on somebody's machine.
* **A checksum**, verified before anything is unpacked.
* **An allowed list of files**, so a surprising archive cannot write outside
  the destination folder.

Updating to a newer CalculiX means changing the three constants together and
re-checking the hash by hand. That is deliberate friction.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import remember_solver, remembered_solver, settings_directory
from ..domain.model import SolverSpecification
from ..solvers.calculix.runner import find_executable, installation_hint, verify_executable

#: Where the download comes from. Pinned to one commit of the CalculiX
#: project's own Windows repository, which publishes the build scripts,
#: patches and source next to the binaries.
DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/calculix/CalculiX-Windows/"
    "60c9a10b350ec813f4d493098f47f7a8b96cd276/releases/CalculiX-2.23.0-win-x64.zip"
)
DOWNLOAD_SHA256 = "79a674fdee735a22720bbd12bb9ccbadad1e8c8ad5b1de74e1c62f8808b2d395"
DOWNLOAD_SIZE = 26_400_736
SOLVER_VERSION = "2.23"

#: Where the source for this exact build lives, for anyone who wants it.
SOURCE_URL = "https://github.com/calculix/CalculiX-Windows"

_ARCHIVE_PREFIX = "CalculiX-2.23.0-win-x64/bin/"

#: Only what the solver actually needs to run, established by removing each
#: file in turn and re-running a real analysis. ``cgx.exe`` is the separate
#: viewer, ``libstdc++-6.dll`` belongs to it, and neither is used here. Taking
#: only these turns a 69 MB install into about 10 MB.
#:
#: ``LICENSE.txt`` is not optional. It is CalculiX's own licence and must stay
#: with the program.
_WANTED = (
    "ccx.exe",
    "libgcc_s_seh-1.dll",
    "libgfortran-3.dll",
    "libgomp-1.dll",
    "libquadmath-0.dll",
    "libwinpthread-1.dll",
    "pthreadGC2.dll",
    "LICENSE.txt",
)

#: Refuse anything wildly larger than the archive we pinned, so a redirect to
#: something unexpected cannot fill the user's disk before the hash is checked.
_MAXIMUM_DOWNLOAD_BYTES = 64 * 1024 * 1024

ProgressCallback = Callable[[str, float], None]


class SolverInstallError(RuntimeError):
    """Something went wrong fetching or unpacking the solver."""


@dataclass(frozen=True)
class InstalledSolver:
    executable: Path
    version: str
    directory: Path


def install_directory() -> Path:
    return settings_directory() / "solver"


def solver_status() -> dict[str, Any]:
    """Everything the setup screen needs to decide what to offer.

    Note there is no list of "solvers we spotted for you": anything findable in
    a usual location has already been found by :func:`find_executable`, so it
    would be available rather than a suggestion.
    """
    found = find_executable(SolverSpecification(name="calculix"))
    version = ""
    if found:
        ok, detail = verify_executable(found)
        version = detail if ok else ""
    chosen = remembered_solver()
    return {
        "available": bool(found),
        "path": found or "",
        "version": version,
        "message": "" if found else installation_hint(),
        # True when this came from the user picking or installing it, which is
        # the only case where offering to undo the choice makes sense.
        "chosen_by_user": bool(chosen and found and Path(chosen) == Path(found)),
        "can_install": is_supported(),
        "install_note": "" if is_supported() else unsupported_reason(),
        "download": {
            "version": SOLVER_VERSION,
            "megabytes": round(DOWNLOAD_SIZE / 1e6),
            "source": SOURCE_URL,
        },
    }


def is_supported() -> bool:
    """Only Windows has a pinned build. Elsewhere a package manager is better."""
    import os

    return os.name == "nt"


def unsupported_reason() -> str:
    return (
        "Automatic install is Windows-only. On Debian or Ubuntu run "
        "'sudo apt install calculix-ccx'; on macOS run 'brew install calculix'. "
        "Then use 'Find it on my computer' if it is still not picked up."
    )


def _download(destination: Path, progress: ProgressCallback | None) -> None:
    digest = hashlib.sha256()
    received = 0
    # https only, and a pinned host: the URL is a module constant, never
    # anything a project file or a page can influence. Both calls need the
    # suppression -- newer ruff audits the Request as well as the urlopen.
    request = urllib.request.Request(  # noqa: S310
        DOWNLOAD_URL, headers={"User-Agent": "OpenOptima"}
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,  # noqa: S310
            destination.open("wb") as handle,
        ):
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > _MAXIMUM_DOWNLOAD_BYTES:
                    raise SolverInstallError(
                        "the download was much larger than expected and was stopped"
                    )
                digest.update(chunk)
                handle.write(chunk)
                if progress:
                    progress("downloading", min(received / DOWNLOAD_SIZE, 1.0))
    except SolverInstallError:
        raise
    except OSError as exc:
        raise SolverInstallError(
            f"could not download CalculiX: {exc}. Check the internet connection, "
            "or use 'Find it on my computer' if you already have a copy."
        ) from exc

    actual = digest.hexdigest()
    if actual != DOWNLOAD_SHA256:
        # Refuse rather than run it. A file that does not match is either a
        # damaged download or not the file we pinned, and neither should be
        # unpacked and executed.
        raise SolverInstallError(
            "the downloaded file did not match its expected checksum, so it was "
            "discarded. Try again; if it keeps happening, install CalculiX by "
            "hand and use 'Find it on my computer'."
        )


def _extract(archive: Path, target: Path, progress: ProgressCallback | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        available = set(bundle.namelist())
        missing = [name for name in _WANTED if _ARCHIVE_PREFIX + name not in available]
        if missing:
            raise SolverInstallError(f"the CalculiX archive did not contain {', '.join(missing)}")
        for index, name in enumerate(_WANTED):
            # The destination name is ours, never the archive's, so a crafted
            # entry cannot escape the target folder.
            with bundle.open(_ARCHIVE_PREFIX + name) as source:
                (target / name).write_bytes(source.read())
            if progress:
                progress("unpacking", (index + 1) / len(_WANTED))


def install(progress: ProgressCallback | None = None) -> InstalledSolver:
    """Download, check, unpack and verify CalculiX. Remembers it on success.

    Nothing is remembered until the unpacked solver has actually run and
    reported its version, so a half-finished install cannot leave the app
    pointing at something broken.
    """
    if not is_supported():
        raise SolverInstallError(unsupported_reason())

    target = install_directory()
    with tempfile.TemporaryDirectory(prefix="openoptima-solver-") as scratch:
        archive = Path(scratch) / "calculix.zip"
        _download(archive, progress)
        staged = Path(scratch) / "bin"
        _extract(archive, staged, progress)

        executable = staged / "ccx.exe"
        ok, message = verify_executable(executable)
        if not ok:
            raise SolverInstallError(f"the downloaded solver did not run: {message}")

        # Only now replace anything already installed, so a failed attempt
        # leaves a previously working solver untouched.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(target))

    installed = target / "ccx.exe"
    ok, version = verify_executable(installed)
    if not ok:
        raise SolverInstallError(f"the installed solver did not run: {version}")

    remember_solver(installed)
    return InstalledSolver(executable=installed, version=version, directory=target)


@dataclass
class BackgroundInstall:
    """Runs :func:`install` on a worker thread so the page can show progress.

    The download is tens of megabytes and takes long enough that doing it
    inside a request would look like the app had hung.
    """

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _state: str = "idle"
    _stage: str = ""
    _fraction: float = 0.0
    _message: str = ""

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state == "running"

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "stage": self._stage,
                "fraction": round(self._fraction, 3),
                "message": self._message,
            }

    def start(self) -> None:
        with self._lock:
            if self._state == "running":
                raise SolverInstallError("an install is already running")
            self._state = "running"
            self._stage = "starting"
            self._fraction = 0.0
            self._message = ""
        threading.Thread(target=self._run, daemon=True).start()

    def _progress(self, stage: str, fraction: float) -> None:
        with self._lock:
            self._stage = stage
            self._fraction = fraction

    def _run(self) -> None:
        try:
            result = install(self._progress)
        except SolverInstallError as exc:
            with self._lock:
                self._state, self._stage, self._message = "error", "", str(exc)
            return
        except Exception as exc:  # pragma: no cover - unexpected, still must not hang
            with self._lock:
                self._state, self._stage = "error", ""
                self._message = f"unexpected problem installing the solver: {exc}"
            return
        with self._lock:
            self._state = "done"
            self._stage = ""
            self._fraction = 1.0
            self._message = f"CalculiX {result.version} is ready."
