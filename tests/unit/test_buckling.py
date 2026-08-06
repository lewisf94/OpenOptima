"""Buckling: parsing, the negative-eigenvalue rule, and metric aggregation."""

from __future__ import annotations

import pytest

from openoptima.domain.model import BucklingSettings
from openoptima.solvers.calculix.dat import (
    BucklingTable,
    parse_buckling,
    parse_dat,
    reactions_in_step,
)

# One static step and one buckle step, as the deck writes them when buckling is
# enabled. Note that CalculiX reports a reaction total in the buckle step too --
# an artefact of the eigenvalue solve, not a real reaction.
DAT_WITH_BUCKLING = """
                        S T E P       1


 total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

       -7.163067E-09 -1.116377E-08  2.500000E+03

                        S T E P       2


     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
                FACTOR

      1   0.1440865E+02
      2   0.1440915E+02
      3   0.1275687E+03

 total force (fx,fy,fz) for set FIXED and time  0.0000000E+00

        1.075000E+00 -2.898000E+00  5.000509E+03
"""

DAT_TENSION = """
                        S T E P       1


     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
                FACTOR

      1  -0.1440865E+02
      2  -0.1275687E+03
"""


class TestBucklingTable:
    def test_critical_factor_is_the_lowest_positive(self):
        table = BucklingTable((14.41, 14.40, 127.5))
        assert table.critical == pytest.approx(14.40)

    def test_negative_eigenvalues_are_not_a_buckling_failure(self):
        """Tension. The load would have to reverse before anything buckles.

        Reporting -14.4 as "the buckling factor" would make a perfectly safe
        design look catastrophically unstable and drive the optimiser away from
        an entire region of the design space for no reason.
        """
        table = BucklingTable((-14.4, -127.5))
        assert table.critical is None

    def test_a_mixture_takes_only_the_positive_ones(self):
        table = BucklingTable((-50.0, 8.2, 19.0))
        assert table.critical == pytest.approx(8.2)

    def test_close_pair_is_detected(self):
        """A symmetric part buckles in either of two directions at the same load."""
        assert BucklingTable((14.4086, 14.4091, 127.5)).has_close_pair

    def test_well_separated_modes_are_not_flagged(self):
        assert not BucklingTable((14.4, 60.0, 127.5)).has_close_pair

    def test_close_pair_needs_two_positive_modes(self):
        assert not BucklingTable((14.4,)).has_close_pair
        assert not BucklingTable((-3.0, 14.4)).has_close_pair


