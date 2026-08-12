"""A bad design must not come back from the mesher as a broken run.

The mesher tries progressively coarser settings when meshing fails. That is
right for a meshing problem and wrong for everything else, and the difference
is not cosmetic: an infeasible design relabelled ``MESH_GENERATION_FAILED``
is an infrastructure error, so the optimiser never learns to avoid it, the
evaluation is retried, and the budget pays four times over for an attempt
that could not have succeeded.

This was a real defect, found by adding a new infeasible failure code and
watching a 180 mm2 face come back as "every meshing attempt failed" with the
same message four times. The retry rule used to name the codes it would not
retry, so any code added later fell through it. It now asks the taxonomy.

No gmsh needed: the retry logic is exercised by making the inner attempt
raise, which is the only behaviour under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome, outcome_for
from openoptima.domain.model import MeshSpecification
from openoptima.meshing.gmsh_mesher import GmshMesher


class _Raising(GmshMesher):
    """A mesher whose every attempt fails with one chosen code."""

    def __init__(self, specification: MeshSpecification, code: FailureCode) -> None:
        super().__init__(specification)
        self.code = code
        self.attempts = 0

    def _attempt(self, *args: Any, **kwargs: Any) -> Any:
        self.attempts += 1
        raise EvaluationFailure(self.code, f"raised {self.code.value} on purpose")


def _mesh(code: FailureCode, tmp_path: Path) -> tuple[EvaluationFailure, int]:
    mesher = _Raising(MeshSpecification(global_size=5.0, minimum_size=1.0), code)
    with pytest.raises(EvaluationFailure) as caught:
        mesher._generate(
            lambda _gmsh: None,
            (),
            tmp_path,
            expected_regions=None,
            write_mesh_file=False,
        )
    return caught.value, mesher.attempts


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.REGION_TOO_SMALL,
        FailureCode.FEATURE_FAILED,
        FailureCode.INVALID_SOLID,
        FailureCode.MANUFACTURING_RULE_VIOLATED,
    ],
)
def test_an_infeasible_design_is_reported_as_itself_and_not_retried(
    code: FailureCode, tmp_path: Path
) -> None:
    failure, attempts = _mesh(code, tmp_path)
    assert failure.code is code, (
        f"{code.value} was relabelled {failure.code.value}; the optimiser would "
        f"treat a bad design as an infrastructure error and learn nothing"
    )
    assert failure.outcome is Outcome.INFEASIBLE
    assert attempts == 1, "a coarser mesh cannot fix a design that is simply bad"


@pytest.mark.parametrize("code", [FailureCode.REGION_NOT_FOUND, FailureCode.REGION_AMBIGUOUS])
def test_a_selector_problem_is_reported_as_itself_and_not_retried(
    code: FailureCode, tmp_path: Path
) -> None:
    # These stay infrastructure errors -- a selector reads the same on every
    # attempt -- but they are equally pointless to retry.
    failure, attempts = _mesh(code, tmp_path)
    assert failure.code is code
    assert failure.outcome is Outcome.ERROR
    assert attempts == 1


def test_a_real_meshing_problem_still_walks_the_whole_retry_ladder(tmp_path: Path) -> None:
    failure, attempts = _mesh(FailureCode.MESH_GENERATION_FAILED, tmp_path)
    assert failure.code is FailureCode.MESH_GENERATION_FAILED
    assert attempts > 1, "coarser settings are exactly what this ladder is for"
    assert "every meshing attempt failed" in failure.message


def test_every_infeasible_code_is_covered_by_the_rule() -> None:
    """The point of asking the taxonomy instead of listing codes by name.

    A future infeasible code needs no change here. If this assertion is ever
    weakened, the defect above comes straight back.
    """
    from openoptima.domain.failures import INFEASIBLE_CODES

    assert all(outcome_for(code) is Outcome.INFEASIBLE for code in INFEASIBLE_CODES)
