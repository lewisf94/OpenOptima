"""V17 -- how thin the thinnest wall is, against shapes whose answer is exact.

A printer lays plastic in beads of a fixed width. A wall thinner than about
two of them either does not print or prints as one unfused line, and no stress
calculation notices: the shape is perfectly sound on paper.

This checks the measurement against boxes and tubes whose wall thickness is
known by construction, rather than against an earlier run of the same code.

**Why the ray method.** ``trimesh`` offers two. The largest-inscribed-sphere
method reads a third low on a plate of known thickness -- 0.5333 for 0.8 mm,
and the same 33% on 2 mm and 5 mm. The ray method reads 0.8000, 2.0000 and
5.0000 exactly.

**Flat walls are exact and do not depend on the triangles.** Measured on a
block carrying a 0.6 mm fin, tessellated at 10, 5, 3, 1.5 and 0.8 mm -- 576 to
24 008 triangles -- every one read 0.6000. So optimising this cannot turn into
optimising the tessellation.

**Curved walls read low, and the error is set by the triangle size.** A flat
facet cuts the corner off a curve, so the chord across a curved wall is
shorter than the wall. Measured on walls of 0.6, 1.2 and 2.0 mm wrapped round
small radii, with the triangle size a multiple of the wall:

    5 x the wall     -16% to -34%
    2 x the wall      -8% to -11%
    1 x the wall    -1.7% to -3.0%
    0.5 x the wall  -0.6% to -0.8%

Every one **low**, which is the safe direction: a coarse measurement reports a
wall as thinner than it is, so it can only over-reject. The tessellation is
tied to the wall limit the project declares, which puts it on the 1x row.

**What it does not find, stated plainly.** The thin end of a taper. The
thinnest point of anything that tapers is its edge, where the thickness is
zero by definition, and reporting zero for every part with a chamfer on it
would make the number useless. Measured on a plate running from 6 mm down to
0.5 mm, this reads 0.5502 -- about 10% **high**, the unsafe direction. It
measures walls, and a wall is a run of material of roughly even thickness.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

import pytest

from openoptima.domain.printing import PrintingSettings
from openoptima.geometry.gmsh_session import gmsh_session, suppress_native_output
from openoptima.printing.overhang import measure_printability

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh, pytest.mark.gmsh]

#: A flat wall is exact, so this only has to cover arithmetic noise.
FLAT_TOLERANCE = 1.0e-4

#: The measured worst case on a curved wall at the tessellation this uses is
#: -3.0%, and it is always low. 5% catches a real error with room to spare
#: while leaving the discretisation alone.
CURVED_TOLERANCE = 0.05


def _brep(directory, name, build):
    path = directory / f"{name}.brep"
    with gmsh_session() as gmsh, suppress_native_output():
        gmsh.model.add(name)
        build(gmsh)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


def _measure(directory, name, build, limit_mm):
    path = _brep(directory, name, build)
    settings = PrintingSettings(enabled=True, min_wall_check_mm=limit_mm)
    report = measure_printability(path, (0.0, 0.0, 1.0), settings, scratch=directory)
    assert report.min_wall_thickness_mm is not None
    return report.min_wall_thickness_mm


def _hollow_box(wall: float):
    def build(gmsh):
        gmsh.model.occ.addBox(0, 0, 0, 30.0, 25.0, 60.0, 1)
        gmsh.model.occ.addBox(wall, wall, -1.0, 30.0 - 2 * wall, 25.0 - 2 * wall, 62.0, 2)
        gmsh.model.occ.cut([(3, 1)], [(3, 2)])

    return build


def _block_with_fin(fin: float):
    def build(gmsh):
        gmsh.model.occ.addBox(0, 0, 0, 40.0, 40.0, 10.0, 1)
        gmsh.model.occ.addBox(20.0 - fin / 2.0, 0.0, 10.0, fin, 40.0, 20.0, 2)
        gmsh.model.occ.fuse([(3, 1)], [(3, 2)])

    return build


def _tube(outer_diameter: float, wall: float):
    def build(gmsh):
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 60.0, outer_diameter / 2.0, 1)
        gmsh.model.occ.addCylinder(0, 0, -1.0, 0, 0, 62.0, outer_diameter / 2.0 - wall, 2)
        gmsh.model.occ.cut([(3, 1)], [(3, 2)])

    return build


@pytest.mark.parametrize("wall", [1.0, 2.5])
def test_a_hollow_box_reports_its_wall_exactly(tmp_path, wall) -> None:
    """The wall is the answer, not the 30 x 25 x 60 outside."""
    measured = _measure(tmp_path, f"box{wall:g}", _hollow_box(wall), wall)
    assert measured == pytest.approx(wall, rel=FLAT_TOLERANCE)


@pytest.mark.parametrize("fin", [2.0, 1.0, 0.6])
def test_one_thin_fin_on_a_thick_block_is_found(tmp_path, fin) -> None:
    """The whole point of the check. The fin is a small part of the surface
    and the block around it is ten to sixteen times thicker, so an answer that
    described the part as a whole would miss it completely."""
    measured = _measure(tmp_path, f"fin{fin:g}", _block_with_fin(fin), fin)
    assert measured == pytest.approx(fin, rel=FLAT_TOLERANCE)


@pytest.mark.parametrize("size_mm", [10.0, 3.0, 0.8])
def test_a_flat_wall_does_not_depend_on_the_triangles(tmp_path, size_mm) -> None:
    """576 to 24 008 triangles, all reading 0.6000.

    This is what separates a real measurement from one that is really about
    the mesh -- the distinction that stops raw peak stress being an objective.
    """
    path = _brep(tmp_path, f"fin_{size_mm:g}", _block_with_fin(0.6))
    settings = PrintingSettings(enabled=True, min_wall_check_mm=size_mm)
    report = measure_printability(path, (0.0, 0.0, 1.0), settings, scratch=tmp_path)
    assert report.min_wall_thickness_mm == pytest.approx(0.6, rel=FLAT_TOLERANCE)


@pytest.mark.parametrize("wall", [1.0, 2.5])
def test_a_curved_wall_is_close_and_never_reads_thick(tmp_path, wall) -> None:
    """Low is the safe direction and it must stay that way.

    A facet cuts the corner off a curve, so the chord across is shorter than
    the wall. Reading *thick* would mean the measurement had started flattering
    the part, which is the direction that matters.
    """
    measured = _measure(tmp_path, f"tube{wall:g}", _tube(20.0, wall), wall)
    assert measured == pytest.approx(wall, rel=CURVED_TOLERANCE)
    assert measured <= wall * (1.0 + FLAT_TOLERANCE), (
        f"a curved {wall:g} mm wall read {measured:.4f} mm, thicker than it is"
    )


def test_a_coarser_limit_reads_thinner_not_thicker(tmp_path) -> None:
    """The bias has a direction, and the direction is what makes a coarse
    measurement safe to act on: it over-rejects rather than over-accepts."""
    fine = _measure(tmp_path, "tube_fine", _tube(20.0, 1.0), 1.0)
    coarse = _measure(tmp_path, "tube_coarse", _tube(20.0, 1.0), 3.0)
    assert coarse < fine <= 1.0 + FLAT_TOLERANCE


def test_nothing_is_measured_without_a_declared_limit(tmp_path) -> None:
    """No default, for the same reason ``min_area_mm2`` has none: how thin is
    too thin is the engineer's decision. Here it also sets the resolution, so
    without it there is no defensible tessellation to measure at."""
    path = _brep(tmp_path, "plain", _hollow_box(2.5))
    report = measure_printability(
        path, (0.0, 0.0, 1.0), PrintingSettings(enabled=True), scratch=tmp_path
    )
    assert report.min_wall_thickness_mm is None
    assert "min_wall_thickness_mm" not in report.as_metrics()
    # The other two printability numbers still come out.
    assert "support_area_mm2" in report.as_metrics()
