"""Verification against closed-form beam theory.

This is the test that says the numbers mean something.  Everything else checks
that the software does what it was told; this checks that what it was told
produces physics.

**Case.** Prismatic cantilever, 200 x 20 x 20 mm, aluminium (E = 70 GPa,
nu = 0.33), fully fixed at x = 0, 1 kN transverse point load spread over the
free end face.

**Reference.** Timoshenko beam theory:

.. math::

    \\delta = \\frac{FL^3}{3EI} + \\frac{FL}{\\kappa G A}

with :math:`\\kappa = 5/6` for a rectangular section.  For this geometry that is
2.857 mm of bending plus 0.023 mm of shear, so 2.880 mm total.

**Expected agreement.** The finite element model should come out slightly
*stiffer* than the beam solution, because fully fixing the end face suppresses
the Poisson contraction that beam theory allows.  A tolerance of 3% is applied
and the measured value at the time of writing was **-0.98%** (2.852 mm), well
inside it.  A result that drifts outside this band, or that lands on the *soft*
side, indicates a real regression rather than mesh noise.

Recorded in ``docs/verification-plan.md``. Do not widen the tolerance to make a
failing build pass — that is exactly the change that must be reviewed by a human.
"""

from __future__ import annotations

import math

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
from openoptima.domain.regions import (
    RegionSelector,
    SemanticRegion,
    SurfaceType,
)
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, BREADTH, DEPTH = 200.0, 20.0, 20.0
MODULUS, POISSON = 70000.0, 0.33
FORCE = 1000.0

#: Agreement band against Timoshenko theory. See the module docstring.
DEFLECTION_TOLERANCE = 0.03
#: Equilibrium is exact arithmetic, not an approximation; hold it tight.
EQUILIBRIUM_TOLERANCE = 1e-4


def timoshenko_tip_deflection() -> float:
    second_moment = BREADTH * DEPTH**3 / 12.0
    bending = FORCE * LENGTH**3 / (3.0 * MODULUS * second_moment)
    shear_modulus = MODULUS / (2.0 * (1.0 + POISSON))
    shear = FORCE * LENGTH / ((5.0 / 6.0) * shear_modulus * BREADTH * DEPTH)
    return bending + shear


def nominal_bending_stress() -> float:
    second_moment = BREADTH * DEPTH**3 / 12.0
    return FORCE * LENGTH * (DEPTH / 2.0) / second_moment


@pytest.fixture(scope="module")
def cantilever_solution(tmp_path_factory):
    directory = tmp_path_factory.mktemp("cantilever")

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

    mesher = GmshMesher(MeshSpecification(global_size=5.0, minimum_size=2.0, element_order=2))
    mesh, region_map = mesher.generate(geometry, regions, directory / "mesh")

    model = AnalysisModel(
        name="cantilever verification",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=160.0,
        ),
        load_cases=(
            LoadCase(
                id="tip_load",
                boundary_conditions=(BoundaryCondition(region="fixed_face"),),
                loads=(
                    Load(
                        kind=LoadKind.FORCE,
                        region="load_face",
                        vector=(0.0, 0.0, -FORCE),
                    ),
                ),
            ),
        ),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )

    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=900))
    results = solver.solve(model, mesh, directory / "solver")
    return results.by_id("tip_load"), mesh, geometry, region_map


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestCantileverAgainstBeamTheory:
    def test_tip_deflection_matches_theory(self, cantilever_solution):
        fields, _mesh, _geometry, _regions = cantilever_solution
        computed = float(fields.displacement_magnitude.max())
        theory = timoshenko_tip_deflection()
        error = (computed - theory) / theory

        assert abs(error) < DEFLECTION_TOLERANCE, (
            f"tip deflection {computed:.4f} mm differs from Timoshenko theory "
            f"{theory:.4f} mm by {error:+.2%}, outside the {DEFLECTION_TOLERANCE:.0%} band"
        )

    def test_model_is_stiffer_than_beam_theory_as_expected(self, cantilever_solution):
        """A fully fixed end suppresses Poisson contraction, so FE must be stiffer.

        Landing on the soft side would mean something is wrong with the
        constraint, the element order or the load, not merely that the mesh is
        coarse.
        """
        fields, _mesh, _geometry, _regions = cantilever_solution
        computed = float(fields.displacement_magnitude.max())
        assert computed < timoshenko_tip_deflection()

    def test_reaction_force_balances_the_applied_load(self, cantilever_solution):
        """Global equilibrium. Exact arithmetic, so the tolerance is tight."""
        fields, _mesh, _geometry, _regions = cantilever_solution
        reaction_z = fields.reaction_force[2]
        assert reaction_z == pytest.approx(FORCE, rel=EQUILIBRIUM_TOLERANCE)

    def test_no_spurious_transverse_reaction(self, cantilever_solution):
        fields, _mesh, _geometry, _regions = cantilever_solution
        assert abs(fields.reaction_force[0]) < FORCE * 1e-4
        assert abs(fields.reaction_force[1]) < FORCE * 1e-4

    def test_peak_stress_is_near_the_nominal_bending_stress(self, cantilever_solution):
        fields, _mesh, _geometry, _regions = cantilever_solution
        computed = float(fields.von_mises.max())
        nominal = nominal_bending_stress()
        # The built-in end raises the true peak somewhat; a factor of 1.5 is a
        # generous ceiling that still catches a genuinely wrong stress field.
        assert 0.85 * nominal < computed < 1.5 * nominal, (
            f"peak von Mises {computed:.1f} MPa is not consistent with the "
            f"{nominal:.1f} MPa nominal bending stress"
        )

    def test_mesh_volume_matches_the_cad_volume(self, cantilever_solution):
        _fields, mesh, geometry, _regions = cantilever_solution
        assert geometry.volume == pytest.approx(LENGTH * BREADTH * DEPTH, rel=1e-9)
        assert mesh.quality is not None
        assert mesh.quality.volume_error < 0.01

    def test_second_order_elements_were_actually_used(self, cantilever_solution):
        _fields, mesh, _geometry, _regions = cantilever_solution
        assert mesh.element_type == "C3D10", (
            "first-order tets are far too stiff for this comparison; if the mesher "
            "silently fell back the deflection check above is meaningless"
        )

    def test_regions_resolved_uniquely(self, cantilever_solution):
        _fields, _mesh, _geometry, region_map = cantilever_solution
        assert len(region_map["fixed_face"].face_tags) == 1
        assert len(region_map["load_face"].face_tags) == 1
        assert region_map["load_face"].signatures[0].area == pytest.approx(
            BREADTH * DEPTH, rel=1e-6
        )


@pytest.mark.verification
def test_beam_theory_reference_values_are_what_the_docstring_claims():
    """Guard the reference itself, so a typo in the formula cannot pass silently."""
    assert timoshenko_tip_deflection() == pytest.approx(2.880, abs=0.005)
    assert nominal_bending_stress() == pytest.approx(150.0, abs=0.1)
    assert math.isclose(
        FORCE * LENGTH**3 / (3.0 * MODULUS * BREADTH * DEPTH**3 / 12.0),
        2.857,
        abs_tol=0.005,
    )
