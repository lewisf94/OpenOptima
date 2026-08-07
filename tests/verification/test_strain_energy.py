"""Strain energy, checked against Clapeyron's theorem.

Strain energy is the work the load did on the part. For a linear elastic solve
it is exactly half the applied load times the displacement it moves through:

    U = 1/2 * sum over loaded nodes of F . u

That is an identity, not an approximation, and it holds on *any* mesh however
coarse. It uses the same applied forces the deck was written with and the same
displacements the solver returned, so it checks the number CalculiX reports
against a quantity assembled from two completely different parts of the output.

**Why this quantity is worth having.** Strain energy is the best-behaved
convergence measure available. A displacement-based finite element model is too
stiff, so it always *understates* strain energy, and refining can only ever
raise it. That means it approaches its limit monotonically from below -- unlike
a peak stress, which can scatter or run away. V6 could not use it because
nothing populated it.

**Independently checked here as well:** a second load case doubles nothing.
Energy is quadratic in load, so doubling a load quadruples the energy, and that
relation would break immediately if the parser paired energies with the wrong
step.
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
from openoptima.solvers.calculix.loads import consistent_nodal_forces
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, BREADTH, DEPTH = 200.0, 20.0, 20.0
MODULUS, POISSON = 70000.0, 0.33
FORCE = 1000.0
DOUBLE_FORCE = 2000.0

#: Clapeyron is an identity, so this is a numerical-precision band, not an
#: engineering one. It is loose enough only for the rounding in CalculiX's
#: printed output.
IDENTITY_TOLERANCE = 2e-3


def _cases() -> tuple[LoadCase, ...]:
    def case(identifier: str, magnitude: float) -> LoadCase:
        return LoadCase(
            id=identifier,
            boundary_conditions=(BoundaryCondition(region="fixed_face"),),
            loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -magnitude)),),
        )

    return (case("single", FORCE), case("double", DOUBLE_FORCE))


@pytest.fixture(scope="module")
def solution(tmp_path_factory):
    directory = tmp_path_factory.mktemp("strain_energy")

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
        name="strain energy",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=160.0,
        ),
        load_cases=_cases(),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    results = solver.solve(model, mesh, directory / "solver")
    return results, mesh


def _clapeyron_energy(fields, mesh, magnitude: float) -> float:
    """Half the applied load dotted with the displacement it moves through.

    The nodal forces are rebuilt with the same shape-function integration the
    deck writer used, so this is the work done by the load actually applied --
    not by an idealised point force.
    """
    coordinates_by_tag = {
        int(tag): mesh.coordinates[index] for index, tag in enumerate(mesh.node_tags)
    }
    contributions = consistent_nodal_forces(
        mesh.surface_triangles["load_face"], coordinates_by_tag, (0.0, 0.0, -magnitude)
    )
    row_of = {int(tag): row for row, tag in enumerate(fields.node_tags)}

    work = 0.0
    for tag, force in contributions.items():
        row = row_of.get(int(tag))
        if row is not None:
            work += float(np.dot(force, fields.displacement[row]))
    return 0.5 * work


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestStrainEnergyAgainstClapeyron:
    def test_strain_energy_is_reported_at_all(self, solution):
        """It was declared in the result type but never populated."""
        results, _mesh = solution
        for case in results.load_cases:
            assert case.strain_energy is not None, (
                f"no strain energy for load case {case.load_case_id!r}"
            )
            assert case.strain_energy > 0.0

    @pytest.mark.parametrize(
        ("case_id", "magnitude"), [("single", FORCE), ("double", DOUBLE_FORCE)]
    )
    def test_strain_energy_matches_the_work_done_by_the_load(self, solution, case_id, magnitude):
        results, mesh = solution
        fields = results.by_id(case_id)
        expected = _clapeyron_energy(fields, mesh, magnitude)

        assert fields.strain_energy == pytest.approx(expected, rel=IDENTITY_TOLERANCE), (
            f"case {case_id!r}: CalculiX reports {fields.strain_energy:.4f} mJ but the "
            f"load does {expected:.4f} mJ of work. Clapeyron's theorem makes these "
            f"identical for a linear elastic solve, on any mesh."
        )

    def test_doubling_the_load_quadruples_the_energy(self, solution):
        """Energy is quadratic in load, displacement is linear.

        This also catches the parser pairing an energy total with the wrong
        step: any mix-up between the two cases breaks the factor of four.
        """
        results, _mesh = solution
        single = results.by_id("single").strain_energy
        double = results.by_id("double").strain_energy

        assert double / single == pytest.approx(4.0, rel=1e-3)

    def test_the_two_cases_did_not_get_the_same_number(self, solution):
        """Guards against the parser handing every case the first total it saw."""
        results, _mesh = solution
        assert results.by_id("single").strain_energy != pytest.approx(
            results.by_id("double").strain_energy
        )

    def test_energy_is_consistent_with_the_tip_deflection(self, solution):
        """A cross-check in engineer's terms.

        For a load spread over the end face, the work done is about half the
        total load times the deflection it moves through, so U / (F/2) should
        land close to the tip deflection. This is looser than Clapeyron because
        the face does not move rigidly, but it catches an error of scale -- a
        factor of two, or millijoules read as joules.
        """
        results, _mesh = solution
        fields = results.by_id("single")
        implied = fields.strain_energy / (0.5 * FORCE)
        peak = float(fields.displacement_magnitude.max())

        assert 0.5 * peak < implied < 1.05 * peak, (
            f"energy implies a deflection of {implied:.4f} mm against a peak of "
            f"{peak:.4f} mm, which is not a plausible scale"
        )
