"""The written report.

Nothing here checks prose style. What is pinned is that the report renders at
all -- it is the artefact a user actually reads, and a crash in it would only
show up after a study has finished, which is the most expensive moment to find
one -- and that it says plainly when a winning size ran into the limit it was
given rather than finding an optimum.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openoptima.domain.results import EvaluationResult, EvaluationState, Outcome
from openoptima.optimisation.study import StudyResult
from openoptima.reporting.report import build_report, write_report
from openoptima.schema.loader import load_project

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="module")
def project():
    return load_project(EXAMPLES / "l_bracket" / "project.yaml")


def result(project, run_id: str, design: dict, **metrics) -> EvaluationResult:
    values = {
        "mass_kg": 0.5,
        "displacement_max_mm": 0.6,
        "stress_max_mpa": 70.0,
        "factor_of_safety": 2.3,
    }
    values.update(metrics)
    return EvaluationResult(
        design=project.design_space.decode(design),
        outcome=Outcome.OK,
        state=EvaluationState.ACCEPTED,
        metrics=values,
        run_id=run_id,
    )


def study(project, results: list[EvaluationResult]) -> StudyResult:
    return StudyResult(name="test", kind="optimise", results=results, front=results, wall_time=1.0)


def middle_design(project) -> dict:
    """A design with nothing sitting on a limit."""
    return {
        v.id: 0.5 * (v.minimum + v.maximum)
        for v in project.design_space  # type: ignore[operator]
    }


class TestItRenders:
    def test_a_report_is_produced_without_crashing(self, project):
        text = build_report(
            study(project, [result(project, "r1", middle_design(project))]), project
        )
        assert "Run summary" in text

    def test_it_is_written_to_disk(self, project, tmp_path):
        path = write_report(
            study(project, [result(project, "r1", middle_design(project))]),
            project,
            tmp_path / "report.md",
        )
        assert path.read_text(encoding="utf-8").startswith("#")


class TestPinnedValuesAreCalledOut:
    """A size on its limit was chosen by the limit, not by the search.

    Minimising mass always pushes an internal fillet towards the smallest
    radius allowed, because a bigger fillet is more material. The reader has to
    be told that, or they will read "3 mm" as an engineering conclusion when it
    is really a restatement of their own input.
    """

    def _report_with_fillet_at_its_minimum(self, project):
        design = middle_design(project)
        design["fillet_radius"] = project.design_space["fillet_radius"].minimum
        return build_report(study(project, [result(project, "r1", design)]), project)

    def test_the_value_is_marked(self, project):
        assert "at its minimum" in self._report_with_fillet_at_its_minimum(project)

    def test_the_reader_is_told_what_it_means(self, project):
        text = self._report_with_fillet_at_its_minimum(project)
        assert "sitting on the limits you set" in text
        assert "smallest value allowed" in text
        assert "Widen the range" in text

    def test_nothing_is_said_when_no_value_is_on_a_limit(self, project):
        text = build_report(
            study(project, [result(project, "r1", middle_design(project))]), project
        )
        assert "sitting on the limits you set" not in text
        assert "at its minimum" not in text
