"""V6 — mesh convergence of the cantilever.

The other benchmarks compare one mesh against theory. This one compares a
sequence of meshes against *itself*, and asks the question every result in this
software depends on: have the numbers stopped changing?

**Case.** The V1 cantilever, 200 x 20 x 20 mm, aluminium, fully fixed at x = 0,
1 kN over the free end face. Run at four mesh densities from 3.16 mm down to
1.61 mm average element size (2,537 to 19,298 second-order tetrahedra).

**What this proves.** Two quantities from the same runs behave in opposite
ways, and the software must report each correctly:

- **Tip deflection settles.** It moved 0.058% in total across all four meshes,
  and extrapolates to 2.8658 mm against Timoshenko's 2.8799 mm -- an error of
  -0.49%, on the *stiff* side, which is the physically correct direction for a
  fully fixed end.
- **Raw peak von Mises stress never settles.** It climbed 147.3 -> 183.7 MPa,
  a total movement of 19.8%, and the steps got *larger* with every refinement.
  This is the stress singularity at the fixed face. The true elastic stress
  there is unbounded, so no mesh will ever converge, and refining only makes
  the number bigger.

The ratio between those two spreads is about 340. That single figure is the
evidence behind the rule that OpenOptima must not optimise raw peak stress:
a design search handed the peak would be chasing the mesh, not the design.

**A result worth noticing.** V1 measures -0.98% against beam theory at a single
5 mm mesh. Extrapolating to zero mesh size gives -0.49%. So roughly half of
V1's discrepancy is mesh coarseness and the other half is the genuine physical
stiffening from suppressing Poisson contraction at the built-in end. Neither
test could establish that alone.

Measured values are recorded in ``docs/verification-plan.md``. Do not widen a
tolerance to make a failing build pass -- that is exactly the change a human
must review.
"""

from __future__ import annotations

import pytest

from openoptima.domain.convergence import Behaviour, GridLevel, analyse_metric, representative_size
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
FORCE = 1000.0

#: Requested element sizes, coarsest first. These exact sizes produced the
#: measured values in the docstring; changing them invalidates the tolerances.
MESH_SIZES = (6.0, 4.6, 3.55, 2.73)

#: The extrapolated deflection must agree with beam theory to this band.
#: Measured: -0.49%.
EXTRAPOLATION_TOLERANCE = 0.02
#: Deflection must have all but stopped moving. Measured spread: 0.058%.
SETTLED_SPREAD = 0.005
#: The raw peak must be visibly running away. Measured spread: 19.8%.
RUNAWAY_SPREAD = 0.05
#: The whole point, as a single number. Measured ratio: about 340.
MINIMUM_SPREAD_RATIO = 20.0
#: Equilibrium is exact arithmetic and holds at every mesh density.
EQUILIBRIUM_TOLERANCE = 1e-4


def timoshenko_tip_deflection() -> float:
    second_moment = BREADTH * DEPTH**3 / 12.0
    bending = FORCE * LENGTH**3 / (3.0 * MODULUS * second_moment)
    shear_modulus = MODULUS / (2.0 * (1.0 + POISSON))
    shear = FORCE * LENGTH / ((5.0 / 6.0) * shear_modulus * BREADTH * DEPTH)
    return bending + shear


def _solve_at(size: float, directory):
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

    # minimum_size scales with global_size so the refinement stays uniform.
    mesher = GmshMesher(
        MeshSpecification(global_size=size, minimum_size=size / 3.0, element_order=2)
    )
    mesh, _region_map = mesher.generate(geometry, regions, directory / "mesh")

    model = AnalysisModel(
        name="cantilever convergence",
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
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -FORCE)),),
            ),
        ),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )

    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    fields = solver.solve(model, mesh, directory / "solver").by_id("tip_load")
    quality = mesh.quality
    return {
        "h": representative_size(quality.mesh_volume, quality.element_count),
        "elements": quality.element_count,
        "nodes": quality.node_count,
        "element_type": mesh.element_type,
        "displacement": float(fields.displacement_magnitude.max()),
        "raw_peak": float(fields.von_mises.max()),
        "reaction_z": fields.reaction_force[2],
    }


@pytest.fixture(scope="module")
def refinement_sequence(tmp_path_factory):
    """Solve the same cantilever at four mesh densities. Runs once per module."""
    root = tmp_path_factory.mktemp("convergence")
    return [_solve_at(size, root / f"level{index}") for index, size in enumerate(MESH_SIZES)]