class TestParsing:
    def test_buckling_factors_are_read(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(DAT_WITH_BUCKLING)
        tables = parse_buckling(path)
        assert len(tables) == 1
        assert tables[0].factors == pytest.approx((14.40865, 14.40915, 127.5687))

    def test_table_is_tagged_with_its_step(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(DAT_WITH_BUCKLING)
        assert parse_buckling(path)[0].step == 2

    def test_negative_factors_parse_correctly(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(DAT_TENSION)
        table = parse_buckling(path)[0]
        assert table.factors[0] == pytest.approx(-14.40865)
        assert table.critical is None

    def test_reaction_totals_are_tagged_with_their_step(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(DAT_WITH_BUCKLING)
        totals = parse_dat(path)
        assert len(totals) == 2
        assert totals[0].step == 1
        assert totals[1].step == 2

    def test_only_the_static_step_reaction_is_selected(self, tmp_path):
        """The regression this guards.

        Associating reactions with load cases by dividing the record count
        summed the real static reaction with the buckle step's artefact,
        reporting 5000.5 N for a 2500 N load and failing the equilibrium check
        on a perfectly sound model.
        """
        path = tmp_path / "job.dat"
        path.write_text(DAT_WITH_BUCKLING)
        totals = parse_dat(path)

        static = reactions_in_step(totals, 1)
        assert len(static) == 1
        assert static[0].force[2] == pytest.approx(2500.0)

        buckle = reactions_in_step(totals, 2)
        assert len(buckle) == 1
        assert buckle[0].force[2] == pytest.approx(5000.509)

    def test_missing_file_returns_empty(self, tmp_path):
        assert parse_buckling(tmp_path / "absent.dat") == []

    def test_a_file_without_buckling_yields_no_tables(self, tmp_path):
        path = tmp_path / "job.dat"
        path.write_text(
            "                        S T E P       1\n\n"
            " total force (fx,fy,fz) for set FIXED and time  0.1000000E+01\n\n"
            "       0.0 0.0  1.000000E+03\n"
        )
        assert parse_buckling(path) == []
        assert len(parse_dat(path)) == 1


class TestSettings:
    def test_disabled_by_default(self):
        assert not BucklingSettings().enabled

    def test_at_least_one_mode(self):
        with pytest.raises(ValueError, match="at least 1"):
            BucklingSettings(enabled=True, modes=0)

    def test_absurd_mode_counts_are_rejected(self):
        with pytest.raises(ValueError, match="rarely useful"):
            BucklingSettings(enabled=True, modes=500)


class TestMetricAggregation:
    """Across load cases the governing buckling factor is the lowest, and a
    non-buckling case must not drag it down."""

    def make_case(self, case_id: str, factor: float | None, modes=()):
        from openoptima.domain.results import LoadCaseResult

        return LoadCaseResult(
            load_case_id=case_id,
            displacement_max=0.1,
            displacement_node=1,
            stress_measure=50.0,
            stress_raw_max=60.0,
            stress_measure_name="p99",
            reaction_force=(0.0, 0.0, 1000.0),
            buckling_factor=factor,
            buckling_modes=modes or ((factor,) if factor is not None else ()),
        )

    def test_lowest_factor_governs(self):
        cases = [self.make_case("a", 8.0), self.make_case("b", 3.2)]
        values = [c.buckling_factor for c in cases if c.buckling_factor is not None]
        assert min(values) == pytest.approx(3.2)

    def test_a_tensile_case_does_not_lower_the_envelope(self):
        cases = [self.make_case("compress", 3.2), self.make_case("tension", None)]
        values = [c.buckling_factor for c in cases if c.buckling_factor is not None]
        assert min(values) == pytest.approx(3.2)


class TestPlausibilityGuard:
    """The guard that refuses to report a buckling factor it cannot trust.

    Background: CalculiX's buckling solve was verified accurate on a 20 mm
    square column at three lengths (better than 1% against Euler, mode series
    in the correct 1:9 ratio), but on more slender members it returned a factor
    up to nine times too high, with a mode series nothing like a column's. That
    error is optimistic, so an optimiser handed it selects the unsafe design and
    reports it as the winner. Hence a failure, not a warning.
    """

    def build_mesh(self, length: float, section: float, divisions: int = 6):
        """A crude prismatic bar mesh, enough to exercise the estimator."""
        import numpy as np

        from openoptima.meshing.base import MeshData

        nodes: list[list[float]] = []
        tags: list[int] = []
        tag = 1
        grid: dict[tuple[int, int, int], int] = {}
        for i in range(divisions + 1):
            for j in range(2):
                for k in range(2):
                    grid[(i, j, k)] = tag
                    nodes.append([i * length / divisions, j * section, k * section])
                    tags.append(tag)
                    tag += 1

        # Six tets per hexahedral cell.
        cells = [
            (0, 1, 3, 7),
            (0, 1, 7, 5),
            (0, 5, 7, 4),
            (0, 3, 2, 7),
            (0, 6, 4, 7),
            (0, 2, 6, 7),
        ]
        corner = [
            (0, 0, 0),
            (1, 0, 0),
            (0, 1, 0),
            (1, 1, 0),
            (0, 0, 1),
            (1, 0, 1),
            (0, 1, 1),
            (1, 1, 1),
        ]
        connectivity: list[list[int]] = []
        for i in range(divisions):
            local = [grid[(i + dx, dy, dz)] for dx, dy, dz in corner]
            for cell in cells:
                connectivity.append([local[c] for c in cell])

        return MeshData(
            node_tags=np.array(tags, dtype=np.int64),
            coordinates=np.array(nodes, dtype=np.float64),
            element_tags=np.arange(1, len(connectivity) + 1, dtype=np.int64),
            connectivity=np.array(connectivity, dtype=np.int64),
            element_type="C3D4",
        )

    def material(self):
        from openoptima.domain.model import Material

        return Material.from_engineering_units(
            name="Al",
            elastic_modulus_mpa=70000.0,
            poisson_ratio=0.33,
            density_kg_m3=2700.0,
            allowable_stress_mpa=160.0,
        )

    def test_column_properties_recover_the_section(self):
        from openoptima.results.buckling_check import estimate_column_properties

        mesh = self.build_mesh(length=400.0, section=20.0)
        estimate = estimate_column_properties(mesh)
        assert estimate is not None
        assert estimate.length == pytest.approx(400.0)
        assert estimate.area == pytest.approx(400.0, rel=1e-6)
        # I = b h^3 / 12 = 20 * 8000 / 12
        assert estimate.second_moment_min == pytest.approx(13333.3, rel=1e-4)

    def test_a_slender_member_is_refused_rather_than_reported(self):
        from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=600.0, section=15.0)  # very slender
        with pytest.raises(EvaluationFailure) as info:
            check_buckling_plausibility(
                mesh,
                self.material(),
                "axial",
                buckling_factor=2.79,
                applied_load=30000.0,
                slenderness_limit=150.0,
            )
        assert info.value.code is FailureCode.RESULT_UNRELIABLE
        assert info.value.outcome is Outcome.ERROR, (
            "an untrustworthy number is 'we could not find out', not 'the design is bad'"
        )
        assert "UNSAFE" in info.value.message

    def test_a_member_in_the_verified_regime_is_reported(self):
        """Slenderness 139 -- the case measured accurate to 0.11% against Euler.

        It is close enough to the limit to earn an advisory, but the number is
        reported rather than refused.
        """
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=400.0, section=20.0)
        warnings = check_buckling_plausibility(
            mesh,
            self.material(),
            "axial",
            buckling_factor=14.4,
            applied_load=1000.0,
            slenderness_limit=150.0,
        )
        assert all("worth cross-checking" in w for w in warnings)

    def test_a_stocky_member_passes_silently(self):
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=400.0, section=45.0)  # slenderness ~62
        assert (
            check_buckling_plausibility(
                mesh,
                self.material(),
                "axial",
                buckling_factor=60.0,
                applied_load=1000.0,
                slenderness_limit=150.0,
            )
            == []
        )

    def test_the_estimator_matches_the_analytical_section(self):
        """Exact tetrahedron formula, so a coarse mesh does not cause a false refusal."""
        from openoptima.results.buckling_check import estimate_column_properties

        estimate = estimate_column_properties(self.build_mesh(400.0, 20.0))
        assert estimate is not None
        assert estimate.second_moment_min == pytest.approx(20 * 20**3 / 12, rel=1e-6)
        assert estimate.radius_of_gyration == pytest.approx(20 / 12**0.5, rel=1e-6)

    def test_a_compact_block_is_not_checked_at_all(self):
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=60.0, section=40.0)
        assert (
            check_buckling_plausibility(
                mesh, self.material(), "axial", buckling_factor=90.0, applied_load=100.0
            )
            == []
        )

    def test_a_physically_impossible_factor_is_refused(self):
        """Above the fixed-fixed bound nothing real can buckle."""
        from openoptima.domain.failures import EvaluationFailure, FailureCode
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=400.0, section=20.0)
        with pytest.raises(EvaluationFailure) as info:
            check_buckling_plausibility(
                mesh,
                self.material(),
                "axial",
                buckling_factor=5000.0,
                applied_load=1000.0,
                slenderness_limit=1e9,  # slenderness gate off
            )
        assert info.value.code is FailureCode.RESULT_UNRELIABLE

    def test_no_buckling_result_means_nothing_to_check(self):
        from openoptima.results.buckling_check import check_buckling_plausibility

        mesh = self.build_mesh(length=600.0, section=15.0)
        assert (
            check_buckling_plausibility(
                mesh, self.material(), "axial", buckling_factor=None, applied_load=30000.0
            )
            == []
        )

    def test_unreliable_results_are_never_retried(self):
        from openoptima.domain.failures import FailureCode, is_retryable

        assert not is_retryable(FailureCode.RESULT_UNRELIABLE), (
            "a modelling limitation is deterministic; retrying only burns budget"
        )
