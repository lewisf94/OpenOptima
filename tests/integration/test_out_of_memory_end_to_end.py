"""The real runner, against a solver the operating system really kills.

``tests/unit/test_out_of_memory_classification.py`` covers the classifier on
its own. This checks the whole path: a process that dies exactly the way the
kernel kills one when it reclaims memory, through ``run_calculix``, coming out
as a failure that is not retried.

Needs no CAE tool -- the stand-in solver is three lines of shell.
"""

from __future__ import annotations

import os
import stat

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, is_retryable
from openoptima.domain.model import SolverSpecification
from openoptima.solvers.calculix.runner import run_calculix

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX signals; the Windows path exits with a code instead"
)


def _stand_in(directory, script: str):
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "ccx_stand_in"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (directory / "job.inp").write_text("** empty\n")
    return SolverSpecification(name="calculix", executable=str(fake.resolve()), timeout_seconds=60)


def test_a_solver_killed_by_the_kernel_is_not_retried(tmp_path) -> None:
    """SIGKILL is what the out-of-memory killer sends.

    Without the fix this arrives as SOLVER_CRASH -- retryable -- and the
    message reads "CalculiX exited with code -9", which tells nobody anything.
    """
    work = tmp_path / "killed"
    specification = _stand_in(work, "#!/bin/sh\nkill -9 $$\n")

    with pytest.raises(EvaluationFailure) as caught:
        run_calculix(specification, "job", work)

    assert caught.value.code is FailureCode.OUT_OF_MEMORY
    assert not is_retryable(caught.value.code)
    assert caught.value.detail["return_code"] == -9
    assert "running out of memory" in str(caught.value)
    assert "parallel_jobs" in str(caught.value)


def test_a_solver_that_exits_badly_is_still_a_retryable_crash(tmp_path) -> None:
    """The narrowing has to stay narrow: an ordinary bad exit is unchanged."""
    work = tmp_path / "exited"
    specification = _stand_in(work, "#!/bin/sh\nexit 201\n")

    with pytest.raises(EvaluationFailure) as caught:
        run_calculix(specification, "job", work)

    assert caught.value.code is FailureCode.SOLVER_CRASH
    assert is_retryable(caught.value.code)
    assert "201" in str(caught.value)


def test_a_segmentation_fault_is_named_and_not_called_memory(tmp_path) -> None:
    """A real defect in the solver must not send somebody to buy more RAM."""
    work = tmp_path / "segv"
    specification = _stand_in(work, "#!/bin/sh\nkill -11 $$\n")

    with pytest.raises(EvaluationFailure) as caught:
        run_calculix(specification, "job", work)

    assert caught.value.code is FailureCode.SOLVER_CRASH
    assert "segmentation fault" in str(caught.value)
    assert "memory" not in str(caught.value)