def _assess(sequence, key: str):
    levels = [
        GridLevel(
            label=f"L{index}",
            representative_size=record["h"],
            node_count=record["nodes"],
            element_count=record["elements"],
            value=record[key],
        )
        for index, record in enumerate(sequence)
    ]
    return analyse_metric(key, levels)


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestCantileverMeshConvergence:
    def test_the_meshes_really_do_get_finer(self, refinement_sequence):
        """Guard the experiment itself before trusting anything measured from it."""
        sizes = [record["h"] for record in refinement_sequence]
        assert sizes == sorted(sizes, reverse=True), (
            f"meshes must be ordered coarse to fine, got {sizes}"
        )
        counts = [record["elements"] for record in refinement_sequence]
        assert counts == sorted(counts), f"element count must rise with refinement, got {counts}"
        assert counts[-1] > 4 * counts[0], (
            "the finest mesh must be substantially finer than the coarsest, or "
            "there is nothing to measure"
        )

    def test_every_mesh_used_second_order_elements(self, refinement_sequence):
        """First-order tets are far too stiff; a silent fallback at any level
        would corrupt the whole sequence."""
        for record in refinement_sequence:
            assert record["element_type"] == "C3D10"

    def test_equilibrium_holds_at_every_mesh_density(self, refinement_sequence):
        """Reaction against applied load is exact arithmetic, and mesh-independent.

        If this drifts with refinement, the load is not being applied the way
        the deflection comparison assumes.
        """
        for record in refinement_sequence:
            assert record["reaction_z"] == pytest.approx(FORCE, rel=EQUILIBRIUM_TOLERANCE)

    # -- the deflection settles ---------------------------------------------
    def test_deflection_settles(self, refinement_sequence):
        result = _assess(refinement_sequence, "displacement")
        assert result.behaviour is Behaviour.SETTLING, (
            f"tip deflection should settle under refinement, got {result.behaviour.value}"
        )

    def test_deflection_has_all_but_stopped_moving(self, refinement_sequence):
        result = _assess(refinement_sequence, "displacement")
        assert result.spread is not None
        assert result.spread < SETTLED_SPREAD, (
            f"tip deflection moved {result.spread:.3%} across the four meshes, "
            f"above the {SETTLED_SPREAD:.1%} band. Measured at the time of "
            f"writing: 0.058%"
        )

    def test_deflection_extrapolates_to_beam_theory(self, refinement_sequence):
        """The value a perfect mesh would give, against Timoshenko.

        This is a stronger statement than V1's single-mesh comparison: it
        removes mesh coarseness from the discrepancy and leaves only the
        physical difference between the models.
        """
        result = _assess(refinement_sequence, "displacement")
        assert result.extrapolated is not None
        theory = timoshenko_tip_deflection()
        error = (result.extrapolated - theory) / theory
        assert abs(error) < EXTRAPOLATION_TOLERANCE, (
            f"extrapolated deflection {result.extrapolated:.4f} mm differs from "
            f"Timoshenko {theory:.4f} mm by {error:+.2%}, outside the "
            f"{EXTRAPOLATION_TOLERANCE:.0%} band"
        )

    def test_the_perfect_mesh_answer_is_still_on_the_stiff_side(self, refinement_sequence):
        """Refining cannot remove the physical stiffening at a built-in end.

        A fully fixed face suppresses the Poisson contraction beam theory
        allows, so even a perfect mesh must land below the beam solution. An
        extrapolation on the soft side would mean the constraint or the load is
        wrong, not that the mesh was coarse.
        """
        result = _assess(refinement_sequence, "displacement")
        assert result.extrapolated is not None
        assert result.extrapolated < timoshenko_tip_deflection()

    def test_deflection_softens_as_the_mesh_refines(self, refinement_sequence):
        """Coarse meshes are artificially stiff, so deflection must rise."""
        values = [record["displacement"] for record in refinement_sequence]
        assert values == sorted(values), (
            f"deflection should increase monotonically with refinement, got {values}"
        )

    # -- the raw peak stress does not ---------------------------------------
    def test_raw_peak_stress_never_settles(self, refinement_sequence):
        """The singularity at the fixed face. This is the point of the test.

        The true elastic stress at a fully fixed face is unbounded, so the
        computed peak grows with every refinement and has no limit to converge
        on. The software must say so rather than reporting a settled value.
        """
        result = _assess(refinement_sequence, "raw_peak")
        assert result.behaviour is not Behaviour.SETTLING, (
            "raw peak stress at a singularity must never be reported as "
            f"settling; got {result.behaviour.value}"
        )
        assert result.extrapolated is None, (
            "a quantity with no limit must not be given an extrapolated value"
        )
        assert result.uncertainty is None, (
            "a quantity with no limit must not be given an uncertainty band"
        )

    def test_raw_peak_stress_is_reported_as_running_away(self, refinement_sequence):
        result = _assess(refinement_sequence, "raw_peak")
        assert result.behaviour is Behaviour.DIVERGING
        assert result.observed_order is not None and result.observed_order < 0, (
            "a runaway quantity must report a negative rate, not a plausible positive one"
        )

    def test_raw_peak_stress_grows_with_every_refinement(self, refinement_sequence):
        values = [record["raw_peak"] for record in refinement_sequence]
        assert values == sorted(values), (
            f"peak stress at a singularity must climb with refinement, got {values}"
        )
        assert values[-1] > values[0] * (1.0 + RUNAWAY_SPREAD)

    # -- the contrast, which is the whole argument --------------------------
    def test_the_peak_moves_hundreds_of_times_more_than_the_deflection(self, refinement_sequence):
        """The evidence behind not optimising raw peak stress.

        Both numbers come from the same four solves. One is settled to a
        twentieth of a percent; the other moved by twenty percent and is still
        going. An optimiser handed the peak would be searching the mesh rather
        than the design.
        """
        deflection = _assess(refinement_sequence, "displacement")
        peak = _assess(refinement_sequence, "raw_peak")
        assert deflection.spread is not None and peak.spread is not None
        assert deflection.spread > 0, "a zero spread would make this ratio meaningless"

        ratio = peak.spread / deflection.spread
        assert ratio > MINIMUM_SPREAD_RATIO, (
            f"peak stress moved {peak.spread:.2%} and deflection moved "
            f"{deflection.spread:.3%}, a ratio of {ratio:.0f}. Expected at "
            f"least {MINIMUM_SPREAD_RATIO:.0f}. Measured at the time of "
            f"writing: about 340"
        )


@pytest.mark.verification
def test_the_reference_deflection_is_what_the_docstring_claims():
    """Guard the reference itself, so a typo cannot move the goalposts."""
    assert timoshenko_tip_deflection() == pytest.approx(2.880, abs=0.005)
