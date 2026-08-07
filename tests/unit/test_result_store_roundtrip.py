"""A stored result must come back the way it went in.

The database is the index, not the archive: the meshes and solver files stay on
disk. But everything the database *does* hold has to survive the round trip. A
field that is written and never read back is worse than one that was never
stored, because the caller cannot tell the difference between "this design had
no mesh" and "the mesh was dropped on the way out".

These need no CAE tool.
"""

from __future__ import annotations

import pytest

from openoptima.domain.failures import EvaluationState, Outcome
from openoptima.domain.results import EvaluationResult, LoadCaseResult, MeshSummary
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.storage.database import ResultStore


@pytest.fixture
def space() -> DesignSpace:
    return DesignSpace((DesignVariable(id="thickness", minimum=5.0, maximum=20.0, default=10.0),))


@pytest.fixture
def result(space: DesignSpace) -> EvaluationResult:
    return EvaluationResult(
        design=space.defaults(),
        outcome=Outcome.OK,
        state=EvaluationState.ACCEPTED,
        metrics={"mass_kg": 0.4826, "displacement_max_mm": 0.31},
        mesh=MeshSummary(
            node_count=12_345,
            element_count=6_789,
            element_type="C3D10",
            min_scaled_jacobian=0.283,
            mesh_volume=119_551.7,
            cad_volume=119_550.0,
            volume_error=1.4e-5,
            algorithm="delaunay",
            attempt=2,
        ),
        load_cases=(
            LoadCaseResult(
                load_case_id="tip_load",
                displacement_max=0.31,
                displacement_node=42,
                stress_measure=80.1,
                stress_raw_max=143.7,
                stress_measure_name="p99 percentile",
                reaction_force=(0.0, 0.0, 2500.0),
                buckling_factor=3.4,
                buckling_modes=(3.4, 3.5, 9.1),
            ),
        ),
        evaluation_hash="abc123",
        run_id="000031",
        wall_time=12.5,
    )


def test_a_cached_result_keeps_its_mesh_summary(tmp_path, space, result):
    """Regression test. The mesh summary was written and never read back.

    The mesh convergence command found this. On its first run every level
    meshed and solved correctly. On the second run every level was served from
    the cache, came back with ``mesh`` set to None, and the study reported
    "not enough data" -- while looking exactly like a study that had worked.
    The element counts and the achieved element size had been silently dropped
    between the two runs.
    """
    with ResultStore(tmp_path / "store.sqlite") as store:
        store.record(result, setup_digest="digest", study="s")
        restored = store.cached_result("abc123", space)

    assert restored is not None
    assert restored.mesh is not None, "the mesh summary was dropped on the way out"
    assert restored.mesh.element_count == 6_789
    assert restored.mesh.node_count == 12_345
    assert restored.mesh.element_type == "C3D10"
    assert restored.mesh.mesh_volume == pytest.approx(119_551.7)
    assert restored.mesh.min_scaled_jacobian == pytest.approx(0.283)
    assert restored.mesh.attempt == 2


def test_a_cached_result_keeps_its_per_load_case_numbers(tmp_path, space, result):
    """Per-case values are how a reader checks which case governs.

    Losing them on the way out of the cache would leave only the envelope, and
    the envelope alone cannot show which load case produced it.
    """
    with ResultStore(tmp_path / "store.sqlite") as store:
        store.record(result, setup_digest="digest", study="s")
        restored = store.cached_result("abc123", space)

    assert restored is not None
    assert len(restored.load_cases) == 1
    case = restored.load_cases[0]
    assert case.load_case_id == "tip_load"
    assert case.displacement_max == pytest.approx(0.31)
    assert case.stress_measure == pytest.approx(80.1)
    assert case.stress_raw_max == pytest.approx(143.7)
    assert case.reaction_force == pytest.approx((0.0, 0.0, 2500.0))
    assert case.buckling_factor == pytest.approx(3.4)
    assert case.buckling_modes == pytest.approx((3.4, 3.5, 9.1))


def test_a_result_with_no_mesh_still_round_trips_as_no_mesh(tmp_path, space):
    """A design that failed before meshing genuinely has no mesh.

    That must stay distinguishable from a mesh that was dropped in transit.
    """
    failed = EvaluationResult(
        design=space.defaults(),
        outcome=Outcome.INFEASIBLE,
        state=EvaluationState.GEOMETRY_GENERATED,
        metrics={},
        evaluation_hash="nomesh",
        message="geometry self-intersects",
    )
    with ResultStore(tmp_path / "store.sqlite") as store:
        store.record(failed, setup_digest="digest")
        restored = store.cached_result("nomesh", space)

    assert restored is not None
    assert restored.mesh is None
    assert restored.load_cases == ()


def test_an_infeasible_result_is_cached_but_an_error_is_not(tmp_path, space, result):
    """A bad design is a real answer. An infrastructure failure is not.

    Replaying a stored solver crash would make a transient failure permanent.
    """
    import dataclasses

    infeasible = dataclasses.replace(
        result, outcome=Outcome.INFEASIBLE, evaluation_hash="infeasible"
    )
    errored = dataclasses.replace(result, outcome=Outcome.ERROR, evaluation_hash="errored")

    with ResultStore(tmp_path / "store.sqlite") as store:
        store.record(infeasible, setup_digest="digest")
        store.record(errored, setup_digest="digest")

        assert store.cached_result("infeasible", space) is not None
        assert store.cached_result("errored", space) is None
