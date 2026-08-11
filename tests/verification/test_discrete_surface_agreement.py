"""V13 — the same part, two ways in, and the same answer out.

A shape can reach the analysis by two routes.  The ordinary one is a CAD model,
which knows that this face *is* a plane and that one *is* a 6 mm hole, because
it was built that way.  The other is a bag of triangles -- what a topology
optimisation hands back -- where nothing is known and every face has to be
measured.

That second route exists so a topology result can be analysed at all.  It is
worth nothing unless it gives the same answer, so this is the benchmark that
says whether it does.

**The case.** A 60 x 20 x 4 mm bar, steel, fixed along one end and pulled down
with 1 kN spread over the other.  It is meshed and solved twice: once from a
BREP file through OpenCASCADE, and once from an STL of the same bar.  Nothing
else differs -- same selectors, same mesh settings, same solver, same material.

**Measured at the time of writing**, with the triangles written at three
different finenesses:

===================  ==============  ==============  ==============
Compared with CAD    4 mm triangles  2 mm triangles  1 mm triangles
===================  ==============  ==============  ==============
Volume                     0.000%          0.000%          0.000%
Deflection                -0.004%         -0.006%         -0.008%
Stored energy             -0.003%         -0.005%         -0.007%
Peak stress               +1.691%         +1.832%         +0.233%
===================  ==============  ==============  ==============

Deflection and stored energy agree to under a hundredth of a per cent.  That is
the important pair: they are averages over the whole part, so they say the two
routes built the same structure and loaded it the same way.

**Stress agrees less closely, and that is expected rather than tolerated.**  The
figure is a high percentile of a field that peaks where the bar is held, and
the two runs put their mesh points in different places, so they sample that peak
slightly differently.  It is not a sign that one of them is wrong.  Note also
that the error does not fall steadily as the triangles get finer, which is what
sampling noise looks like; a real error in the triangle route would grow or
shrink with the faceting.

**A hole must survive the trip too**, or a selector written for a bolt hole
stops matching once the part comes back from a topology run.  Measured on a
3.000 mm hole: the radius comes back as **3.0000 mm**.  Fitting the middles of
the triangles instead of their corner points gives 2.967 mm, and that 1.1 per
cent error is enough to pick the wrong hole.

**What this route cannot do**, stated because it is a real limit and not a
tolerance: a rounded blend between two faces cannot be found at all.  A blend
runs smoothly into the faces it joins, so there is no crease in the triangles to
find it by, and it is measured as part of its neighbours.  A selector that asks
for a blend by its radius will not match on a shape made of triangles.

Recorded as V13 in ``docs/verification-plan.md``.
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
from openoptima.domain.regions import (
    BoundingBox,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.geometry.base import GeometryArtifact, SurfaceArtifact
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.regions.discrete import measure_discrete_surface
from openoptima.results.metrics import collect_metrics
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, DEPTH, THICKNESS = 60.0, 20.0, 4.0
FORCE = 1000.0

#: How closely the two routes must agree on deflection and stored energy. These
#: are whole-part figures, so anything worse than this means the triangle route
#: built a different structure. Measured: under 0.01%.
STRUCTURAL_TOLERANCE = 0.002

#: Peak stress is a sample of a field at its worst point, and the two runs place
#: their mesh points differently. Measured: 1.8% at worst. See the docstring --
#: this is sampling, not error, and the band is not to be widened to rescue a
#: build.
STRESS_TOLERANCE = 0.04

MATERIAL = Material(
    name="steel",
    elastic_modulus=210000.0,
    poisson_ratio=0.3,
    density=7.85e-9,
    allowable_stress=250.0,
)

REGIONS = (
    SemanticRegion(
        "fixed_end",
        RegionSelector(
            surface_type=SurfaceType.PLANE,
            normal=(-1.0, 0.0, 0.0),
            within_box=BoundingBox(-0.1, -1.0, -1.0, 0.1, DEPTH + 1, THICKNESS + 1),
            mode=SelectionMode.ALL,
        ),
    ),
    SemanticRegion(
        "loaded_end",
        RegionSelector(
            surface_type=SurfaceType.PLANE,
            normal=(1.0, 0.0, 0.0),
            within_box=BoundingBox(
                LENGTH - 0.1, -1.0, -1.0, LENGTH + 0.1, DEPTH + 1, THICKNESS + 1
            ),
            mode=SelectionMode.ALL,
        ),
    ),
)

MESH = MeshSpecification(
    global_size=2.0,
    minimum_size=1.0,
    element_order=2,
    curvature_refinement=False,
    size_from_thickness=False,
)

MODEL = AnalysisModel(
    name="bar",
    material=MATERIAL,
    load_cases=(
        LoadCase(
            id="tip",
            loads=(Load(kind=LoadKind.FORCE, region="loaded_end", vector=(0.0, -FORCE, 0.0)),),
            boundary_conditions=(BoundaryCondition(region="fixed_end"),),
        ),
    ),
    stress_evaluation=StressEvaluation(),
    element_order=2,
)


def write_bar(directory, triangle_size: float):
    """The same bar as a BREP file and as an STL, from one OpenCASCADE build."""
    brep = directory / "bar.brep"
    stl = directory / f"bar_{triangle_size:g}.stl"
    with gmsh_session() as gmsh:
        gmsh.model.add("bar")
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, LENGTH, DEPTH, THICKNESS)
        gmsh.model.occ.synchronize()
        gmsh.write(str(brep))
        gmsh.option.setNumber("Mesh.MeshSizeMax", triangle_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", triangle_size)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(stl))
    return brep, stl


def measure(stl, note: str = "triangles") -> SurfaceArtifact:
    """Work out the faces of a triangle mesh, the way the mesher does."""
    with gmsh_session() as gmsh:
        gmsh.model.add("surface")
        gmsh.merge(str(stl))
        gmsh.model.mesh.classifySurfaces(math.radians(30.0), True, True, math.radians(180.0))
        surface = measure_discrete_surface(gmsh)
    return SurfaceArtifact(
        stl_path=stl,
        volume=surface.volume,
        bbox=surface.bbox,
        surface_area=surface.surface_area,
        source_description=note,
    )


def solve(directory, mesh, region_map, reference_volume):
    solver = CalculiXSolver(SolverSpecification())
    analysis = solver.solve(MODEL, mesh, directory)
    metrics, _cases, _warnings = collect_metrics(
        analysis, MODEL, mesh, region_map, reference_volume
    )
    return metrics


@pytest.fixture(scope="module")
def from_cad(tmp_path_factory):
    directory = tmp_path_factory.mktemp("v13_cad")
    brep, _stl = write_bar(directory, 2.0)
    artifact = GeometryArtifact(
        brep_path=brep,
        step_path=None,
        volume=LENGTH * DEPTH * THICKNESS,
        bbox=BoundingBox(0.0, 0.0, 0.0, LENGTH, DEPTH, THICKNESS),
        solid_count=1,
        centre_of_mass=(LENGTH / 2, DEPTH / 2, THICKNESS / 2),
        surface_area=2 * (LENGTH * DEPTH + LENGTH * THICKNESS + DEPTH * THICKNESS),
    )
    mesh, region_map = GmshMesher(MESH).generate(artifact, REGIONS, directory / "mesh")
    return solve(directory / "solve", mesh, region_map, artifact.volume)


@pytest.fixture(scope="module")
def from_triangles(tmp_path_factory):
    """The same bar, at three finenesses of triangle."""
    directory = tmp_path_factory.mktemp("v13_stl")
    results = {}
    for size in (4.0, 2.0, 1.0):
        _brep, stl = write_bar(directory, size)
        artifact = measure(stl)
        mesh, region_map = GmshMesher(MESH).generate_from_surface(
            artifact, REGIONS, directory / f"mesh_{size:g}"
        )
        results[size] = (
            artifact,
            solve(directory / f"solve_{size:g}", mesh, region_map, artifact.volume),
        )
    return results


@requires_gmsh
@requires_calculix
@pytest.mark.verification
class TestTheTwoRoutesAgree:
    def test_the_faces_of_a_box_are_found_from_its_triangles_alone(self, from_triangles):
        for size, (artifact, _metrics) in from_triangles.items():
            with gmsh_session() as gmsh:
                gmsh.model.add("check")
                gmsh.merge(str(artifact.stl_path))
                gmsh.model.mesh.classifySurfaces(
                    math.radians(30.0), True, True, math.radians(180.0)
                )
                surface = measure_discrete_surface(gmsh)
            assert len(surface.signatures) == 6, (
                f"a box has six faces; measuring {size} mm triangles found "
                f"{len(surface.signatures)}"
            )
            assert all(s.surface_type is SurfaceType.PLANE for s in surface.signatures)

    def test_the_volume_is_exact(self, from_triangles):
        """Flat faces are flat, so there is no faceting error to allow for."""
        exact = LENGTH * DEPTH * THICKNESS
        for size, (artifact, _metrics) in from_triangles.items():
            assert artifact.volume == pytest.approx(exact, rel=1e-9), f"{size} mm triangles"

    def test_the_part_bends_by_the_same_amount(self, from_cad, from_triangles):
        for size, (_artifact, metrics) in from_triangles.items():
            assert metrics["displacement_max_mm"] == pytest.approx(
                from_cad["displacement_max_mm"], rel=STRUCTURAL_TOLERANCE
            ), f"{size} mm triangles"

    def test_the_part_stores_the_same_energy(self, from_cad, from_triangles):
        """The strongest check of the pair: it uses the whole displacement field."""
        for size, (_artifact, metrics) in from_triangles.items():
            assert metrics["strain_energy_mj"] == pytest.approx(
                from_cad["strain_energy_mj"], rel=STRUCTURAL_TOLERANCE
            ), f"{size} mm triangles"

    def test_the_stress_agrees_within_the_sampling_band(self, from_cad, from_triangles):
        for size, (_artifact, metrics) in from_triangles.items():
            assert metrics["stress_max_mpa"] == pytest.approx(
                from_cad["stress_max_mpa"], rel=STRESS_TOLERANCE
            ), f"{size} mm triangles"

    def test_the_factor_of_safety_agrees(self, from_cad, from_triangles):
        """The number an engineer would act on."""
        for size, (_artifact, metrics) in from_triangles.items():
            assert metrics["factor_of_safety"] == pytest.approx(
                from_cad["factor_of_safety"], rel=STRESS_TOLERANCE
            ), f"{size} mm triangles"


#: The hole drilled through the plate below.
HOLE_RADIUS = 3.0


@pytest.fixture(scope="module")
def plate_with_hole(tmp_path_factory):
    directory = tmp_path_factory.mktemp("v13_hole")
    stl = directory / "plate.stl"
    with gmsh_session() as gmsh:
        gmsh.model.add("plate")
        box = gmsh.model.occ.addBox(0.0, 0.0, 0.0, 40.0, 20.0, 5.0)
        drill = gmsh.model.occ.addCylinder(10.0, 10.0, -1.0, 0.0, 0.0, 7.0, HOLE_RADIUS)
        gmsh.model.occ.cut([(3, box)], [(3, drill)])
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 1.2)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(stl))
    with gmsh_session() as gmsh:
        gmsh.model.add("measure")
        gmsh.merge(str(stl))
        gmsh.model.mesh.classifySurfaces(math.radians(30.0), True, True, math.radians(180.0))
        return measure_discrete_surface(gmsh)


@requires_gmsh
@pytest.mark.verification
class TestAHoleSurvivesTheTrip:
    """A bolt hole has to still be findable, or the selectors for it stop working."""

    def test_the_plate_has_exactly_the_seven_faces_cad_would_report(self, plate_with_hole):
        assert len(plate_with_hole.signatures) == 7

    def test_the_hole_is_found_and_its_size_is_right(self, plate_with_hole):
        """3.0000 mm measured. Fitting triangle middles instead gives 2.967 mm."""
        holes = [s for s in plate_with_hole.signatures if s.surface_type is SurfaceType.CYLINDER]
        assert len(holes) == 1
        assert holes[0].radius == pytest.approx(HOLE_RADIUS, rel=1e-3)
        assert abs(holes[0].axis[2]) == pytest.approx(1.0, abs=1e-2)

    def test_the_hole_arrives_in_pieces_and_is_put_back_together(self, plate_with_hole):
        """Left in pieces, a selector for one hole would stop with an ambiguity."""
        hole = next(s for s in plate_with_hole.signatures if s.surface_type is SurfaceType.CYLINDER)
        assert len(plate_with_hole.patches[hole.tag]) > 1

    def test_the_faceted_hole_leaves_slightly_too_much_material(self, plate_with_hole):
        """A faceted hole is a polygon inside the true circle, so the part is heavier.

        Said out loud rather than smoothed over: the error is small, it is always
        in the same direction, and it is the price of having no CAD.
        """
        exact = 40.0 * 20.0 * 5.0 - math.pi * HOLE_RADIUS**2 * 5.0
        assert plate_with_hole.volume > exact
        assert plate_with_hole.volume == pytest.approx(exact, rel=0.005)
