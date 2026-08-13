"""Overhang and build-volume fit measured on real shapes.

The headline number is checked against a closed form rather than against an
earlier run: for a horizontal cylinder of radius *R* and length *L*, the strip
needing support at a 45 degree limit is the bottom quarter-turn of the
circumference on each side of bottom dead centre, so its area is exactly
``R (pi/2) L``. A metric that classified faces by the wrong sign, or measured
the angle from the wrong reference, does not land on that by accident.

The test that matters most for the *answer* is
:func:`test_the_bed_is_not_an_overhang`. Removing the face that rests on the
build plate is not a refinement -- it reverses which orientation the metric
prefers on the example part.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.failures import EvaluationFailure
from openoptima.domain.printing import BuildVolume, PrintingSettings
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.geometry.occ.templates import get_template
from openoptima.printing import measure_printability

pytestmark = pytest.mark.gmsh

#: The section the module docstring of ``printing/overhang.py`` quotes.
ARM = {"width": 26.0, "height": 24.0, "wall": 2.5}

#: Measured, and stable from 2010 to 95814 triangles. Printing upright needs
#: least support; printing on edge needs most.
ARM_SUPPORT_MM2 = {"x": 527.0, "y": 5500.0, "z": 2292.0}
#: What rests on the plate, and is therefore not an overhang.
ARM_BED_MM2 = {"x": 624.0, "y": 128.0, "z": 3900.0}

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _arm_brep(tmp_path, size_mm: float = 3.0, **overrides):
    template = get_template("drone_arm")
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "arm.brep"
    with gmsh_session() as gmsh:
        gmsh.model.add("arm")
        template.build(gmsh, {**template.defaults, **ARM, **overrides})
        gmsh.write(str(path))
    return path


def _settings(**kwargs) -> PrintingSettings:
    return PrintingSettings(enabled=True, **kwargs)


@pytest.mark.parametrize("axis", sorted(AXES))
def test_the_support_area_is_what_was_measured(tmp_path, axis: str) -> None:
    report = measure_printability(_arm_brep(tmp_path), AXES[axis], _settings())
    assert report.support_area_mm2 == pytest.approx(ARM_SUPPORT_MM2[axis], rel=1e-6)


@pytest.mark.parametrize("axis", sorted(AXES))
def test_the_bed_is_not_an_overhang(tmp_path, axis: str) -> None:
    """The term that decides which orientation wins.

    Printed flat, 3900 mm2 of the arm's underside lies on the plate. Counting
    it as needing support makes flat (6192) look worse than on edge (5628);
    removing it makes flat less than half the cost of on edge (2292 against
    5500). The ranking reverses, so this is load-bearing rather than tidy.
    """
    report = measure_printability(_arm_brep(tmp_path), AXES[axis], _settings())
    assert report.bed_area_mm2 == pytest.approx(ARM_BED_MM2[axis], rel=1e-6)
    # The raw figure, before the bed comes off, would rank y below z.
    raw = report.support_area_mm2 + report.bed_area_mm2
    assert raw == pytest.approx(ARM_SUPPORT_MM2[axis] + ARM_BED_MM2[axis], rel=1e-6)


def test_printing_flat_beats_printing_on_edge(tmp_path) -> None:
    """Stated as its own fact, because the raw number says the opposite."""
    flat = measure_printability(_arm_brep(tmp_path), AXES["z"], _settings())
    on_edge = measure_printability(_arm_brep(tmp_path), AXES["y"], _settings())
    assert flat.support_area_mm2 < on_edge.support_area_mm2
    assert (
        flat.support_area_mm2 + flat.bed_area_mm2 > on_edge.support_area_mm2 + on_edge.bed_area_mm2
    )


def test_the_area_does_not_move_when_the_shape_is_chopped_more_finely(tmp_path) -> None:
    """A flat-faced part is exact at any tessellation.

    If this drifted, optimising the metric would mean optimising the
    tessellation -- the defect the raw-peak-stress rule exists to prevent.
    """
    from openoptima.printing import overhang as module

    original = module._TESSELLATION_SIZE_MM
    seen = []
    try:
        for size in (6.0, 3.0, 1.5):
            module._TESSELLATION_SIZE_MM = size
            seen.append(
                measure_printability(
                    _arm_brep(tmp_path / str(size)), AXES["z"], _settings()
                ).support_area_mm2
            )
    finally:
        module._TESSELLATION_SIZE_MM = original
    assert seen == pytest.approx([ARM_SUPPORT_MM2["z"]] * 3, rel=1e-9)


def test_a_curved_surface_matches_the_closed_form(tmp_path) -> None:
    """A horizontal cylinder: supported strip is the bottom quarter-turn each
    side of bottom dead centre, so exactly ``R (pi/2) L``.

    Checked with the bed term off, because a cylinder touches the plate along
    a line of zero area and the tangent facets there are an artefact of the
    tessellation rather than a face resting on anything.
    """
    radius, length = 10.0, 50.0
    path = tmp_path / "cyl.brep"
    with gmsh_session() as gmsh:
        gmsh.model.add("cyl")
        gmsh.model.occ.addCylinder(0.0, 0.0, 0.0, length, 0.0, 0.0, radius)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))

    from openoptima.printing import overhang as module

    original = module._TESSELLATION_SIZE_MM
    try:
        module._TESSELLATION_SIZE_MM = 0.5
        report = measure_printability(path, AXES["z"], _settings())
    finally:
        module._TESSELLATION_SIZE_MM = original

    exact = radius * (math.pi / 2.0) * length
    measured = report.support_area_mm2 + report.bed_area_mm2
    # Facets straddling the 45 degree threshold flip in and out, so a few per
    # cent of wobble is expected and bounded. Measured +1.66% at this setting.
    assert measured == pytest.approx(exact, rel=0.05)


def test_a_part_too_big_for_the_bed_reports_how_far_over(tmp_path) -> None:
    """The arm is 150 mm long, so a 100 mm bed is 50 mm short."""
    report = measure_printability(
        _arm_brep(tmp_path),
        AXES["z"],
        _settings(build_volume=BuildVolume(100.0, 100.0, 100.0)),
    )
    assert report.build_volume_overflow_mm == pytest.approx(50.0, rel=1e-6)


def test_a_part_that_fits_reports_zero(tmp_path) -> None:
    report = measure_printability(
        _arm_brep(tmp_path),
        AXES["z"],
        _settings(build_volume=BuildVolume(220.0, 220.0, 250.0)),
    )
    assert report.build_volume_overflow_mm == 0.0


def test_a_shape_that_is_not_closed_is_refused(tmp_path) -> None:
    """Without an inside there is no outward normal, and every angle here is
    the sign of one."""
    path = tmp_path / "open.stl"
    path.write_text(
        "solid open\n"
        "facet normal 0 0 1\n outer loop\n"
        "  vertex 0 0 0\n  vertex 1 0 0\n  vertex 0 1 0\n"
        " endloop\nendfacet\nendsolid open\n"
    )
    with pytest.raises(EvaluationFailure, match="not closed"):
        measure_printability(path, AXES["z"], _settings())
