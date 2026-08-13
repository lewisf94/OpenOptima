"""Printability settings, the fit arithmetic, and the cache hash.

The geometry itself is measured in ``tests/integration/test_printability.py``,
which needs a CAD kernel. What is here needs no CAE tool.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from openoptima.domain.printing import (
    DEFAULT_TESSELLATION_MM,
    MINIMUM_WALL_CHECK_MM,
    BuildVolume,
    PrintingSettings,
    build_volume_overflow,
)
from openoptima.schema.loader import load_project
from openoptima.schema.project_schema import PrintingSchema

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"


def _top_level_key(text: str, key: str) -> int:
    """Offset of a top-level YAML key, matching only at the start of a line.

    A plain ``text.index("printing:")`` also matches the word in a comment,
    and this example is heavily commented. That once cut a file in half from
    the middle of a paragraph and produced a YAML error nobody could read.
    """
    match = re.search(rf"^{re.escape(key)}:", text, re.MULTILINE)
    assert match is not None, f"{key}: not found as a top-level key"
    return match.start()


# -- the settings themselves --------------------------------------------------


def test_the_overhang_angle_is_measured_from_horizontal() -> None:
    """0 and 90 are both meaningless as a threshold, and both are refused.

    A vertical wall is 90 degrees and never needs support; a flat ceiling is 0
    and always does. A threshold at either end classifies everything or
    nothing, which is a setting nobody meant to type.
    """
    for bad in (0.0, 90.0, -5.0, 120.0):
        with pytest.raises(ValueError, match="measured from horizontal"):
            PrintingSettings(overhang_angle_deg=bad)
    assert PrintingSettings(overhang_angle_deg=45.0).overhang_angle_deg == 45.0


def test_a_printer_with_no_size_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        BuildVolume(width=220.0, depth=0.0, height=250.0)


# -- does it fit ---------------------------------------------------------------


def test_a_part_that_fits_overflows_by_nothing() -> None:
    bed = BuildVolume(width=220.0, depth=220.0, height=250.0)
    assert build_volume_overflow((100.0, 50.0, 60.0), bed) == 0.0


def test_overflow_is_a_distance_not_a_verdict() -> None:
    """So a constraint can say "must not exceed", and so a part that is barely
    too big is distinguishable from one that is hopeless."""
    bed = BuildVolume(width=220.0, depth=220.0, height=250.0)
    assert build_volume_overflow((300.0, 50.0, 60.0), bed) == pytest.approx(50.0)
    assert build_volume_overflow((1000.0, 50.0, 60.0), bed) == pytest.approx(750.0)


def test_the_part_may_be_turned_about_the_build_axis() -> None:
    """A long thin part on a rectangular bed fits if it fits either way round.

    Matching the extents to the bed in the order they happen to arrive would
    refuse a part that any operator would simply rotate.
    """
    bed = BuildVolume(width=100.0, depth=300.0, height=250.0)
    # 250 long across the bed: too big for the 100 side, fine on the 300 side.
    assert build_volume_overflow((50.0, 250.0, 80.0), bed) == 0.0
    assert build_volume_overflow((50.0, 80.0, 250.0), bed) == 0.0


def test_height_is_measured_along_the_build_direction() -> None:
    """The first extent is the one that has to clear the gantry, and it is not
    interchangeable with the other two."""
    bed = BuildVolume(width=300.0, depth=300.0, height=100.0)
    assert build_volume_overflow((150.0, 50.0, 50.0), bed) == pytest.approx(50.0)
    assert build_volume_overflow((50.0, 150.0, 50.0), bed) == 0.0


def test_no_printer_means_no_check() -> None:
    assert build_volume_overflow((9999.0, 9999.0, 9999.0), None) == 0.0


# -- the project file ----------------------------------------------------------


def test_printing_is_off_unless_asked_for() -> None:
    """It costs a tessellation per design, so it is opt-in.

    Checked on a project with no ``printing:`` block at all, rather than on
    the drone arm, which switches it on deliberately.
    """
    text = DRONE_ARM.read_text()
    start = _top_level_key(text, "printing")
    end = _top_level_key(text, "solver")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "project.yaml"
        path.write_text(text[:start] + text[end:])
        assert not load_project(path).printing.enabled


def test_the_drone_arm_example_measures_printability() -> None:
    """The example is the documentation for this feature; keep them together."""
    project = load_project(DRONE_ARM)
    assert project.printing.enabled
    assert project.printing.build_volume is not None


def test_a_printing_block_round_trips() -> None:
    settings = PrintingSchema(
        enabled=True,
        overhang_angle_deg=40.0,
        build_volume={"width_mm": 220.0, "depth_mm": 210.0, "height_mm": 250.0},
    ).to_domain()
    assert settings.enabled
    assert settings.overhang_angle_deg == 40.0
    assert settings.build_volume is not None
    assert settings.build_volume.footprint == (210.0, 220.0)


def test_an_unknown_printing_key_is_refused() -> None:
    """A typo must not fall back to a default and change a reported number."""
    with pytest.raises(ValidationError):
        PrintingSchema(enabled=True, overhang_angle=45.0)


# -- the cache hash ------------------------------------------------------------


def _digest(printing_block: str) -> str:
    text = DRONE_ARM.read_text().replace(
        "solver:\n  name: calculix", f"{printing_block}\nsolver:\n  name: calculix"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "project.yaml"
        path.write_text(text)
        return load_project(path).setup_digest()


_ON = "printing:\n  enabled: true\n  overhang_angle_deg: 45.0\n"


def test_turning_printing_on_changes_the_digest() -> None:
    assert _digest("") != _digest(_ON)


def test_a_different_overhang_limit_changes_the_digest() -> None:
    """40 degrees and 45 classify different faces, so they are different
    answers and must not share a cached result."""
    assert _digest(_ON) != _digest(_ON.replace("45.0", "40.0"))


def test_a_different_printer_changes_the_digest() -> None:
    """The overflow is measured against the bed, so the bed is part of the
    question."""
    small = _ON + "  build_volume: { width_mm: 100, depth_mm: 100, height_mm: 100 }\n"
    large = _ON + "  build_volume: { width_mm: 300, depth_mm: 300, height_mm: 300 }\n"
    assert _digest(small) != _digest(large)


# -- how thin a wall to look for -----------------------------------------------


def test_a_wall_limit_is_optional() -> None:
    """No default, for the same reason min_area_mm2 has none: how thin is too
    thin depends on the nozzle and on what the wall is holding."""
    assert PrintingSettings().min_wall_check_mm is None
    assert PrintingSettings().tessellation_mm == DEFAULT_TESSELLATION_MM


def test_the_wall_limit_sets_how_finely_the_shape_is_chopped_up() -> None:
    """Measured: at 5x the wall a curved wall reads 16-34% thin, at 1x it
    reads 1.7-3.0% thin. Tying the two is what puts it on the 1x row."""
    assert PrintingSettings(min_wall_check_mm=0.8).tessellation_mm == 0.8
    assert PrintingSettings(min_wall_check_mm=1.5).tessellation_mm == 1.5


def test_a_generous_wall_limit_does_not_make_the_chopping_coarser() -> None:
    """A 10 mm limit must not tessellate at 10 mm: the overhang measurement
    shares this mesh, and a curve needs following whatever the wall is."""
    assert PrintingSettings(min_wall_check_mm=10.0).tessellation_mm == DEFAULT_TESSELLATION_MM


def test_a_slipped_decimal_point_is_refused() -> None:
    """0.08 instead of 0.8 is not a thinner check, it is an unusable one: the
    tessellation it asks for would take hours per design, and no nozzle lays a
    bead that thin anyway."""
    with pytest.raises(ValueError, match="thinner than any nozzle"):
        PrintingSettings(min_wall_check_mm=0.01)
    assert PrintingSettings(min_wall_check_mm=MINIMUM_WALL_CHECK_MM).min_wall_check_mm


def test_a_wall_limit_round_trips() -> None:
    settings = PrintingSchema(enabled=True, min_wall_check_mm=0.8).to_domain()
    assert settings.min_wall_check_mm == 0.8
    assert settings.tessellation_mm == 0.8


def test_the_drone_arm_example_deliberately_does_not_check_its_wall() -> None:
    """It costs 7.02 s per design there against 0.55 s, and the `wall`
    variable has a 2 mm floor, so the check could only ever report 2.5 mm.
    Paying 13 minutes a run for a number that cannot change is the mistake the
    example exists to talk somebody out of."""
    project = load_project(DRONE_ARM)
    assert project.printing.enabled, "the other two printability checks stay on"
    assert project.printing.min_wall_check_mm is None


def test_a_wall_limit_changes_the_digest() -> None:
    """It decides whether the wall is measured at all AND how finely, and a
    coarser chop reads a curved wall thinner. Two limits, two answers."""
    assert _digest(_ON) != _digest(_ON + "  min_wall_check_mm: 0.8\n")
    assert _digest(_ON + "  min_wall_check_mm: 0.8\n") != _digest(
        _ON + "  min_wall_check_mm: 1.2\n"
    )
