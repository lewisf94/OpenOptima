"""V11 — the buckling load scaling is load-bearing, and this proves it.

**Why this benchmark exists.** V3 and V9 check buckling where the answer is
comfortable: a 1 kN load on a column that carries 14.4 kN, so the buckling
factor is 14.4. That is well clear of the range where CalculiX misbehaves, so
those tests would still pass if the load scaling were deleted tomorrow.

This one sits *inside* the dangerous range. CalculiX silently returns the
second buckling mode instead of the first when the true factor against the
applied load falls below about **0.52** — with nothing in its output to say so.
OpenOptima works around this by solving the buckling step against a load a
thousand times smaller and dividing the answer back, which is exact because
the eigenvalue is exactly inversely proportional to the reference load.

**The measurement.** One column, one mesh, only the applied load changed.
Because the eigenvalue scales exactly with 1/load, ``factor x load`` must come
out the same every time. It does not, without the scaling:

===========  ==========  ==========  ==============  ==========
applied      scaling     factor      implied P_cr    vs Euler
===========  ==========  ==========  ==============  ==========
1 000 N      none        14.4086     14 409 N        1.00x
1 000 N      1000x       14.4086     14 409 N        1.00x
30 000 N     none         4.2523     127 569 N       **8.86x**
30 000 N     1000x        0.4803     14 409 N        1.00x
60 000 N     none         2.1261     127 569 N       **8.86x**
60 000 N     1000x        0.2401     14 409 N        1.00x
===========  ==========  ==========  ==============  ==========

A column that folds under 14.4 kN is reported as surviving 128 kN. The error is
**optimistic**, which is the dangerous direction, and an optimiser reads the
number and not any warning attached to it.

**This also settles the topology question.** ``beso`` offers buckling as an
optimisation objective, drives the same CalculiX, and applies no such scaling.
So its buckling objective is affected, and it is refused in
``topology/config.py`` rather than passed on. That refusal is checked here too,
next to the evidence for it.

Recorded as V11 in ``docs/verification-plan.md``. **Do not remove the scaling
and do not widen these tolerances.**
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

#: Loads chosen so the true buckling factor lands either side of the threshold.
#: At 1 kN it is 14.4, comfortably safe. At 30 kN it is 0.48 and at 60 kN it is
#: 0.24 -- both below the ~0.52 point where CalculiX starts skipping the mode.
SAFE_LOAD = 1000.0
DANGEROUS_LOADS = (30000.0, 60000.0)

#: Agreement band against Euler. The measured error is 0.11%.
TOLERANCE = 0.03


def euler_critical_load() -> float:
    """Fixed-free (cantilever) column: the effective length is twice the real one."""
    second_moment = BREADTH * DEPTH**3 / 12.0
    return math.pi**2 * MODULUS * second_moment / (2.0 * LENGTH) ** 2


@pytest.fixture(scope="module")
def column(tmp_path_factory):
    """One geometry and one mesh, reused for every load below.

    Sharing the mesh is the point of the experiment: if the mesh changed
    between loads, a difference in the answer could be blamed on that instead
    of on the solver.
    """
    tmp = tmp_path_factory.mktemp("v11")
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
    geometry = provider.build(space.defaults(), tmp / "geometry")
    regions = (
        SemanticRegion(
            "fixed_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(-1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
        SemanticRegion(
            "load_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
    )
    mesher = GmshMesher(MeshSpecification(global_size=8.0, minimum_size=3.0, element_order=2))
    mesh, _ = mesher.generate(geometry, regions, tmp / "mesh")
    return tmp, mesh


def solve_at(load_n: float, column, subdirectory: str) -> tuple[float, ...]:
    tmp, mesh = column
    material = Material.from_engineering_units(
        name="Aluminium",
        elastic_modulus_mpa=MODULUS,
        poisson_ratio=POISSON,
        density_kg_m3=2700.0,
        allowable_stress_mpa=160.0,
    )
    model = AnalysisModel(
        name="column",
        material=material,
        load_cases=(
            LoadCase(
                id="axial",
                boundary_conditions=(BoundaryCondition(region="fixed_face"),),
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(-load_n, 0.0, 0.0)),),
            ),
        ),
        # Raised so the guard does not stop this column before the solver is
        # reached. The point here is the solver's own behaviour.
        buckling=BucklingSettings(enabled=True, modes=4, slenderness_limit=500.0),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    results = solver.solve(model, mesh, tmp / subdirectory)
    return results.by_id("axial").buckling_factors


@requires_gmsh
@requires_calculix
class TestTheAnswerDoesNotDependOnTheAppliedLoad:
    """The physical invariant: ``factor x load`` is the critical load, always."""

    def test_it_is_right_where_the_solver_behaves(self, column):
        factors = solve_at(SAFE_LOAD, column, "safe")
        assert factors, "no buckling factor was produced"
        critical = factors[0] * SAFE_LOAD
        assert critical == pytest.approx(euler_critical_load(), rel=TOLERANCE)

    @pytest.mark.parametrize("load", DANGEROUS_LOADS)
    def test_it_is_still_right_inside_the_dangerous_range(self, load, column):
        """The case V3 and V9 do not reach.

        Here the true factor is below 0.52, which is where CalculiX starts
        returning the wrong mode. The load scaling is what keeps this correct.
        """
        factors = solve_at(load, column, f"dangerous_{load:.0f}")
        assert factors, "no buckling factor was produced"

        critical = factors[0] * load
        assert critical == pytest.approx(euler_critical_load(), rel=TOLERANCE), (
            f"at {load:.0f} N the implied critical load is {critical:.0f} N, but "
            f"Euler gives {euler_critical_load():.0f} N. If this has drifted "
            f"upwards by a factor of about nine, the buckling load scaling has "
            f"been removed or defeated."
        )

    def test_the_factor_itself_is_below_the_threshold(self, column):
        """Proves the test is actually exercising the dangerous range.

        Without this, a change that quietly moved the load could leave the test
        passing while no longer testing anything.
        """
        factors = solve_at(DANGEROUS_LOADS[0], column, "threshold_check")
        assert 0.0 < factors[0] < 0.52


@requires_gmsh
@requires_calculix
def test_removing_the_scaling_reproduces_the_defect(column, monkeypatch):
    """The regression guard: shows the scaling is doing real work.

    Without it, this column is reported as about nine times stronger than it
    is. If this test ever *passes* with the scaling removed, then either
    CalculiX has been fixed or the measurement has stopped being meaningful,
    and either way a human needs to look before anything is changed.
    """
    from openoptima.solvers.calculix import deck as deck_module
    from openoptima.solvers.calculix import solver as solver_module

    # The solver imported the constant by value, so both names need setting.
    monkeypatch.setattr(deck_module, "BUCKLING_LOAD_SCALE", 1.0)
    monkeypatch.setattr(solver_module, "BUCKLING_LOAD_SCALE", 1.0)

    load = DANGEROUS_LOADS[0]
    factors = solve_at(load, column, "unscaled")
    assert factors, "no buckling factor was produced"

    critical = factors[0] * load
    overstatement = critical / euler_critical_load()
    assert overstatement > 5.0, (
        f"without the load scaling the critical load should come out far too "
        f"high, and it came out {overstatement:.2f}x Euler. Measured at 8.86x "
        f"when this was written."
    )


class TestTopologyWillNotPassBucklingOn:
    """beso drives the same solver and applies no scaling of its own."""

    def test_the_objective_is_refused(self):
        from openoptima.topology.config import OBJECTIVES, UnsupportedObjective, objective_for

        assert "buckling" not in OBJECTIVES
        with pytest.raises(UnsupportedObjective, match="nine times too high"):
            objective_for("buckling")


def test_the_docstring_table_matches_the_formula():
    """Guards the numbers quoted above from drifting away from the physics."""
    assert euler_critical_load() == pytest.approx(14393.2, rel=1e-4)
    # The factors quoted in the table, back-calculated.
    assert euler_critical_load() / SAFE_LOAD == pytest.approx(14.393, rel=1e-3)
    assert euler_critical_load() / 30000.0 == pytest.approx(0.4798, rel=1e-3)
    assert euler_critical_load() / 60000.0 == pytest.approx(0.2399, rel=1e-3)
