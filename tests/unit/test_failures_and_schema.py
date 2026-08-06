"""The failure taxonomy, units, and the project schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from openoptima.domain.failures import (
    INFEASIBLE_CODES,
    EvaluationFailure,
    FailureCode,
    Outcome,
    is_retryable,
    outcome_for,
)
from openoptima.domain.model import Material
from openoptima.domain.units import density_kg_m3_to_internal, get_unit_system
from openoptima.schema.loader import ProjectLoadError, load_project, load_project_dict

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "l_bracket" / "project.yaml"


class TestFailureTaxonomy:
    """The distinction the whole optimisation loop depends on."""

    def test_a_bad_design_is_infeasible(self):
        assert outcome_for(FailureCode.INVALID_SOLID) is Outcome.INFEASIBLE
        assert outcome_for(FailureCode.MANUFACTURING_RULE_VIOLATED) is Outcome.INFEASIBLE
        assert outcome_for(FailureCode.ENGINEERING_CONSTRAINT_FAILED) is Outcome.INFEASIBLE

    def test_our_own_failure_is_an_error_not_a_bad_design(self):
        for code in (
            FailureCode.SOLVER_CRASH,
            FailureCode.SOLVER_TIMEOUT,
            FailureCode.MESH_GENERATION_FAILED,
            FailureCode.WORKER_CRASH,
            FailureCode.REGION_AMBIGUOUS,
        ):
            assert outcome_for(code) is Outcome.ERROR, (
                f"{code.value} says nothing about the design and must never be "
                f"reported to the optimiser as a poor result"
            )

    def test_no_code_means_success(self):
        assert outcome_for(None) is Outcome.OK

    def test_only_transient_errors_are_retried(self):
        assert is_retryable(FailureCode.SOLVER_TIMEOUT)
        assert is_retryable(FailureCode.WORKER_CRASH)
        # Retrying these would just burn the budget: they are deterministic.
        assert not is_retryable(FailureCode.REGION_AMBIGUOUS)
        assert not is_retryable(FailureCode.INVALID_SOLID)
        assert not is_retryable(None)

    def test_infeasible_codes_are_never_retryable(self):
        for code in INFEASIBLE_CODES:
            assert not is_retryable(code)

    def test_exception_carries_its_classification(self):
        failure = EvaluationFailure(FailureCode.INVALID_SOLID, "wall went to zero")
        assert failure.outcome is Outcome.INFEASIBLE
        assert "invalid_solid" in str(failure)


class TestUnits:
    def test_density_converts_to_the_internal_system(self):
        assert density_kg_m3_to_internal(2700.0) == pytest.approx(2.7e-9)

    def test_steel_mass_of_a_known_volume(self):
        """A 100 mm cube of steel is 7.85 kg."""
        material = Material.from_engineering_units(
            name="steel",
            elastic_modulus_mpa=210000,
            poisson_ratio=0.3,
            density_kg_m3=7850,
            allowable_stress_mpa=200,
        )
        from openoptima.results.metrics import mass_kg

        assert mass_kg(100.0**3, material) == pytest.approx(7.85, rel=1e-9)

    def test_unknown_unit_system_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported unit system"):
            get_unit_system("imperial_slugs")


class TestMaterial:
    def test_rejects_impossible_poisson_ratio(self):
        with pytest.raises(ValueError, match="Poisson"):
            Material("x", 210000, 0.7, 7.85e-9, 200)

    def test_rejects_negative_modulus(self):
        with pytest.raises(ValueError, match="elastic modulus"):
            Material("x", -1, 0.3, 7.85e-9, 200)

    def test_allowable_stress_is_required_and_positive(self):
        with pytest.raises(ValueError, match="allowable stress"):
            Material("x", 210000, 0.3, 7.85e-9, 0)


class TestProjectSchema:
    def test_the_bundled_example_loads(self):
        project = load_project(EXAMPLE)
        assert project.name
        assert len(project.design_space) == 3
        assert {r.name for r in project.regions} == {
            "mounting_face",
            "load_face",
            "fillet_surface",
            "bolt_holes",
        }

    def test_setup_digest_is_stable(self):
        assert load_project(EXAMPLE).setup_digest() == load_project(EXAMPLE).setup_digest()

    def test_changing_the_material_changes_the_digest(self):
        import yaml

        raw = yaml.safe_load(EXAMPLE.read_text())
        first = load_project_dict(raw).setup_digest()
        raw["material"]["elastic_modulus_mpa"] = 69000.0
        assert load_project_dict(raw).setup_digest() != first, (
            "a cached result computed with a different modulus is not a cache hit"
        )

    def test_a_typo_in_a_key_is_rejected_rather_than_defaulted(self):
        import yaml

        raw = yaml.safe_load(EXAMPLE.read_text())
        raw["material"]["allowable_stres_mpa"] = 160.0
        with pytest.raises(ProjectLoadError, match=r"allowable_stres_mpa|extra"):
            load_project_dict(raw)

    def test_load_case_referencing_an_unknown_region_is_rejected(self):
        import yaml

        raw = yaml.safe_load(EXAMPLE.read_text())
        raw["load_cases"][0]["loads"][0]["region"] = "not_a_region"
        with pytest.raises(ProjectLoadError, match="unknown region"):
            load_project_dict(raw)

    def test_a_future_schema_version_is_refused(self):
        import yaml

        raw = yaml.safe_load(EXAMPLE.read_text())
        raw["schema_version"] = 999
        with pytest.raises(ProjectLoadError, match="newer than this build"):
            load_project_dict(raw)

    def test_missing_file_gives_a_clear_message(self):
        with pytest.raises(ProjectLoadError, match="not found"):
            load_project("/nonexistent/project.yaml")
