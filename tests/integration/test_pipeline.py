"""End-to-end pipeline behaviour: caching, classification, persistence."""

from __future__ import annotations

import pytest

from openoptima.domain.failures import Outcome
from openoptima.evaluation.evaluator import Evaluator
from openoptima.storage.database import ResultStore

from ..conftest import requires_calculix, requires_gmsh

pytestmark = [requires_gmsh, requires_calculix, pytest.mark.slow]


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    from openoptima.schema.loader import load_project

    from ..conftest import EXAMPLES

    project = load_project(EXAMPLES / "l_bracket" / "project.yaml")
    workspace = tmp_path_factory.mktemp("pipeline")
    evaluator = Evaluator(project, workspace, study="test", project_root=EXAMPLES)
    design = project.design_space.decode(
        {"thickness_h": 18.0, "thickness_v": 14.0, "fillet_radius": 18.0}
    )
    result = evaluator.evaluate(design)
    yield project, evaluator, design, result
    evaluator.close()


class TestSingleEvaluation:
    def test_produces_physical_metrics(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        assert result.outcome is not Outcome.ERROR, result.message
        assert result.metrics["mass_kg"] > 0
        assert result.metrics["displacement_max_mm"] > 0
        assert result.metrics["stress_max_mpa"] > 0

    def test_reaction_balances_the_applied_load(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        reaction = result.load_cases[0].reaction_force
        assert reaction[2] == pytest.approx(2500.0, rel=1e-3)

    def test_no_equilibrium_warning_is_raised(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        assert not any("not in equilibrium" in w for w in result.warnings)

    def test_raw_peak_stress_is_at_least_the_reported_measure(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        assert result.metrics["stress_raw_max_mpa"] >= result.metrics["stress_max_mpa"]

    def test_second_order_mesh_was_used(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        assert result.mesh is not None
        assert result.mesh.element_type == "C3D10"
        assert result.mesh.volume_error < 0.02

    def test_run_artifacts_and_manifest_are_written(self, evaluated):
        from pathlib import Path

        _project, _evaluator, _design, result = evaluated
        directory = Path(result.run_directory)
        assert (directory / "evaluation_manifest.json").exists()
        assert (directory / "results" / "metrics.json").exists()
        assert (directory / "mesh" / "regions.json").exists()

    def test_provenance_records_the_tool_versions(self, evaluated):
        _project, _evaluator, _design, result = evaluated
        assert result.provenance.get("gmsh")
        assert result.provenance.get("calculix")


class TestCaching:
    def test_repeating_a_design_hits_the_cache(self, evaluated):
        _project, evaluator, design, first = evaluated
        second = evaluator.evaluate(design)
        assert second.from_cache
        assert second.metrics["mass_kg"] == pytest.approx(first.metrics["mass_kg"])

    def test_no_cache_forces_a_fresh_run(self, evaluated):
        _project, evaluator, design, _first = evaluated
        fresh = evaluator.evaluate(design, use_cache=False)
        assert not fresh.from_cache

    def test_changing_the_material_invalidates_the_cache(self, evaluated):
        """A number computed under different physics is not a cache hit."""
        import dataclasses

        project, evaluator, design, _first = evaluated
        softer = dataclasses.replace(
            project,
            material=dataclasses.replace(project.material, elastic_modulus=35000.0),
        )
        assert softer.setup_digest() != project.setup_digest()

        other = Evaluator(
            softer, evaluator.workspace, study="test2", project_root=evaluator.project_root
        )
        try:
            assert other.hash_for(design) != evaluator.hash_for(design)
            assert other.store.cached_result(other.hash_for(design), softer.design_space) is None
        finally:
            other.close()

    def test_results_are_persisted_and_reloadable(self, evaluated):
        _project, evaluator, _design, _result = evaluated
        with ResultStore(evaluator.workspace / "openoptima.sqlite") as store:
            records = store.evaluations()
            assert records
            assert any(r["outcome"] in ("ok", "infeasible") for r in records)


class TestInfeasibleClassification:
    def test_a_too_thin_design_is_infeasible_not_an_error(self, evaluated):
        project, evaluator, _design, _result = evaluated
        weak = project.design_space.decode(
            {"thickness_h": 5.0, "thickness_v": 5.0, "fillet_radius": 3.0}
        )
        result = evaluator.evaluate(weak)
        assert result.outcome is Outcome.INFEASIBLE
        assert result.constraint_violations
        assert result.total_violation > 0

    def test_an_impossible_geometry_is_infeasible_not_an_error(self, evaluated):
        project, evaluator, _design, _result = evaluated
        impossible = project.design_space.decode(
            {"thickness_h": 5.0, "thickness_v": 5.0, "fillet_radius": 25.0}
        )
        result = evaluator.evaluate(impossible)
        assert result.outcome is Outcome.INFEASIBLE, (
            "a fillet that will not fit is a fact about the design; the optimiser "
            "must learn it rather than see an infrastructure error"
        )

    def test_infeasible_designs_still_record_a_run_directory(self, evaluated):
        project, evaluator, _design, _result = evaluated
        weak = project.design_space.decode(
            {"thickness_h": 5.5, "thickness_v": 5.5, "fillet_radius": 3.0}
        )
        result = evaluator.evaluate(weak)
        assert result.run_directory


class TestBatchEvaluation:
    def test_batch_returns_one_result_per_design(self, evaluated):
        project, evaluator, _design, _result = evaluated
        designs = [
            project.design_space.decode(
                {"thickness_h": t, "thickness_v": 12.0, "fillet_radius": 10.0}
            )
            for t in (14.0, 16.0)
        ]
        results = evaluator.evaluate_many(designs, jobs=2)
        assert len(results) == 2
        assert all(r.outcome is not Outcome.ERROR for r in results), [r.message for r in results]

    def test_mass_increases_with_thickness(self, evaluated):
        project, evaluator, _design, _result = evaluated
        light, heavy = evaluator.evaluate_many(
            [
                project.design_space.decode(
                    {"thickness_h": t, "thickness_v": 10.0, "fillet_radius": 8.0}
                )
                for t in (8.0, 19.0)
            ],
            jobs=1,
        )
        assert heavy.metrics["mass_kg"] > light.metrics["mass_kg"]

    def test_stiffness_increases_with_thickness(self, evaluated):
        """A physical sanity check on the optimisation landscape itself."""
        project, evaluator, _design, _result = evaluated
        thin, thick = evaluator.evaluate_many(
            [
                project.design_space.decode(
                    {"thickness_h": t, "thickness_v": 12.0, "fillet_radius": 10.0}
                )
                for t in (8.0, 18.0)
            ],
            jobs=1,
        )
        assert thick.metrics["displacement_max_mm"] < thin.metrics["displacement_max_mm"]
