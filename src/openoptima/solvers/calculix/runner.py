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

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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


def find_executable(specification: SolverSpecification) -> str | None:
    if specification.executable:
        candidate = Path(specification.executable)
        if candidate.exists():
            return str(candidate)
        return shutil.which(specification.executable)
    for name in ("ccx", "ccx_2.22", "ccx_2.21", "ccx_2.20", "CalculiX"):
        found = shutil.which(name)
        if found:
            return found
    return None


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
            "CalculiX executable not found. Install it (Debian/Ubuntu: "
            "'apt install calculix-ccx') or set solver.executable in the project file.",
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
                start_new_session=True,
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


def _terminate(process: subprocess.Popen) -> None:
    """Kill the whole process group; CalculiX spawns helpers."""
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


def _read_tail(path: Path, limit: int = 20000) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:]
