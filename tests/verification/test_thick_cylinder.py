"""V5 — thick-walled cylinder under internal pressure, against Lame.

This benchmark verifies the **pressure load path**. Until it existed, pressure
loading was covered only by unit tests of the element-face lookup: no test had
ever checked that a pressure applied through ``*DLOAD`` produces the right
stresses in a real part. Every force-loaded case in the suite exercises a
completely different code path.

**Case.** A quarter of a thick-walled cylinder, bore radius 50 mm, outer radius
100 mm, 40 mm tall, carrying 50 MPa of internal pressure. Aluminium-like
properties, E = 70 GPa, nu = 0.33.

Only a quarter is modelled. Restraining movement normal to each cut face
reproduces the whole cylinder at a quarter of the cost, and also removes a real
difficulty: a complete ring under internal pressure is in balance with itself,
so nothing holds it in place and the solve has no unique answer. Restraining
both end faces along the axis makes the model plane strain, which is the
condition Lame's closed-form solution describes.

**Reference.** Lame's equations, with the axial stress that plane strain
implies:

    hoop(r)   = k (1 + b^2/r^2)
    radial(r) = k (1 - b^2/r^2)          k = p a^2 / (b^2 - a^2)
    axial     = nu (hoop + radial)       (constant through the wall)

**Measured agreement**, at a 6 mm mesh, checked at five radii through the wall:

    r =  50.0   FE 114.539   Lame 115.609   -0.93%
    r =  62.5   FE  73.975   Lame  74.118   -0.19%
    r =  75.0   FE  51.590   Lame  51.632   -0.08%
    r =  87.5   FE  38.170   Lame  38.128   +0.11%
    r = 100.0   FE  29.493   Lame  29.418   +0.25%

Radial displacement agrees to -0.33% at the bore and +0.07% at the outer
surface.

**The exact check.** The resultant of the pressure over a quarter bore is
``p * a * h`` = 100 000 N in each of x and y, by the divergence of the
projected area -- no approximation. The measured reactions are -100 001.3 N and
-100 000.8 N, correct to about one part in 100 000. That figure verifies the
pressure magnitude, its direction, and the shape-function integration behind it
in a single number.

**A defect this benchmark found.** OpenOptima used to total the reaction by
adding every component of every restrained set. CalculiX reports a full
``(fx, fy, fz)`` for each set, including the directions that set leaves free,
and those figures are not reactions. Here the x-symmetry set reports its true
fx of -100 001 N together with a spurious fy of +1 560 N. Adding everything gave
a total 1.7% short, and the equilibrium check then reported a 1.7% error on an
analysis that was correct to one part in 100 000 -- telling the user not to
trust a sound result, on every model that uses symmetry. Reactions are now
assembled one direction at a time from the sets that restrain that direction.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

import math

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

INNER, OUTER, HEIGHT = 50.0, 100.0, 40.0
PRESSURE = 50.0
MODULUS, POISSON = 70000.0, 0.33
MESH_SIZE = 6.0

#: Agreement band against Lame through the wall. Worst measured: -0.93% at the
#: bore, where the mesh is coarsest relative to the stress gradient.
STRESS_TOLERANCE = 0.03
#: Radial displacement band. Worst measured: -0.33%.
DISPLACEMENT_TOLERANCE = 0.02
#: The resultant of pressure over the bore is exact arithmetic, so hold it
#: tight. Measured: about 1 part in 100 000.
REACTION_TOLERANCE = 1e-3


def lame_stresses(radius: float) -> tuple[float, float, float]:
    """Hoop, radial and axial stress at a radius, under plane strain."""
    k = PRESSURE * INNER**2 / (OUTER**2 - INNER**2)
    hoop = k * (1.0 + OUTER**2 / radius**2)
    radial = k * (1.0 - OUTER**2 / radius**2)
    axial = POISSON * (hoop + radial)
    return hoop, radial, axial


def lame_von_mises(radius: float) -> float:
    hoop, radial, axial = lame_stresses(radius)
    return math.sqrt(0.5 * ((hoop - radial) ** 2 + (radial - axial) ** 2 + (axial - hoop) ** 2))


def lame_radial_displacement(radius: float) -> float:
    k = PRESSURE * INNER**2 / (OUTER**2 - INNER**2)
    return ((1.0 + POISSON) / MODULUS) * k * ((1.0 - 2.0 * POISSON) * radius + OUTER**2 / radius)


def pressure_resultant() -> float:
    """Force the internal pressure exerts on a quarter bore, in x and in y.

    The bore spans a quarter turn, so its projection onto the plane
    perpendicular to x is exactly a rectangle ``a`` wide and ``h`` tall. The
    resultant is therefore ``p * a * h`` with no approximation, whatever the
    mesh does with the curve.
    """
    return PRESSURE * INNER * HEIGHT


def _regions():
    return (
        SemanticRegion(
            "bore_surface",
            RegionSelector(
                surface_type=SurfaceType.CYLINDER,
                min_radius=0.9 * INNER,
                max_radius=1.1 * INNER,
            ),
        ),
        SemanticRegion(
            "outer_surface",
            RegionSelector(
                surface_type=SurfaceType.CYLINDER,
                min_radius=0.9 * OUTER,
                max_radius=1.1 * OUTER,
            ),
        ),
        SemanticRegion(
            "symmetry_x",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(-1.0, 0.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
        SemanticRegion(
            "symmetry_y",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(0.0, -1.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
        SemanticRegion(
            "bottom_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(0.0, 0.0, -1.0),
                normal_tolerance_deg=2.0,
            ),
        ),
        SemanticRegion(
            "top_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(0.0, 0.0, 1.0),
                normal_tolerance_deg=2.0,
            ),
        ),
    )


@pytest.fixture(scope="module")
def cylinder_solution(tmp_path_factory):
    directory = tmp_path_factory.mktemp("thick_cylinder")

    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="thick_cylinder",
            parameters={
                "inner_radius": INNER,
                "outer_radius": OUTER,
                "height": HEIGHT,
            },
        )
    )
    space = DesignSpace(
        (DesignVariable(id="height", minimum=HEIGHT, maximum=HEIGHT, default=HEIGHT),)
    )
    geometry = provider.build(space.defaults(), directory / "geometry")

    mesher = GmshMesher(
        MeshSpecification(global_size=MESH_SIZE, minimum_size=MESH_SIZE / 3.0, element_order=2)
    )
    mesh, region_map = mesher.generate(geometry, _regions(), directory / "mesh")

    model = AnalysisModel(
        name="thick cylinder verification",
        material=Material.from_engineering_units(
            name="Aluminium",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=2700.0,
            allowable_stress_mpa=200.0,
        ),
        load_cases=(
            LoadCase(
                id="internal_pressure",
                # Symmetry on the two cut faces, axial restraint on both ends.
                # Together these make the quarter model behave exactly as the
                # whole cylinder under plane strain.
                boundary_conditions=(
                    BoundaryCondition(region="symmetry_x", dofs=(1,)),
                    BoundaryCondition(region="symmetry_y", dofs=(2,)),
                    BoundaryCondition(region="bottom_face", dofs=(3,)),
                    BoundaryCondition(region="top_face", dofs=(3,)),
                ),
                loads=(
                    Load(
                        kind=LoadKind.PRESSURE,
                        region="bore_surface",
                        magnitude=PRESSURE,
                    ),
                ),
            ),
        ),
        stress_evaluation=StressEvaluation(measure="percentile", percentile=99.0),
    )

    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=1800))
    results = solver.solve(model, mesh, directory / "solver")
    fields = results.by_id("internal_pressure")

    coordinates = np.array([mesh.coordinates[mesh.index_of(int(tag))] for tag in fields.node_tags])
    radius = np.hypot(coordinates[:, 0], coordinates[:, 1])
    return fields, radius, mesh, geometry, region_map, results


def _mean_in_band(values, radius, target: float, width: float = 1.5) -> float:
    mask = np.abs(radius - target) < width
    assert mask.sum() > 20, f"too few nodes near r={target} to average meaningfully"
    return float(values[mask].mean())


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestThickCylinderAgainstLame:
    def test_the_geometry_is_a_quarter_annulus(self, cylinder_solution):
        _fields, _radius, _mesh, geometry, _regions_map, _results = cylinder_solution
        exact = 0.25 * math.pi * (OUTER**2 - INNER**2) * HEIGHT
        assert geometry.volume == pytest.approx(exact, rel=1e-9)

    def test_the_bore_and_outer_surfaces_resolved_uniquely(self, cylinder_solution):
        """The bore is a 90 degree arc, which is the case that broke radius
        fitting once before: fitting from the sampled centroid works only on a
        full cylinder."""
        _fields, _radius, _mesh, _geometry, region_map, _results = cylinder_solution
        assert len(region_map["bore_surface"].face_tags) == 1
        assert len(region_map["outer_surface"].face_tags) == 1
        assert region_map["bore_surface"].total_area == pytest.approx(
            0.25 * 2.0 * math.pi * INNER * HEIGHT, rel=1e-6
        )
        assert region_map["outer_surface"].total_area == pytest.approx(
            0.25 * 2.0 * math.pi * OUTER * HEIGHT, rel=1e-6
        )

    # -- the exact check -----------------------------------------------------
    def test_reaction_balances_the_pressure_resultant(self, cylinder_solution):
        """The strongest single check on the pressure load path.

        The resultant of pressure over a quarter bore is ``p * a * h`` exactly.
        Matching it verifies the pressure magnitude, its direction and the
        shape-function integration behind it, all at once.
        """
        fields, _radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        expected = pressure_resultant()
        assert fields.reaction_force[0] == pytest.approx(-expected, rel=REACTION_TOLERANCE)
        assert fields.reaction_force[1] == pytest.approx(-expected, rel=REACTION_TOLERANCE)

    def test_no_net_axial_reaction(self, cylinder_solution):
        """Internal pressure on the bore has no axial component, so the two end
        restraints must cancel."""
        fields, _radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        assert abs(fields.reaction_force[2]) < pressure_resultant() * REACTION_TOLERANCE

    def test_the_equilibrium_check_does_not_cry_wolf(self, cylinder_solution):
        """Regression test for the reaction-assembly defect.

        This model is correct to about one part in 100 000. Before reactions
        were assembled one direction at a time, the check reported a 1.7% error
        on it and told the user not to proceed. A false alarm on a sound model
        is not a harmless warning: it trains people to ignore the check that
        exists to catch a load on the wrong face.
        """
        _f, _r, _m, _g, _rm, results = cylinder_solution
        equilibrium_warnings = [
            warning for warning in results.warnings if "equilibrium" in warning.lower()
        ]
        assert not equilibrium_warnings, equilibrium_warnings

    # -- through the wall ----------------------------------------------------
    @pytest.mark.parametrize("target", [INNER, 62.5, 75.0, 87.5, OUTER])
    def test_von_mises_matches_lame_through_the_wall(self, cylinder_solution, target):
        """Checked at five radii, not just the peak.

        A single-point check can pass on a stress field that is wrong
        everywhere else. The profile through the wall is the real test of
        whether the pressure produced the right internal state.
        """
        fields, radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        measured = _mean_in_band(fields.von_mises, radius, target)
        reference = lame_von_mises(target)
        error = (measured - reference) / reference
        assert abs(error) < STRESS_TOLERANCE, (
            f"von Mises at r={target:g} is {measured:.3f} MPa against Lame's "
            f"{reference:.3f} MPa, an error of {error:+.2%}, outside the "
            f"{STRESS_TOLERANCE:.0%} band"
        )

    def test_stress_falls_from_bore_to_outer_surface(self, cylinder_solution):
        """Lame says the wall is worked hardest at the bore. If the pressure had
        landed on the outer surface instead, this ordering would reverse -- and
        the magnitudes alone would not necessarily give it away."""
        fields, radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        profile = [
            _mean_in_band(fields.von_mises, radius, target)
            for target in (INNER, 62.5, 75.0, 87.5, OUTER)
        ]
        assert profile == sorted(profile, reverse=True), (
            f"stress must fall monotonically outwards, got {profile}"
        )

    @pytest.mark.parametrize("target", [INNER, OUTER])
    def test_radial_displacement_matches_lame(self, cylinder_solution, target):
        fields, radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        measured = _mean_in_band(fields.displacement_magnitude, radius, target)
        reference = lame_radial_displacement(target)
        error = (measured - reference) / reference
        assert abs(error) < DISPLACEMENT_TOLERANCE, (
            f"radial displacement at r={target:g} is {measured:.6f} mm against "
            f"Lame's {reference:.6f} mm, an error of {error:+.2%}"
        )

    def test_the_bore_expands_more_than_the_outer_surface(self, cylinder_solution):
        fields, radius, _mesh, _geometry, _regions_map, _results = cylinder_solution
        bore = _mean_in_band(fields.displacement_magnitude, radius, INNER)
        outer = _mean_in_band(fields.displacement_magnitude, radius, OUTER)
        assert bore > outer


@pytest.mark.verification
class TestLameReferenceValues:
    """Guard the reference itself, so a typo cannot move the goalposts."""

    def test_radial_stress_equals_minus_the_pressure_at_the_bore(self):
        """A boundary condition of the exact solution, not a fitted number."""
        _hoop, radial, _axial = lame_stresses(INNER)
        assert radial == pytest.approx(-PRESSURE, rel=1e-12)

    def test_radial_stress_vanishes_at_a_free_outer_surface(self):
        _hoop, radial, _axial = lame_stresses(OUTER)
        assert radial == pytest.approx(0.0, abs=1e-9)

    def test_hoop_stress_matches_the_closed_form_at_the_bore(self):
        hoop, _radial, _axial = lame_stresses(INNER)
        expected = PRESSURE * (OUTER**2 + INNER**2) / (OUTER**2 - INNER**2)
        assert hoop == pytest.approx(expected, rel=1e-12)
        assert hoop == pytest.approx(83.3333, abs=1e-3)

    def test_the_quoted_von_mises_values_are_what_the_docstring_claims(self):
        assert lame_von_mises(INNER) == pytest.approx(115.609, abs=1e-3)
        assert lame_von_mises(OUTER) == pytest.approx(29.418, abs=1e-3)

    def test_the_quoted_displacements_are_what_the_docstring_claims(self):
        assert lame_radial_displacement(INNER) == pytest.approx(0.068717, abs=1e-6)
        assert lame_radial_displacement(OUTER) == pytest.approx(0.042433, abs=1e-6)

    def test_the_pressure_resultant_is_the_projected_area_times_pressure(self):
        assert pressure_resultant() == pytest.approx(100_000.0)
