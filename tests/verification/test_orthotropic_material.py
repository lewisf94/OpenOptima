"""V10 — an orthotropic material really is directional.

A 3D-printed part is weaker between its layers than along them. Until this
landed, OpenOptima assumed one stiffness in every direction, which meant a
printed part could be reported as safe and then peel apart along its layers.

**The test.** The same bar, the same mesh, the same load — pulled once along
the print layers and once through them. Only the build direction changes.

That comparison is the strongest available, because the ratio of the two
answers is the ratio of the two moduli **exactly**, and it does not depend on
the load, the length, the section or the mesh. Any of those cancelling
incorrectly would show up immediately.

**Case.** 200 x 20 x 20 mm bar, 5 kN axial tension. Print-like material:
3500 MPa in the layer plane, 2100 MPa through the layers, so the expected
ratio is 3500/2100 = 1.6667.

**Measured:**

    pulled along the layers      0.712080 mm   hand calculation 0.714286   -0.31%
    pulled through the layers    1.188450 mm   hand calculation 1.190476   -0.17%
    ratio                        1.668984      expected         1.666667   +0.14%

Reaction equals the applied 5 kN exactly in both.

**What is deliberately not claimed here.** This verifies the *stiffness* is
directional. It says nothing about strength: von Mises assumes equal strength
in every direction and is the wrong failure measure for this material. That is
why an orthotropic material without directional strengths refuses to report a
factor of safety rather than reporting a misleading one. See
`docs/engineering-assumptions.md`.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

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
from openoptima.domain.orthotropic import OrthotropicMaterial
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, SIDE = 200.0, 20.0
FORCE = 5000.0
IN_PLANE, THROUGH = 3500.0, 2100.0
IN_PLANE_POISSON, THROUGH_POISSON = 0.36, 0.30

#: Agreement with the hand calculation. Worst measured: -0.31%.
STRETCH_TOLERANCE = 0.02
#: The ratio is exact in theory and cancels almost everything, so hold it
#: tighter. Measured: +0.14%.
RATIO_TOLERANCE = 0.01
EQUILIBRIUM_TOLERANCE = 1e-4


def hand_calculation(modulus: float) -> float:
    """Simple extension of a bar: stretch = F L / (A E)."""
    return FORCE * LENGTH / (SIDE * SIDE * modulus)


def _print_material(build_direction: tuple[float, float, float]) -> OrthotropicMaterial:
    return OrthotropicMaterial.transversely_isotropic(
        name=f"print_{build_direction}",
        in_plane_modulus_mpa=IN_PLANE,
        through_layer_modulus_mpa=THROUGH,
        in_plane_poisson=IN_PLANE_POISSON,
        through_layer_poisson=THROUGH_POISSON,
        through_layer_shear_mpa=900.0,
        density_kg_m3=1240.0,
        build_direction=build_direction,
    )


def _solve(material, directory):
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="cantilever_box",
            parameters={"length": LENGTH, "width": SIDE, "height": SIDE},
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
    mesher = GmshMesher(MeshSpecification(global_size=8.0, minimum_size=3.0, element_order=2))
    mesh, _region_map = mesher.generate(geometry, regions, directory / "mesh")

    model = AnalysisModel(
        name="orthotropic bar",
        material=material,
        load_cases=(
            LoadCase(
                id="axial",
                boundary_conditions=(BoundaryCondition(region="fixed_face"),),
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(FORCE, 0.0, 0.0)),),
            ),
        ),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    fields = solver.solve(model, mesh, directory / "solver").by_id("axial")
    return {
        "stretch": float(fields.displacement[:, 0].max()),
        "reaction_x": fields.reaction_force[0],
    }


@pytest.fixture(scope="module")
def bars(tmp_path_factory):
    root = tmp_path_factory.mktemp("orthotropic")
    return {
        # Layers stacked in z, so the bar's long axis lies *in* the layer
        # plane and sees the stiff in-plane modulus.
        "along": _solve(_print_material((0.0, 0.0, 1.0)), root / "along"),
        # Layers stacked in x, so the bar is pulled *through* the layers.
        "through": _solve(_print_material((1.0, 0.0, 0.0)), root / "through"),
        "isotropic": _solve(
            Material.from_engineering_units(
                name="isotropic reference",
                elastic_modulus_mpa=IN_PLANE,
                poisson_ratio=IN_PLANE_POISSON,
                density_kg_m3=1240.0,
                allowable_stress_mpa=40.0,
            ),
            root / "isotropic",
        ),
    }


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestOrthotropicMaterial:
    def test_pulling_along_the_layers_matches_the_hand_calculation(self, bars):
        measured = bars["along"]["stretch"]
        expected = hand_calculation(IN_PLANE)
        error = (measured - expected) / expected
        assert abs(error) < STRETCH_TOLERANCE, (
            f"stretch along the layers is {measured:.6f} mm against a hand "
            f"calculation of {expected:.6f} mm, an error of {error:+.2%}"
        )

    def test_pulling_through_the_layers_matches_the_hand_calculation(self, bars):
        measured = bars["through"]["stretch"]
        expected = hand_calculation(THROUGH)
        error = (measured - expected) / expected
        assert abs(error) < STRETCH_TOLERANCE, (
            f"stretch through the layers is {measured:.6f} mm against a hand "
            f"calculation of {expected:.6f} mm, an error of {error:+.2%}"
        )

    def test_the_ratio_of_the_two_is_the_ratio_of_the_moduli(self, bars):
        """The decisive check, and the tightest.

        The load, the length, the section and the mesh are identical in both
        runs, so they cancel exactly and only the modulus ratio is left. This
        would fail immediately if the orientation were ignored, applied in the
        wrong direction, or applied to only one of the two runs.
        """
        ratio = bars["through"]["stretch"] / bars["along"]["stretch"]
        expected = IN_PLANE / THROUGH
        error = (ratio - expected) / expected
        assert abs(error) < RATIO_TOLERANCE, (
            f"the bar is {ratio:.4f} times softer through the layers than "
            f"along them, but the moduli say it should be {expected:.4f} "
            f"({error:+.2%}). The build direction is not being applied."
        )

    def test_the_part_really_is_softer_through_the_layers(self, bars):
        """Stated on its own because the sign is what matters for safety.

        If this ever inverted, a printed part would be reported as *stronger*
        in its weakest direction.
        """
        assert bars["through"]["stretch"] > bars["along"]["stretch"]

    def test_along_the_layers_is_close_to_the_isotropic_reference(self, bars):
        """Loading in the stiff plane should behave much like an isotropic
        material of the same modulus.

        Not identical: the through-layer Poisson ratio differs, so lateral
        contraction differs slightly and stiffens the bar by a fraction of a
        per cent. A large gap here would mean the in-plane constants are not
        being applied where they should be.
        """
        ratio = bars["along"]["stretch"] / bars["isotropic"]["stretch"]
        assert ratio == pytest.approx(1.0, abs=0.02)

    @pytest.mark.parametrize("case", ["along", "through", "isotropic"])
    def test_equilibrium_holds_for_every_material(self, bars, case):
        assert bars[case]["reaction_x"] == pytest.approx(-FORCE, rel=EQUILIBRIUM_TOLERANCE)


@pytest.mark.verification
class TestOrthotropicReferenceValues:
    """Guard the reference itself, so a typo cannot move the goalposts."""

    def test_the_hand_calculation_is_what_the_docstring_claims(self):
        assert hand_calculation(IN_PLANE) == pytest.approx(0.714286, abs=1e-6)
        assert hand_calculation(THROUGH) == pytest.approx(1.190476, abs=1e-6)

    def test_the_expected_ratio_is_the_modulus_ratio(self):
        assert pytest.approx(1.666667, abs=1e-6) == IN_PLANE / THROUGH
        assert hand_calculation(THROUGH) / hand_calculation(IN_PLANE) == pytest.approx(
            IN_PLANE / THROUGH, rel=1e-12
        )
