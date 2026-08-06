"""Verification of linear buckling against the Euler column formula.

**Why this benchmark matters more than the others.** Minimising mass under
stress and displacement limits alone drives a design towards thin, slender
sections — which is precisely the geometry that buckles. A static analysis
cannot see it, so without this capability an optimiser will confidently hand
back a part that passes every stress check and folds up under load.

**Case.** Cantilever column, 400 mm long, 20 x 20 mm square section, aluminium
(E = 70 GPa, nu = 0.33). Fully fixed at one end, a 1 kN axial compressive load
distributed over the free end face.

**Reference.** Euler's critical load with the fixed-free effective length:

.. math::

    P_{cr} = \\frac{\\pi^2 E I}{(2L)^2}

With I = 13333.3 mm^4 this gives **14 393 N**, so the expected buckling factor
against a 1 kN reference load is **14.393**.

**Expected agreement.** The column is slender (effective slenderness ratio about
139), so Euler theory is accurate here and the finite element result should be
very close. Measured at the time of writing: **14.409, +0.11%**. A 3% tolerance
is applied.

The first two modes must come out nearly equal, because a square column is
equally happy to buckle about either axis. That is a real property of the
structure, and a solver that reported only one of them would be hiding half the
risk.

Recorded in ``docs/verification-plan.md``. Do not widen the tolerance to make a
failing build pass.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    BucklingSettings,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    SolverSpecification,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, BREADTH, DEPTH = 400.0, 20.0, 20.0
MODULUS, POISSON = 70000.0, 0.33
REFERENCE_LOAD = 1000.0

#: Agreement band against Euler theory. See the module docstring.
BUCKLING_TOLERANCE = 0.03


def euler_critical_load() -> float:
    """Fixed-free (cantilever) column: effective length is 2L."""
    second_moment = BREADTH * DEPTH**3 / 12.0
    return math.pi**2 * MODULUS * second_moment / (2.0 * LENGTH) ** 2


def expected_factor() -> float:
    return euler_critical_load() / REFERENCE_LOAD


def _solve(compressive: bool, tmp_path):
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="cantilever_box",
            parameters={"length": LENGTH, "width": BREADTH, "height": DEPTH},
        )
    )
    space = DesignSpace(
        (DesignVariable(id="length", minimum=LENGTH, maximum=LENGTH, default=LENGTH),)
    )
    geometry = provider.build(space.defaults(), tmp_path / "geometry")

    regions = (
        SemanticRegion(
            "fixed_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(-1.0, 0.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
        SemanticRegion(
            "load_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(1.0, 0.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
    )
    mesher = GmshMesher(MeshSpecification(global_size=8.0, minimum_size=3.0, element_order=2))
    mesh, _regions = mesher.generate(geometry, regions, tmp_path / "mesh")

    # Negative x pushes into the column (compression); positive pulls (tension).
    direction = -REFERENCE_LOAD if compressive else REFERENCE_LOAD
    model = AnalysisModel(
        name="euler column",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=160.0,
        ),
        load_cases=(
            LoadCase(
                id="axial",
                boundary_conditions=(BoundaryCondition(region="fixed_face"),),
                loads=(
                    Load(
                        kind=LoadKind.FORCE,
                        region="load_face",
                        vector=(direction, 0.0, 0.0),
                    ),
                ),
            ),
        ),
        buckling=BucklingSettings(enabled=True, modes=4),
    )

    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    results = solver.solve(model, mesh, tmp_path / "solver")
    return results.by_id("axial"), results


@pytest.fixture(scope="module")
def compression(tmp_path_factory):
    return _solve(compressive=True, tmp_path=tmp_path_factory.mktemp("buckle_c"))


@pytest.fixture(scope="module")
def tension(tmp_path_factory):
    return _solve(compressive=False, tmp_path=tmp_path_factory.mktemp("buckle_t"))


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestEulerColumn:
    def test_buckling_factor_matches_euler_theory(self, compression):
        fields, _results = compression
        computed = fields.critical_buckling_factor
        assert computed is not None, "a compressed column must have a buckling mode"

        theory = expected_factor()
        error = (computed - theory) / theory
        assert abs(error) < BUCKLING_TOLERANCE, (
            f"buckling factor {computed:.4f} differs from Euler theory "
            f"{theory:.4f} by {error:+.2%}, outside the {BUCKLING_TOLERANCE:.0%} band"
        )

    def test_square_column_has_two_equally_likely_directions(self, compression):
        """A square section buckles about either axis at the same load."""
        fields, _results = compression
        factors = sorted(f for f in fields.buckling_factors if f > 0)
        assert len(factors) >= 2
        assert factors[1] == pytest.approx(factors[0], rel=0.02), (
            "the first two modes of a square column should be nearly identical"
        )

    def test_the_close_mode_pair_is_reported_to_the_user(self, compression):
        _fields, results = compression
        assert any("two nearly equal buckling modes" in w for w in results.warnings), (
            "a symmetric structure's paired modes must be surfaced: the real "
            "margin is thinner than a single mode suggests"
        )

    def test_higher_modes_are_ordered_and_larger(self, compression):
        fields, _results = compression
        positive = [f for f in fields.buckling_factors if f > 0]
        assert positive == sorted(positive)
        assert positive[-1] > positive[0]


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestTensionDoesNotBuckle:
    """A column in tension must not be reported as unstable.

    CalculiX returns negative eigenvalues here, meaning the load would have to
    reverse before anything buckles. Treating a negative number as "the buckling
    factor" would make a perfectly safe tensile design look catastrophically
    unstable, and the optimiser would then avoid an entire region of the design
    space for no reason at all.
    """

    def test_no_positive_buckling_factor_under_tension(self, tension):
        fields, _results = tension
        assert fields.critical_buckling_factor is None, (
            f"a column in tension should have no positive buckling mode, got "
            f"{fields.buckling_factors}"
        )

    def test_the_user_is_told_rather_than_left_guessing(self, tension):
        _fields, results = tension
        assert any("does not buckle" in w for w in results.warnings)


@pytest.mark.verification
def test_euler_reference_value_is_what_the_docstring_claims():
    """Guard the reference itself, so a typo cannot move the goalposts."""
    assert euler_critical_load() == pytest.approx(14393.0, abs=5.0)
    assert expected_factor() == pytest.approx(14.393, abs=0.005)
