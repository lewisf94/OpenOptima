"""Load cases are enveloped, never averaged.

This is one of the invariants in ``AGENTS.md``, and until now nothing tested
it. Averaging a failing case against a passing one hides the failure: a part
that survives cornering and folds under braking would be reported as fine.

The numbers below are chosen so that the average and the worst case are far
apart, and so that a mean would land in the *safe* range while the truth is
unsafe. A test where both answers looked similar would prove nothing.

These need no CAE tool.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    BucklingSettings,
    Load,
    LoadCase,
    LoadKind,
    Material,
    StressEvaluation,
)
from openoptima.domain.regions import RegionMap
from openoptima.meshing.base import MeshData
from openoptima.results.metrics import collect_metrics
from openoptima.solvers.base import AnalysisResults, LoadCaseFields

ALLOWABLE = 200.0
#: One gentle case and one severe one. The mean of 60 and 180 is 120 MPa,
#: which passes against a 200 MPa allowable; the worst case does not clear it
#: nearly as well. If enveloping ever regressed to averaging, the reported
#: factor of safety would jump from 1.11 to 1.67.
GENTLE_STRESS, SEVERE_STRESS = 60.0, 180.0
GENTLE_DISPLACEMENT, SEVERE_DISPLACEMENT = 0.10, 0.90


def _mesh() -> MeshData:
    """A single tetrahedron. Nothing here reads the geometry."""
    return MeshData(
        node_tags=np.array([1, 2, 3, 4]),
        coordinates=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        element_tags=np.array([1]),
        connectivity=np.array([[1, 2, 3, 4]]),
        element_type="C3D4",
        surface_nodes={},
        surface_triangles={},
    )


def _fields(
    load_case_id: str,
    stress: float,
    displacement: float,
    reaction: tuple[float, float, float],
    buckling: tuple[float, ...] = (),
) -> LoadCaseFields:
    return LoadCaseFields(
        load_case_id=load_case_id,
        node_tags=np.array([1, 2, 3, 4]),
        displacement=np.tile(np.array([0.0, 0.0, displacement]), (4, 1)),
        von_mises=np.full(4, stress),
        reaction_force=reaction,
        buckling_factors=buckling,
    )


def _model(buckling: bool = False) -> AnalysisModel:
    def case(identifier: str) -> LoadCase:
        return LoadCase(
            id=identifier,
            boundary_conditions=(BoundaryCondition(region="base"),),
            loads=(Load(kind=LoadKind.FORCE, region="tip", vector=(0.0, 0.0, -1000.0)),),
        )

    return AnalysisModel(
        name="two cases",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=70000.0,
            poisson_ratio=0.33,
            density_kg_m3=2700.0,
            allowable_stress_mpa=ALLOWABLE,
        ),
        load_cases=(case("cornering"), case("braking")),
        stress_evaluation=StressEvaluation(measure="raw_max"),
        buckling=BucklingSettings(enabled=buckling, modes=2),
    )


def _collect(buckling_gentle=(), buckling_severe=(), enable_buckling=False):
    results = AnalysisResults(
        load_cases=(
            _fields(
                "cornering", GENTLE_STRESS, GENTLE_DISPLACEMENT, (0.0, 0.0, 500.0), buckling_gentle
            ),
            _fields(
                "braking", SEVERE_STRESS, SEVERE_DISPLACEMENT, (0.0, 0.0, 1500.0), buckling_severe
            ),
        ),
        solver_name="stub",
    )
    return collect_metrics(results, _model(enable_buckling), _mesh(), RegionMap({}), 1000.0)


def test_stress_is_the_worst_case_not_the_mean():
    metrics, _cases, _warnings = _collect()

    assert metrics["stress_max_mpa"] == pytest.approx(SEVERE_STRESS)
    mean = 0.5 * (GENTLE_STRESS + SEVERE_STRESS)
    assert metrics["stress_max_mpa"] != pytest.approx(mean), (
        "averaging load cases hides the failing one"
    )


def test_factor_of_safety_comes_from_the_worst_case():
    """The number an engineer sizes from. Averaging inflates it by half."""
    metrics, _cases, _warnings = _collect()

    assert metrics["factor_of_safety"] == pytest.approx(ALLOWABLE / SEVERE_STRESS)
    assert metrics["factor_of_safety"] == pytest.approx(1.111, abs=1e-3)

    averaged = ALLOWABLE / (0.5 * (GENTLE_STRESS + SEVERE_STRESS))
    assert averaged == pytest.approx(1.667, abs=1e-3)
    assert metrics["factor_of_safety"] < averaged


def test_displacement_is_the_worst_case_not_the_mean():
    metrics, _cases, _warnings = _collect()

    assert metrics["displacement_max_mm"] == pytest.approx(SEVERE_DISPLACEMENT)
    assert metrics["displacement_max_mm"] != pytest.approx(
        0.5 * (GENTLE_DISPLACEMENT + SEVERE_DISPLACEMENT)
    )


def test_every_case_is_also_reported_on_its_own():
    """The envelope alone cannot show which case governs.

    Without the per-case values a reader sees only that something failed, not
    which scenario caused it.
    """
    metrics, _cases, _warnings = _collect()

    assert metrics["stress_max_mpa.cornering"] == pytest.approx(GENTLE_STRESS)
    assert metrics["stress_max_mpa.braking"] == pytest.approx(SEVERE_STRESS)
    assert metrics["displacement_max_mm.cornering"] == pytest.approx(GENTLE_DISPLACEMENT)
    assert metrics["displacement_max_mm.braking"] == pytest.approx(SEVERE_DISPLACEMENT)
    assert metrics["factor_of_safety.braking"] == pytest.approx(ALLOWABLE / SEVERE_STRESS)


def test_the_governing_case_is_identified_by_name():
    _metrics, cases, _warnings = _collect()

    governing = max(cases, key=lambda case: case.stress_measure)
    assert governing.load_case_id == "braking"


def test_adding_a_gentler_case_cannot_improve_the_reported_result():
    """The property that makes enveloping safe.

    Adding scenarios may only ever make the reported result worse or leave it
    unchanged. If a mild extra case could raise the factor of safety, a user
    could make a part look better by analysing it more.
    """
    two_cases, _c, _w = _collect()

    single = AnalysisResults(
        load_cases=(_fields("braking", SEVERE_STRESS, SEVERE_DISPLACEMENT, (0.0, 0.0, 1500.0)),),
        solver_name="stub",
    )
    model = _model()
    only_severe, _c2, _w2 = collect_metrics(
        single,
        AnalysisModel(
            name=model.name,
            material=model.material,
            load_cases=(model.load_cases[1],),
            stress_evaluation=model.stress_evaluation,
        ),
        _mesh(),
        RegionMap({}),
        1000.0,
    )

    assert two_cases["stress_max_mpa"] == pytest.approx(only_severe["stress_max_mpa"])
    assert two_cases["factor_of_safety"] == pytest.approx(only_severe["factor_of_safety"])


def test_buckling_takes_the_lowest_factor_across_cases():
    """Buckling envelopes downwards: the smallest factor is the dangerous one."""
    metrics, _cases, _warnings = _collect(
        buckling_gentle=(8.0, 9.0), buckling_severe=(1.4, 12.0), enable_buckling=False
    )

    assert metrics["buckling_factor"] == pytest.approx(1.4)
    assert metrics["buckling_factor.cornering"] == pytest.approx(8.0)
    assert metrics["buckling_factor.braking"] == pytest.approx(1.4)


def test_a_case_that_does_not_buckle_is_excluded_not_counted_as_zero():
    """A purely tensile case has no positive factor.

    Counting it as zero would make the envelope zero and report a safe design
    as buckling instantly.
    """
    metrics, _cases, _warnings = _collect(
        buckling_gentle=(-3.0, -5.0), buckling_severe=(2.5, 20.0), enable_buckling=False
    )

    assert metrics["buckling_factor"] == pytest.approx(2.5)


def test_stiffness_uses_the_worst_case_pair():
    """Stiffness is reported from the governing load and displacement, so it
    cannot be flattered by a gentle case."""
    metrics, _cases, _warnings = _collect()

    assert metrics["stiffness_n_per_mm"] == pytest.approx(1500.0 / SEVERE_DISPLACEMENT)
