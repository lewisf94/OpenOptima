"""A design killed for running out of memory is not a crash, and not retried.

Before this existed, both routes to it were classified as ordinary crashes:
a solver stopped by the kernel reported "CalculiX exited with code -9", and a
worker process that vanished reported the standard library's own sentence
about a process being "terminated abruptly". Both are retryable, so the same
design was re-run against the same memory and failed the same way, costing an
evaluation to learn nothing.

Neither message tells the person running it what to do, and that is the more
useful half of the fix: the answer is nearly always to run fewer designs at
once.

**What is measured rather than assumed.** A process killed by SIGKILL, which
is what the kernel sends when it reclaims memory, returns -9 from
``subprocess.wait`` -- other signals give -2, -11, -15 and are still ordinary
crashes. A pool worker killed the same way surfaces as ``BrokenProcessPool``
with no exit code attached at all, so the cause genuinely cannot be read off
it, and the wording says so instead of asserting it.
"""

from __future__ import annotations

from concurrent.futures.process import BrokenProcessPool

import pytest

from openoptima.domain.failures import (
    RETRYABLE_CODES,
    FailureCode,
    Outcome,
    classify_exit_code,
    is_retryable,
    outcome_for,
)
from openoptima.evaluation.evaluator import _worker_failure


def test_a_solver_the_kernel_stopped_is_out_of_memory() -> None:
    """-9 is SIGKILL: nothing inside the program chose to stop."""
    code, message = classify_exit_code("CalculiX", -9)
    assert code is FailureCode.OUT_OF_MEMORY
    assert "running out of memory" in message
    assert "parallel_jobs" in message, "the message must say what to do about it"


def test_it_never_says_the_design_was_at_fault() -> None:
    """A memory failure says nothing about the shape, and the optimiser must
    not be told otherwise."""
    _code, message = classify_exit_code("CalculiX", -9)
    assert "not at fault" in message
    assert outcome_for(FailureCode.OUT_OF_MEMORY) is Outcome.ERROR
    assert FailureCode.OUT_OF_MEMORY not in RETRYABLE_CODES


def test_running_out_of_memory_is_not_retried() -> None:
    """The fix. A retry re-meshes the same design to the same size and runs it
    beside the same sibling workers, so it meets the same wall."""
    assert not is_retryable(FailureCode.OUT_OF_MEMORY)
    # The codes it used to be filed under are still worth retrying, so this is
    # a narrowing rather than a blanket change.
    assert is_retryable(FailureCode.SOLVER_CRASH)
    assert is_retryable(FailureCode.WORKER_CRASH)


@pytest.mark.parametrize(
    ("return_code", "expected"),
    [
        (-2, "interrupted (Ctrl-C)"),
        (-6, "aborted"),
        (-11, "a segmentation fault"),
        (-15, "asked to stop"),
    ],
)
def test_other_signals_stay_ordinary_crashes_and_are_named(return_code, expected) -> None:
    """Only SIGKILL means memory. A segmentation fault is a real defect and a
    Ctrl-C is the user, and calling either "out of memory" would send somebody
    to buy RAM they do not need."""
    code, message = classify_exit_code("CalculiX", return_code)
    assert code is FailureCode.SOLVER_CRASH
    assert expected in message


def test_an_unknown_signal_is_still_reported_clearly() -> None:
    code, message = classify_exit_code("CalculiX", -31)
    assert code is FailureCode.SOLVER_CRASH
    assert "signal 31" in message


def test_an_ordinary_non_zero_exit_is_unchanged() -> None:
    """A solver that returned a code chose to stop, and that is a crash."""
    code, message = classify_exit_code("CalculiX", 201)
    assert code is FailureCode.SOLVER_CRASH
    assert "201" in message


def test_a_worker_that_vanished_is_out_of_memory() -> None:
    code, message = _worker_failure(
        BrokenProcessPool("A process in the process pool was terminated abruptly")
    )
    assert code is FailureCode.OUT_OF_MEMORY
    assert "running out of memory" in message
    assert "parallel_jobs" in message
    assert "not at fault" in message


def test_a_worker_that_raised_is_still_a_worker_crash() -> None:
    """An ordinary exception inside a worker is a defect, not memory, and it
    is worth retrying in case it was transient."""
    code, message = _worker_failure(ValueError("something went wrong"))
    assert code is FailureCode.WORKER_CRASH
    assert "ValueError" in message
    assert is_retryable(code)


def test_the_worker_message_says_it_takes_the_others_with_it() -> None:
    """One killed worker breaks the whole pool, so every design still being
    solved fails alongside it. Somebody reading a run with twelve errors in it
    needs to know they are one event, not twelve bad designs."""
    _code, message = _worker_failure(BrokenProcessPool("x"))
    assert "alongside it" in message
