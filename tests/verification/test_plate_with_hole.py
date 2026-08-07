"""V4 — plate with a central hole, against Howland's stress concentration.

This benchmark checks a **real** stress concentration: a feature with a finite
radius, where the peak stress is a genuine physical number. V6 checks the
opposite case, a mathematical singularity at a fully fixed face, where no such
number exists. Together they draw the line the whole stress-measure argument
rests on, and V4 is the half that had never been tested.

**Case.** 300 x 100 x 5 mm plate, 20 mm central hole, pulled with 50 kN along
its length. Aluminium, E = 70 GPa, nu = 0.33. The plate is three times its own
width long, so the fixed end is far enough away not to disturb the hole.

**Reference.** Howland's solution for a finite-width plate, in Heywood's
closed form:

    Kt_net = 2 + (1 - d/W)^3

At d/W = 0.2 that is 2.5120 against the net-section stress, equivalently
3.1400 against the gross section -- and Howland's tabulated value at this
ratio is 3.14. With a net stress of 125 MPa the peak should be 314.0 MPa.

**Measured**, at four mesh densities from 3.15 mm down to 1.89 mm average
element size (4 733 to 21 832 elements):

    global 8.0 / hole 4.0    309.48 MPa   -1.44%
    global 6.0 / hole 2.5    319.66 MPa   +1.80%
    global 5.0 / hole 1.6    316.11 MPa   +0.67%
    global 4.0 / hole 1.0    316.76 MPa   +0.88%

**On the scatter.** These do not settle monotonically; they wobble inside a
3.2% band. That is expected and is not the same thing as the V6 singularity
running away. The reported peak is whichever *node* happens to land nearest
the hottest point on the hole, and remeshing moves the nodes around the arc,
so the sampled value moves with them. What matters is that the band is bounded
and stays put: 3.2% across a fourfold change in element count, against 19.8%
and accelerating for the singularity in V6. A real concentration has a real
answer and the mesh scatters around it; a singularity has no answer and the
mesh climbs away from it.

That contrast is the measured evidence for reporting a percentile rather than
the raw peak by default, and for the advice that a genuine fillet should be
modelled and refined rather than hidden behind a percentile.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
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
    LocalRefinement,
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

LENGTH, WIDTH, THICKNESS = 300.0, 100.0, 5.0
HOLE = 20.0
FORCE = 50000.0
MODULUS, POISSON = 70000.0, 0.33

#: Global and near-hole element sizes. These produced the measured values in
#: the docstring; changing them invalidates the tolerances.
MESH_LEVELS = ((8.0, 4.0), (6.0, 2.5), (5.0, 1.6), (4.0, 1.0))

#: Agreement band against Howland. Worst measured: +1.80%.
KT_TOLERANCE = 0.04
#: The peak must stay inside a bounded band under refinement. Measured: 3.2%.
#: V6's singularity moves 19.8% on the same kind of sweep and is still going.
BOUNDED_SPREAD = 0.08
#: Equilibrium is exact arithmetic.
EQUILIBRIUM_TOLERANCE = 1e-4


def gross_stress() -> float:
    return FORCE / (WIDTH * THICKNESS)


def net_stress() -> float:
    """Stress on the reduced section through the hole."""
    return FORCE / ((WIDTH - HOLE) * THICKNESS)


def kt_net() -> float:
    """Howland's concentration factor, in Heywood's closed form."""
    return 2.0 + (1.0 - HOLE / WIDTH) ** 3


def kt_gross() -> float:
    return kt_net() / (1.0 - HOLE / WIDTH)


def peak_reference() -> float:
    return kt_net() * net_stress()


def _solve(global_size: float, hole_size: float, directory):
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="plate_with_hole",
            parameters={
                "length": LENGTH,
                "width": WIDTH,
                "thickness": THICKNESS,
                "hole_diameter": HOLE,
            },
        )
    )
    space = DesignSpace((DesignVariable(id="width", minimum=WIDTH, maximum=WIDTH, default=WIDTH),))
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
        SemanticRegion(
            "hole_surface",
            RegionSelector(
                surface_type=SurfaceType.CYLINDER,
                min_radius=0.4 * HOLE,
                max_radius=0.6 * HOLE,
            ),
        ),
    )
    mesher = GmshMesher(
        MeshSpecification(
            global_size=global_size,
            minimum_size=hole_size / 3.0,
            element_order=2,
            size_from_thickness=False,
            local_refinements=(
                LocalRefinement(region="hole_surface", size=hole_size, distance=HOLE),
            ),
            max_elements=4_000_000,
        )
    )
    mesh, region_map = mesher.generate(geometry, regions, directory / "mesh")

    model = AnalysisModel(
        name="plate with hole",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=400.0,
        ),
        load_cases=(
            LoadCase(
                id="tension",
                boundary_conditions=(BoundaryCondition(region="fixed_face"),),
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(FORCE, 0.0, 0.0)),),
            ),
        ),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=3600))
    fields = solver.solve(model, mesh, directory / "solver").by_id("tension")

    coordinates = np.array([mesh.coordinates[mesh.index_of(int(tag))] for tag in fields.node_tags])
    radial = np.hypot(coordinates[:, 0] - LENGTH / 2.0, coordinates[:, 1] - WIDTH / 2.0)
    # Within 5 mm of the hole edge. Deliberately not the global peak: the
    # fully fixed end has a singularity of its own, and that is V6's subject.
    near_hole = radial < 0.75 * HOLE

    return {
        "global_size": global_size,
        "hole_size": hole_size,
        "elements": mesh.quality.element_count,
        "peak_at_hole": float(fields.von_mises[near_hole].max()),
        "reaction_x": fields.reaction_force[0],
        "hole_area": region_map["hole_surface"].total_area,
        "element_type": mesh.element_type,
    }


@pytest.fixture(scope="module")
def plate_sequence(tmp_path_factory):
    root = tmp_path_factory.mktemp("plate_with_hole")
    return [
        _solve(global_size, hole_size, root / f"level{index}")
        for index, (global_size, hole_size) in enumerate(MESH_LEVELS)
    ]


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestPlateWithHoleAgainstHowland:
    def test_the_hole_was_found_and_meshed(self, plate_sequence):
        expected_area = np.pi * HOLE * THICKNESS
        for record in plate_sequence:
            assert record["hole_area"] == pytest.approx(expected_area, rel=1e-3)
            assert record["element_type"] == "C3D10"

    def test_equilibrium_holds_at_every_mesh(self, plate_sequence):
        for record in plate_sequence:
            assert record["reaction_x"] == pytest.approx(-FORCE, rel=EQUILIBRIUM_TOLERANCE)

    @pytest.mark.parametrize("index", range(len(MESH_LEVELS)))
    def test_peak_stress_matches_howland(self, plate_sequence, index):
        record = plate_sequence[index]
        measured = record["peak_at_hole"]
        error = (measured - peak_reference()) / peak_reference()
        assert abs(error) < KT_TOLERANCE, (
            f"peak at the hole is {measured:.2f} MPa against Howland's "
            f"{peak_reference():.2f} MPa, an error of {error:+.2%}, outside the "
            f"{KT_TOLERANCE:.0%} band"
        )

    def test_the_peak_stays_bounded_under_refinement(self, plate_sequence):
        """The half of the argument V6 cannot make.

        A real concentration has a real answer, and refining scatters the
        sampled peak around it. A singularity has no answer, and refining
        climbs away from it without limit. This asserts the first behaviour;
        V6 asserts the second on the same kind of sweep.
        """
        peaks = [record["peak_at_hole"] for record in plate_sequence]
        spread = (max(peaks) - min(peaks)) / max(peaks)
        assert spread < BOUNDED_SPREAD, (
            f"peak stress at the hole moved {spread:.2%} across the mesh sweep "
            f"({peaks}), which is not the bounded scatter a real stress "
            f"concentration should show"
        )

    def test_the_finest_mesh_does_not_run_away_from_the_coarsest(self, plate_sequence):
        """Guards the contrast with V6 directly.

        There, every refinement pushed the peak further up and the steps grew.
        Here the finest mesh must sit close to the coarsest, not far above it.
        """
        peaks = [record["peak_at_hole"] for record in plate_sequence]
        assert peaks[-1] < peaks[0] * 1.10


@pytest.mark.verification
class TestHowlandReferenceValues:
    """Guard the reference itself, so a typo cannot move the goalposts."""

    def test_the_infinite_plate_limit_is_three(self):
        """As the hole shrinks against the width, Kt must approach the classical
        value of 3 for a hole in an infinite plate."""
        assert pytest.approx(3.0) == 2.0 + (1.0 - 0.0) ** 3

    def test_kt_matches_howlands_tabulated_value_at_this_ratio(self):
        assert pytest.approx(0.2) == HOLE / WIDTH
        assert kt_gross() == pytest.approx(3.14, abs=0.005)
        assert kt_net() == pytest.approx(2.5120, abs=1e-4)

    def test_the_quoted_stresses_are_what_the_docstring_claims(self):
        assert gross_stress() == pytest.approx(100.0)
        assert net_stress() == pytest.approx(125.0)
        assert peak_reference() == pytest.approx(314.0, abs=0.05)

    def test_net_and_gross_definitions_agree(self):
        """The same peak, whichever nominal stress it is expressed against."""
        assert kt_net() * net_stress() == pytest.approx(kt_gross() * gross_stress())
