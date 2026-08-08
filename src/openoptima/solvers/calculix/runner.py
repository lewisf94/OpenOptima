"""Running CalculiX as a subprocess, safely.

Rules enforced here:

* the command is always an argument **list**, never a shell string — a project
  path containing a space or a semicolon must not be able to run anything;
* every run has a timeout and its process group is killed on expiry, so a
  wedged solver cannot hold an optimisation open indefinitely;
* a non-zero exit, a missing result file and a convergence failure are three
  *different* failure codes, because only some are worth retrying.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ...config import remembered_solver
from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.model import SolverSpecification

#: Strings CalculiX prints when it gives up.
_NONCONVERGENCE_MARKERS = (
    "*ERROR in nonlingeo",
    "increment size smaller than minimum",
    "job exceeded the maximum number of increments",
    "singular matrix",
    "nonpositive jacobian",
)


@dataclass(frozen=True)
class SolverRun:
    job_name: str
    directory: Path
    return_code: int
    wall_time: float
    stdout_path: Path
    stderr_path: Path
    frd_path: Path
    dat_path: Path
    version: str = ""


#: True on Windows. Process control is the only place the platform leaks in.
WINDOWS = os.name == "nt"

#: Environment variable pointing straight at the solver, checked before PATH.
#: The Windows installer sets this rather than modifying a system PATH.
_EXECUTABLE_ENV_VAR = "OPENOPTIMA_CCX"

_EXECUTABLE_NAMES = (
    "ccx",
    "ccx_static",
    "ccx_dynamic",
    "ccx_2.22",
    "ccx_2.21",
    "ccx_2.20",
    "CalculiX",
)


def _windows_search_paths() -> list[Path]:
    """Where CalculiX actually lands on a Windows machine.

    Windows has no package manager convention, so the two common distributions
    (bConverged's CalculiX for Windows, and the copy bundled with PrePoMax) put
    the binary in their own install trees and neither adds it to PATH. Looking
    in these places turns "solver not found" into "it just works" for most users.
    """
    roots: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))

    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / "bConverged" / "CalculiX" / "bin" / "ccx.exe")
        candidates.append(root / "CalculiX" / "bin" / "ccx.exe")
        candidates.append(root / "CalculiX" / "ccx.exe")
        # PrePoMax ships a solver and versions its directory name.
        for prepomax in sorted(root.glob("PrePoMax*"), reverse=True):
            candidates.append(prepomax / "Solver" / "ccx.exe")
            candidates.append(prepomax / "ccx.exe")
    return candidates


def _bundled_search_paths() -> list[Path]:
    """A solver shipped alongside OpenOptima itself.

    ``sys._MEIPASS`` comes first and is the one that actually matters.
    PyInstaller 6 no longer puts bundled data next to the executable: a
    one-folder build lands it in an ``_internal`` subfolder, and a one-file
    build unpacks it to a temporary directory. ``_MEIPASS`` points at the right
    place in both, whereas looking beside the executable is correct for neither.

    The remaining candidates are kept because they cost nothing and cover the
    older layout and a solver dropped in by hand next to the exe. Getting this
    wrong is quiet rather than loud -- a shipped solver simply would not be
    found, and the app would fall back to asking the user to install one they
    already have.
    """
    import sys

    candidates: list[Path] = []
    name = "ccx.exe" if WINDOWS else "ccx"

    if getattr(sys, "frozen", False):  # PyInstaller and friends
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "solver" / name)
        base = Path(sys.executable).parent
        candidates.append(base / "_internal" / "solver" / name)
        candidates.append(base / "solver" / name)
        candidates.append(base / name)
    here = Path(__file__).resolve().parents[3]
    candidates.append(here / "solver" / name)
    return candidates


def find_executable(specification: SolverSpecification) -> str | None:
    """Locate the CalculiX binary.

    Order: the project file, then ``OPENOPTIMA_CCX``, then the solver the user
    chose in the app, then one bundled with this installation, then PATH, then
    the platform's usual install locations.

    The user's own choice sits above the bundled solver on purpose. Somebody
    who has gone to the trouble of pointing at a particular build -- a newer
    version, or one they compiled -- means it, and silently preferring the
    shipped copy would ignore them without saying so.
    """
    if specification.executable:
        candidate = Path(specification.executable)
        if candidate.exists():
            return str(candidate)
        return shutil.which(specification.executable)

    from_env = os.environ.get(_EXECUTABLE_ENV_VAR)
    if from_env:
        candidate = Path(from_env)
        if candidate.exists():
            return str(candidate)
        found = shutil.which(from_env)
        if found:
            return found

    chosen = remembered_solver()
    if chosen:
        return chosen

    for candidate in _bundled_search_paths():
        if candidate.exists():
            return str(candidate)

    # shutil.which appends PATHEXT (.exe) automatically on Windows.
    for name in _EXECUTABLE_NAMES:
        found = shutil.which(name)
        if found:
            return found

    if WINDOWS:
        for candidate in _windows_search_paths():
            if candidate.exists():
                return str(candidate)
    return None


def installation_hint() -> str:
    """Platform-appropriate advice when the solver is missing.

    Written for somebody at a command line who has just been stopped dead. The
    desktop app is offered first on Windows because it is the only route that
    does not ask them to find, download and unpack anything themselves.
    """
    existing = (
        "Or point OpenOptima at a copy you already have, by setting the "
        f"{_EXECUTABLE_ENV_VAR} environment variable or solver.executable in the "
        "project file."
    )
    if WINDOWS:
        return (
            "CalculiX (ccx.exe) not found. OpenOptima cannot work out any stresses "
            "without it: it is a separate free program that does the stress "
            "calculation. Run 'openoptima-app' and it will offer to install "
            f"CalculiX for you. {existing} PrePoMax and bConverged's CalculiX for "
            "Windows both include a usable ccx.exe."
        )
    return (
        "CalculiX (ccx) not found. OpenOptima cannot work out any stresses without "
        "it: it is a separate free program that does the stress calculation. "
        "Install it with 'apt install calculix-ccx' on Debian or Ubuntu, or "
        f"'brew install calculix' on macOS. {existing}"
    )


def verify_executable(candidate: str | Path) -> tuple[bool, str]:
    """Check a candidate really is a working CalculiX solver, before storing it.

    Running it is the only check worth anything. A Windows CalculiX is a small
    executable beside seven runtime DLLs, and a copy separated from those DLLs
    exists, is the right size, and dies instantly with a Windows error code and
    no message. Asking it for its version catches that in about a second, where
    trusting the file name would leave the user with a solver that fails much
    later, in the middle of a study.
    """
    path = Path(candidate).expanduser()
    if not path.exists():
        return False, f"There is nothing at {path}."
    if not path.is_file():
        return False, f"{path} is a folder, not the solver program itself."

    # cgx is the CalculiX viewer and sits next to ccx in every distribution, so
    # it is the easiest wrong file to pick.
    stem = path.stem.lower()
    if stem.startswith("cgx"):
        return False, (
            f"{path.name} is the CalculiX viewer, not the solver. "
            "Look for ccx.exe in the same folder."
        )

    version = solver_version(str(path))
    if not version:
        return False, (
            f"{path.name} would not run. On Windows this usually means it has "
            "been copied away from the DLL files that came with it -- point at "
            "the ccx.exe still sitting in its original folder."
        )
    return True, version


def solver_version(executable: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list
            [executable, "-v"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (result.stdout or result.stderr or "").strip()
    for line in text.splitlines():
        if "version" in line.lower():
            return line.strip()
    return text.splitlines()[0].strip() if text else ""


def run_calculix(
    specification: SolverSpecification,
    job_name: str,
    directory: Path,
) -> SolverRun:
    executable = find_executable(specification)
    if executable is None:
        raise EvaluationFailure(
            FailureCode.SOLVER_NOT_FOUND,
            installation_hint(),
        )

    environment = dict(os.environ)
    threads = max(1, int(specification.threads))
    environment["OMP_NUM_THREADS"] = str(threads)
    environment["CCX_NPROC_STIFFNESS"] = str(threads)

    stdout_path = directory / f"{job_name}.stdout.log"
    stderr_path = directory / f"{job_name}.stderr.log"
    command = [executable, *specification.extra_options, job_name]

    started = time.monotonic()
    try:
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            process = subprocess.Popen(  # noqa: S603 - fixed argument list
                command,
                cwd=str(directory),
                stdout=out,
                stderr=err,
                env=environment,
                **_process_isolation_kwargs(),
            )
            try:
                return_code = process.wait(timeout=specification.timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate(process)
                raise EvaluationFailure(
                    FailureCode.SOLVER_TIMEOUT,
                    f"CalculiX exceeded its {specification.timeout_seconds:g} s timeout",
                    detail={"job": job_name, "directory": str(directory)},
                ) from None
    except OSError as exc:
        raise EvaluationFailure(
            FailureCode.SOLVER_CRASH, f"could not start CalculiX: {exc}"
        ) from exc

    wall_time = time.monotonic() - started
    frd_path = directory / f"{job_name}.frd"
    dat_path = directory / f"{job_name}.dat"
    log_text = _read_tail(stdout_path)

    lowered = log_text.lower()
    for marker in _NONCONVERGENCE_MARKERS:
        if marker.lower() in lowered:
            raise EvaluationFailure(
                FailureCode.SOLVER_NONCONVERGENCE,
                f"CalculiX did not converge: {marker}",
                detail={"log_tail": log_text[-2000:]},
            )

    if return_code != 0:
        raise EvaluationFailure(
            FailureCode.SOLVER_CRASH,
            f"CalculiX exited with code {return_code}",
            detail={"log_tail": log_text[-2000:], "command": command},
        )

    if not frd_path.exists() or frd_path.stat().st_size == 0:
        raise EvaluationFailure(
            FailureCode.RESULT_FILE_MISSING,
            f"CalculiX exited cleanly but produced no results in {frd_path.name}",
            detail={"log_tail": log_text[-2000:]},
        )

    return SolverRun(
        job_name=job_name,
        directory=directory,
        return_code=return_code,
        wall_time=wall_time,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        frd_path=frd_path,
        dat_path=dat_path,
        version=solver_version(executable),
    )


def _process_isolation_kwargs() -> dict[str, object]:
    """Put the solver in its own process group so it can be killed as a unit.

    The mechanism differs by platform and neither option exists on the other:
    ``start_new_session`` is POSIX-only and raises on Windows, while
    ``CREATE_NEW_PROCESS_GROUP`` is only defined in the Windows build of
    ``subprocess``.
    """
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate(process: subprocess.Popen) -> None:
    """Kill the solver and anything it started.

    A wedged solver that survives its timeout would hold an optimisation open
    indefinitely, so this has to be thorough rather than polite.
    """
    if WINDOWS:
        _terminate_windows(process)
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()


def _terminate_windows(process: subprocess.Popen) -> None:
    """Windows has no process groups to signal, so use taskkill.

    ``/T`` takes the whole tree and ``/F`` does not ask nicely. ``Popen.kill()``
    alone would leave any child the solver started running.
    """
    # Resolved from %SystemRoot% rather than PATH. Killing a process is exactly
    # the kind of call a hijacked PATH entry would like to intercept.
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 - absolute path, fixed argument list
            [taskkill, "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - taskkill rarely fails
        with contextlib.suppress(OSError):
            process.kill()


def _read_tail(path: Path, limit: int = 20000) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:]
