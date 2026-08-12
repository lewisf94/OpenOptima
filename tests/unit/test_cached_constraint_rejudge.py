"""Changing a limit and re-running must re-judge, not replay.

**The defect this guards.** A constraint threshold is deliberately not part
of the evaluation hash, and should not be: moving "factor of safety at least
2" to "at least 2.5" changes no computed number, so the stress, mass and
frequency in the cache are all still correct and re-solving them would be
waste.

But the stored result also carries the *verdict* reached under the old
limits, and that verdict was replayed. Measured on ``examples/drone_arm``
when its frequency limit was lowered from 195 to 170 Hz: 30 of the next
run's 50 designs came back from cache still carrying "First natural
frequency >= 195", 9 of them marked infeasible while their stored frequency
cleared 170 Hz. The lightest of those was **72.1 g at 175.14 Hz** — lighter
than the 72.7 g the run then reported as its best answer. The optimiser was
told its own best design was unavailable.

Nothing about that is visible: the run completes, the numbers are all real,
and only the verdict attached to them is stale.

These need no CAE tool.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from openoptima.domain.failures import FailureCode, Outcome
from openoptima.domain.objectives import Constraint, Direction, Objective, Operator
from openoptima.domain.results import EvaluationResult, EvaluationState
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.evaluation.pipeline import rejudge
from openoptima.schema.loader import load_project

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"

#: The measured design from the drone arm run: it fails a 195 Hz limit and
#: passes a 170 Hz one.
MEASURED = {
    "mass_kg": 0.0721,
    "factor_of_safety": 5.1,
    "displacement_max_mm": 0.72,
    "natural_frequency_hz": 175.14,
}


def _project(limit_hz: float):
    project = load_project(DRONE_ARM)
    constraints = (
        *(c for c in project.constraints if c.metric != "natural_frequency_hz"),
        Constraint(
            metric="natural_frequency_hz",
            operator=Operator.GE,
            value=limit_hz,
            label="First natural frequency",
        ),
    )
    return replace(project, constraints=constraints)


def _cached(outcome: Outcome, violations: dict[str, float]) -> EvaluationResult:
    """A result as it comes back out of the database, verdict and all."""
    space = DesignSpace((DesignVariable(id="width", minimum=12.0, maximum=30.0, default=20.0),))
    return EvaluationResult(
        design=space.decode({"width": 26.0}),
        outcome=outcome,
        state=(
            EvaluationState.ACCEPTED if outcome is Outcome.OK else EvaluationState.CHECKS_COMPLETE
        ),
        metrics=dict(MEASURED),
        constraint_violations=violations,
        failure_code=(None if outcome is Outcome.OK else FailureCode.ENGINEERING_CONSTRAINT_FAILED),
        from_cache=True,
    )


def test_a_design_rejected_under_the_old_limit_is_accepted_under_the_new_one() -> None:
    """The measured case, and the one that cost a better answer.

    175.14 Hz was stored as infeasible against a 195 Hz limit. Against 170 Hz
    it passes, and at 72.1 g it is lighter than the design the run reported.
    """
    stale = _cached(Outcome.INFEASIBLE, {"First natural frequency >= 195": 0.1019})
    judged = rejudge(stale, _project(170.0))

    assert judged.outcome is Outcome.OK, (
        "a design whose stored frequency clears the current limit must be "
        "re-judged as feasible, not replayed as rejected"
    )
    assert judged.constraint_violations == {}
    assert judged.failure_code is None
    assert judged.state is EvaluationState.ACCEPTED


def test_a_design_accepted_under_the_old_limit_is_rejected_when_it_tightens() -> None:
    """The same defect in the dangerous direction.

    Replaying a stale pass would let a design through that the engineer has
    just decided is not good enough, which is worse than the other way round.
    """
    stale = _cached(Outcome.OK, {})
    judged = rejudge(stale, _project(195.0))

    assert judged.outcome is Outcome.INFEASIBLE
    assert judged.failure_code is FailureCode.ENGINEERING_CONSTRAINT_FAILED
    assert "First natural frequency >= 195" in judged.message


def test_an_unchanged_limit_leaves_the_result_exactly_alone() -> None:
    stale = _cached(Outcome.OK, {})
    assert rejudge(stale, _project(170.0)) is stale


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.MANUFACTURING_RULE_VIOLATED,
        FailureCode.REGION_TOO_SMALL,
        FailureCode.FEATURE_FAILED,
        FailureCode.INVALID_SOLID,
    ],
)
def test_an_infeasible_shape_is_never_re_judged_into_a_good_one(code: FailureCode) -> None:
    """Only a verdict that constraints decided may be revisited.

    A design that broke a manufacturing rule, lost a region to a feature or
    failed to build is a fact about the shape. No change to a stress or
    frequency limit makes it buildable, and re-judging it would turn a real
    refusal into a pass.
    """
    stale = replace(
        _cached(Outcome.INFEASIBLE, {}),
        failure_code=code,
        message="the shape itself is no good",
    )
    judged = rejudge(stale, _project(170.0))

    assert judged is stale
    assert judged.outcome is Outcome.INFEASIBLE
    assert judged.failure_code is code


def test_a_result_with_no_metrics_is_left_alone() -> None:
    """A design that failed before any metric existed cannot be judged."""
    stale = replace(_cached(Outcome.INFEASIBLE, {}), metrics={})
    assert rejudge(stale, _project(170.0)) is stale


def test_the_objective_is_untouched_by_re_judging() -> None:
    """Re-judging decides feasibility only. It must not move a number."""
    stale = _cached(Outcome.INFEASIBLE, {"First natural frequency >= 195": 0.1019})
    judged = rejudge(stale, _project(170.0))
    assert judged.metrics == MEASURED
    assert judged.design == stale.design


def test_the_objectives_themselves_are_not_constraints() -> None:
    """Guard against re-judging quietly depending on objective order."""
    project = _project(170.0)
    assert any(
        isinstance(o, Objective) and o.direction is Direction.MINIMISE for o in project.objectives
    )
    stale = _cached(Outcome.INFEASIBLE, {"First natural frequency >= 195": 0.1019})
    assert rejudge(stale, project).outcome is Outcome.OK
