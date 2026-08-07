"""The convergence study driver: mesh level construction and cache safety.

These run without gmsh or a solver. The end-to-end behaviour is covered by
``tests/verification/test_mesh_convergence.py``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openoptima.convergence.study import (
    DEFAULT_METRICS,
    LevelOutcome,
    MeshLevel,
    assess_metrics,
    element_growth,
    mesh_levels,
    scaled_mesh,
)
from openoptima.domain.convergence import Behaviour
from openoptima.domain.failures import EvaluationState, Outcome
from openoptima.domain.model import LocalRefinement, MeshSpecification
from openoptima.domain.project import Project
from openoptima.domain.results import EvaluationResult, MeshSummary
from openoptima.schema.loader import load_project

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "l_bracket" / "project.yaml"


def make_project(global_size: float = 6.0, minimum_size: float = 1.5) -> Project:
    """The L-bracket example, with its mesh sizes overridden.

    Loading the real example keeps these tests honest about the shape of a
    project. Parsing it needs no CAE tool.
    """
    project = load_project(EXAMPLE)
    return dataclasses.replace(
        project,
        mesh=project.mesh.with_overrides(
            global_size=global_size, minimum_size=minimum_size, local_refinements=()
        ),
    )


# ---------------------------------------------------------------------------
# mesh level construction
# ---------------------------------------------------------------------------


def test_levels_start_at_the_projects_own_mesh_and_get_finer():
    project = make_project(global_size=6.0, minimum_size=1.5)

    levels = mesh_levels(project, count=4, ratio=1.5)

    assert [level.label for level in levels] == ["L0", "L1", "L2", "L3"]
    assert levels[0].scale == 1.0
    assert levels[0].project.mesh.global_size == pytest.approx(6.0)
    assert levels[1].project.mesh.global_size == pytest.approx(4.0)
    assert levels[2].project.mesh.global_size == pytest.approx(6.0 / 1.5**2)
    assert levels[3].project.mesh.global_size == pytest.approx(6.0 / 1.5**3)


def test_the_first_level_is_the_users_own_mesh_untouched():
    """L0 must be the project's exact mesh, so its result is cache-shared with
    an ordinary evaluate or study run rather than recomputed."""
    project = make_project(global_size=6.0, minimum_size=1.5)

    levels = mesh_levels(project, count=3, ratio=1.5)

    assert levels[0].project.setup_digest() == project.setup_digest()


def test_minimum_size_scales_with_global_size():
    """Holding the floor fixed while the global size shrinks would stop the
    refinement being uniform, and the comparison would mean nothing."""
    project = make_project(global_size=6.0, minimum_size=1.5)

    levels = mesh_levels(project, count=3, ratio=2.0)

    for level in levels:
        ratio = level.project.mesh.global_size / level.project.mesh.minimum_size
        assert ratio == pytest.approx(4.0), "the size ratio must be preserved at every level"


def test_local_refinement_size_scales_but_its_reach_does_not():
    """A local refinement carries an absolute element size in mm.

    Leaving it fixed while the global size shrinks would refine part of the
    model and not the rest, so the meshes would not be comparable. Its
    ``distance`` is different: that sets how far the fine zone reaches, which
    is a decision about the model, not about mesh density. Scaling it would
    move the boundary of the fine zone between levels and change what is being
    compared.
    """
    mesh = MeshSpecification(
        global_size=6.0,
        minimum_size=1.5,
        local_refinements=(LocalRefinement(region="fillet", size=1.0, distance=5.0),),
    )

    refined = scaled_mesh(mesh, 0.5)

    assert refined.global_size == pytest.approx(3.0)
    assert refined.local_refinements[0].size == pytest.approx(0.5)
    assert refined.local_refinements[0].distance == pytest.approx(5.0)
    assert refined.local_refinements[0].region == "fillet"


def test_every_level_has_its_own_cache_identity():
    """The property that stops a convergence run corrupting a study.

    Each level is a different mesh, so each must hash differently. If two
    levels shared a setup digest, the second would be served the first's
    cached result and the whole study would compare a mesh against itself.
    Worse, a convergence run could overwrite the cache entry belonging to the
    study it was checking.
    """
    project = make_project(global_size=6.0, minimum_size=1.5)

    levels = mesh_levels(project, count=5, ratio=1.5)
    digests = [level.project.setup_digest() for level in levels]

    assert len(set(digests)) == len(digests), f"levels share a cache identity: {digests}"


def test_levels_differ_only_in_the_mesh():
    """Nothing but the mesh may change between levels.

    A convergence study compares one design under one set of physics at
    several mesh densities. If anything else moved, the comparison would be
    measuring the wrong thing.
    """
    project = make_project(global_size=6.0, minimum_size=1.5)

    levels = mesh_levels(project, count=3, ratio=1.5)

    for level in levels[1:]:
        stripped = dataclasses.replace(level.project, mesh=project.mesh)
        assert stripped.setup_digest() == project.setup_digest()


def test_too_few_levels_is_refused():
    project = make_project()
    with pytest.raises(ValueError, match="at least three"):
        mesh_levels(project, count=2)


def test_a_refinement_step_too_small_to_measure_is_refused():
    """Two meshes that are nearly the same size differ only by noise, and the
    rate of settling calculated from them is meaningless."""
    project = make_project()
    with pytest.raises(ValueError, match="too small"):
        mesh_levels(project, ratio=1.02)


def test_element_growth_warns_about_the_cost_in_three_dimensions():
    """Halving the element size multiplies the element count by about eight.

    The caller uses this to warn before a long run, not after.
    """
    project = make_project()
    levels = mesh_levels(project, count=3, ratio=2.0)

    # scales are 1, 1/2, 1/4 -> element counts scale as 1, 8, 64
    assert element_growth(levels) == pytest.approx(1 + 8 + 64)


# ---------------------------------------------------------------------------
# assessment over levels
# ---------------------------------------------------------------------------


def _outcome(
    label: str,
    scale: float,
    size: float,
    elements: int,
    metrics: dict[str, float],
    outcome: Outcome = Outcome.OK,
):
    project = make_project()
    result = EvaluationResult(
        design=project.design_space.defaults(),
        outcome=outcome,
        state=EvaluationState.ACCEPTED,
        metrics=metrics,
        mesh=MeshSummary(
            node_count=elements * 2,
            element_count=elements,
            element_type="C3D10",
            min_scaled_jacobian=0.3,
            # chosen so representative_size comes out at exactly `size`
            mesh_volume=size**3 * elements,
            cad_volume=size**3 * elements,
            volume_error=0.0,
            algorithm="delaunay",
        ),
    )
    return LevelOutcome(level=MeshLevel(label=label, scale=scale, project=project), result=result)


def test_achieved_size_comes_from_the_mesh_not_the_request():
    outcome = _outcome("L0", 1.0, size=2.0, elements=1000, metrics={"mass_kg": 0.5})
    assert outcome.achieved_size == pytest.approx(2.0)


def test_assessment_uses_the_size_produced_not_the_size_requested():
    """The mesher clamps and curvature-refines, so the size it delivers is not
    the size it was asked for. Using the request would give a wrong refinement
    ratio and therefore a wrong rate of settling."""
    exact = 10.0
    outcomes = [
        _outcome("L0", 1.0, size=4.0, elements=100, metrics={"displacement_max_mm": exact + 16.0}),
        _outcome("L1", 0.5, size=2.0, elements=800, metrics={"displacement_max_mm": exact + 4.0}),
        _outcome("L2", 0.25, size=1.0, elements=6400, metrics={"displacement_max_mm": exact + 1.0}),
    ]

    assessed = assess_metrics(outcomes, ("displacement_max_mm",))

    result = assessed["displacement_max_mm"]
    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(2.0, rel=1e-9)
    assert result.extrapolated == pytest.approx(exact, rel=1e-9)


def test_an_infeasible_design_is_still_a_usable_convergence_data_point():
    """Regression test. A design that breaks its own limits analysed fine.

    The first version of this command treated anything that was not OK as a
    failed level. Run against the L-bracket example it reported "0 of 4 meshes
    succeeded", when in fact all four had meshed and solved correctly and their
    numbers were visibly converged. The default L-bracket design breaks its own
    constraints -- it is an optimisation starting point, not a good design --
    so every level came back INFEASIBLE and was thrown away.

    Whether a design passes its constraints has nothing to do with whether its
    numbers have settled. Conflating the two also breaks the rule in AGENTS.md
    that a bad design and a broken run are different things. Only an ERROR
    means there is nothing to compare.
    """
    exact = 10.0
    outcomes = [
        _outcome(
            "L0",
            1.0,
            size=4.0,
            elements=100,
            metrics={"displacement_max_mm": exact + 16.0},
            outcome=Outcome.INFEASIBLE,
        ),
        _outcome(
            "L1",
            0.5,
            size=2.0,
            elements=800,
            metrics={"displacement_max_mm": exact + 4.0},
            outcome=Outcome.INFEASIBLE,
        ),
        _outcome(
            "L2",
            0.25,
            size=1.0,
            elements=6400,
            metrics={"displacement_max_mm": exact + 1.0},
            outcome=Outcome.INFEASIBLE,
        ),
    ]

    assert all(outcome.usable for outcome in outcomes)
    assert all(outcome.infeasible for outcome in outcomes)

    assessed = assess_metrics(outcomes, ("displacement_max_mm",))

    result = assessed["displacement_max_mm"]
    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(2.0, rel=1e-9)
    assert result.extrapolated == pytest.approx(exact, rel=1e-9)


def test_an_error_level_is_not_usable_even_though_infeasible_ones_are():
    """The other side of the same distinction: a real failure has no numbers."""
    broken = _outcome("L0", 1.0, size=4.0, elements=100, metrics={}, outcome=Outcome.ERROR)
    assert not broken.usable
    assert not broken.infeasible


def test_a_failed_level_is_dropped_and_does_not_fake_a_third_point():
    """Two good meshes plus one failure is two meshes, not three.

    Silently treating a failure as a data point would produce a confident
    convergence claim from insufficient evidence.
    """
    outcomes = [
        _outcome("L0", 1.0, size=4.0, elements=100, metrics={"displacement_max_mm": 26.0}),
        _outcome("L1", 0.5, size=2.0, elements=800, metrics={"displacement_max_mm": 14.0}),
    ]
    failed = LevelOutcome(
        level=MeshLevel(label="L2", scale=0.25, project=make_project()),
        result=None,
        error="mesh exceeded the element limit",
    )
    outcomes.append(failed)

    assert not failed.usable
    assessed = assess_metrics(outcomes, ("displacement_max_mm",))

    assert assessed["displacement_max_mm"].behaviour is Behaviour.NOT_ENOUGH_DATA


def test_a_metric_missing_from_some_levels_is_skipped_not_guessed():
    outcomes = [
        _outcome("L0", 1.0, size=4.0, elements=100, metrics={"mass_kg": 0.5}),
        _outcome("L1", 0.5, size=2.0, elements=800, metrics={"mass_kg": 0.5}),
        _outcome("L2", 0.25, size=1.0, elements=6400, metrics={"mass_kg": 0.5}),
    ]

    assessed = assess_metrics(outcomes, DEFAULT_METRICS)

    assert "mass_kg" in assessed
    assert "buckling_factor" not in assessed, "a metric nobody produced must not appear"


def test_mass_is_assessed_first_because_it_is_the_control():
    """Mass depends on the shape, not the analysis, so it should barely move.

    If it does move, the geometry was not rebuilt identically and nothing else
    in the report can be trusted. It is listed first so a reader sees it before
    the numbers that depend on it being right.
    """
    assert DEFAULT_METRICS[0] == "mass_kg"
