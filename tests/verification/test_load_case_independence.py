"""V7 — load cases are independent, and enveloped rather than averaged.

Two properties, both of which fail silently if broken.

**Independence.** Each load case is written as its own ``*STEP`` with
``OP=NEW`` on the loads and boundary conditions. Without ``OP=NEW`` the second
case would carry the first one's load as well, and every case after the first
would be analysed under a load nobody asked for. Nothing in the output would
say so -- the numbers would simply be too high, and consistently so.

**Enveloping.** The reported metric is the worst case, never the mean.
Averaging a failing case against a passing one hides the failure.

**Case.** The V1 cantilever, carrying two unrelated loads: 1 kN downwards and
3 kN sideways. The two differ in direction *and* magnitude, so an accumulation
between steps would be unmistakable rather than a small discrepancy.

The test solves the two cases together, then solves each one on its own, and
requires the per-case numbers to agree. That comparison is the point: a
two-case run and two single-case runs are the same physics, so any difference
is a defect in how cases are separated.

``tests/unit/test_load_case_envelope.py`` covers the enveloping arithmetic
without a solver.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    SolverSpecification,
    StressEvaluation,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, BREADTH, DEPTH = 200.0, 20.0, 20.0
MODULUS, POISSON = 70000.0, 0.33

DOWN_FORCE = 1000.0
SIDE_FORCE = 3000.0

#: A two-case run and two single-case runs are the same physics solved twice,
#: so they should agree to solver precision, not merely to engineering
#: tolerance. Anything looser would let a real accumulation through.
AGREEMENT = 1e-6
EQUILIBRIUM_TOLERANCE = 1e-4


def _down_case() -> LoadCase:
    return LoadCase(
        id="down",
        boundary_conditions=(BoundaryCondition(region="fixed_face"),),
        loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -DOWN_FORCE)),),
    )


def _side_case() -> LoadCase:
    return LoadCase(
        id="side",
        boundary_conditions=(BoundaryCondition(region="fixed_face"),),
        loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, -SIDE_FORCE, 0.0)),),
    )


def _solve(cases: tuple[LoadCase, ...], directory):
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
    geometry = provider.build(space.defaults(), directory / "geometry")

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
    mesher = GmshMesher(MeshSpecification(global_size=6.0, minimum_size=2.0, element_order=2))
    mesh, _region_map = mesher.generate(geometry, regions, directory / "mesh")

    model = AnalysisModel(
        name="load case independence",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=160.0,
        ),
        load_cases=cases,
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    return solver.solve(model, mesh, directory / "solver")


def _summary(fields) -> dict:
    return {
        "peak_displacement": float(fields.displacement_magnitude.max()),
        "peak_stress": float(fields.von_mises.max()),
        "reaction": tuple(float(component) for component in fields.reaction_force),
    }


@pytest.fixture(scope="module")
def solutions(tmp_path_factory):
    root = tmp_path_factory.mktemp("load_cases")
    together = _solve((_down_case(), _side_case()), root / "together")
    return {
        "down_together": _summary(together.by_id("down")),
        "side_together": _summary(together.by_id("side")),
        "down_alone": _summary(_solve((_down_case(),), root / "down").by_id("down")),
        "side_alone": _summary(_solve((_side_case(),), root / "side").by_id("side")),
        "warnings": together.warnings,
    }


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestLoadCaseIndependence:
    def test_the_first_case_matches_its_solo_run(self, solutions):
        together, alone = solutions["down_together"], solutions["down_alone"]
        assert together["peak_displacement"] == pytest.approx(
            alone["peak_displacement"], rel=AGREEMENT
        )
        assert together["peak_stress"] == pytest.approx(alone["peak_stress"], rel=AGREEMENT)

    def test_the_second_case_matches_its_solo_run(self, solutions):
        """The one that catches a missing OP=NEW.

        If loads accumulated between steps, this case would carry the first
        one's 1 kN as well as its own 3 kN, and its numbers would come out
        higher than the same case run on its own.
        """
        together, alone = solutions["side_together"], solutions["side_alone"]
        assert together["peak_displacement"] == pytest.approx(
            alone["peak_displacement"], rel=AGREEMENT
        ), "the second load case does not match its own solo run; loads are accumulating"
        assert together["peak_stress"] == pytest.approx(alone["peak_stress"], rel=AGREEMENT)

    def test_each_case_reacts_only_against_its_own_load(self, solutions):
        """Reaction is exact arithmetic, and it points where the load does.

        The downward case must show no sideways reaction and the sideways case
        no downward one. Accumulation would put a component in both.
        """
        down = solutions["down_together"]["reaction"]
        side = solutions["side_together"]["reaction"]

        assert down[2] == pytest.approx(DOWN_FORCE, rel=EQUILIBRIUM_TOLERANCE)
        assert abs(down[1]) < DOWN_FORCE * EQUILIBRIUM_TOLERANCE

        assert side[1] == pytest.approx(SIDE_FORCE, rel=EQUILIBRIUM_TOLERANCE)
        assert abs(side[2]) < SIDE_FORCE * EQUILIBRIUM_TOLERANCE

    def test_the_cases_really_are_different(self, solutions):
        """Guards the experiment itself.

        If both cases produced the same numbers, every assertion above would
        pass while proving nothing.
        """
        down = solutions["down_together"]
        side = solutions["side_together"]
        assert side["peak_stress"] > 2.0 * down["peak_stress"], (
            "the two cases must differ enough for accumulation to be visible"
        )

    def test_no_equilibrium_warnings_on_either_case(self, solutions):
        equilibrium = [w for w in solutions["warnings"] if "equilibrium" in w.lower()]
        assert not equilibrium, equilibrium

    def test_the_worst_case_governs_and_it_is_the_sideways_one(self, solutions):
        """Enveloping, on real numbers rather than stubs.

        The sideways load is three times larger, so it must govern. A mean of
        the two would land between them and understate the part's worst day.
        """
        down = solutions["down_together"]["peak_stress"]
        side = solutions["side_together"]["peak_stress"]

        envelope = max(down, side)
        mean = 0.5 * (down + side)

        assert envelope == pytest.approx(side)
        assert envelope > mean
        assert np.isfinite(mean)
