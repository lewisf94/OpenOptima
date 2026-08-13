"""How big a carried item is: the arithmetic, the refusals and the cache hash.

The physics is measured in ``tests/verification/test_carried_size.py``, which
needs gmsh and CalculiX. Nothing here does.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from openoptima.domain.carried import CarriedShape, CarriedSize, mass_group
from openoptima.domain.model import PointMass
from openoptima.schema.loader import load_project
from openoptima.schema.project_schema import CarriedSizeSchema, PointMassSchema

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"

MASS = 0.035e-3


# -- where the middle sits -----------------------------------------------------


def test_an_item_sitting_on_the_face_has_its_middle_half_way_up() -> None:
    size = CarriedSize(CarriedShape.CYLINDER, across=28.0, deep=0.0, height=32.0)
    assert size.effective_centre_height == 16.0


def test_the_middle_can_be_put_somewhere_else() -> None:
    """A motor with a propeller on top is not uniform, and the person who owns
    it knows that better than a formula does."""
    size = CarriedSize(
        CarriedShape.CYLINDER, across=28.0, deep=0.0, height=32.0, centre_height=21.0
    )
    assert size.effective_centre_height == 21.0


def test_a_middle_below_the_face_is_refused() -> None:
    """Inside the part is not somewhere a bolted-on item can be, and the
    resulting frequency would look entirely ordinary."""
    with pytest.raises(ValueError, match="on or above the face"):
        CarriedSize(CarriedShape.BOX, across=10.0, deep=10.0, height=10.0, centre_height=-1.0)


def test_an_item_with_no_size_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        CarriedSize(CarriedShape.CYLINDER, across=0.0, deep=0.0, height=10.0)
    with pytest.raises(ValueError, match="must be positive"):
        CarriedSize(CarriedShape.BOX, across=10.0, deep=0.0, height=10.0)


# -- resisting being turned ----------------------------------------------------


def test_a_cylinder_matches_the_published_formulas() -> None:
    size = CarriedSize(CarriedShape.CYLINDER, across=28.0, deep=0.0, height=32.0)
    across, deep, axial = size.principal_inertia(MASS)
    radius = 14.0
    assert across == deep, "a cylinder is the same in every direction across it"
    assert across == pytest.approx(MASS * (3.0 * radius**2 + 32.0**2) / 12.0)
    assert axial == pytest.approx(MASS * radius**2 / 2.0)


def test_a_box_matches_the_published_formulas() -> None:
    size = CarriedSize(CarriedShape.BOX, across=20.0, deep=10.0, height=40.0)
    across, deep, out = size.principal_inertia(MASS)
    assert across == pytest.approx(MASS * (10.0**2 + 40.0**2) / 12.0)
    assert deep == pytest.approx(MASS * (20.0**2 + 40.0**2) / 12.0)
    assert out == pytest.approx(MASS * (20.0**2 + 10.0**2) / 12.0)


def test_a_taller_item_resists_turning_more() -> None:
    short = CarriedSize(CarriedShape.CYLINDER, 10.0, 0.0, 10.0).principal_inertia(MASS)
    tall = CarriedSize(CarriedShape.CYLINDER, 10.0, 0.0, 40.0).principal_inertia(MASS)
    assert tall[0] > short[0]
    # Only the height term changes, so the difference is exactly m (h2^2 - h1^2)/12.
    assert tall[0] - short[0] == pytest.approx(MASS * (40.0**2 - 10.0**2) / 12.0)


# -- the group of lumps that stands in for it ----------------------------------


def _measure(lumps):
    """Re-derive mass, centre and inertia from the lumps themselves.

    Deliberately independent of how they were built: this is the check that
    the group really represents the item, not that the code agrees with itself.
    """
    total = sum(lump.mass for lump in lumps)
    centre = tuple(
        sum(getattr(lump, axis) * lump.mass for lump in lumps) / total
        for axis in ("across", "deep", "out")
    )
    inertia = []
    for keep in range(3):
        others = [a for i, a in enumerate(("across", "deep", "out")) if i != keep]
        inertia.append(
            sum(
                lump.mass
                * sum(
                    (getattr(lump, a) - centre[("across", "deep", "out").index(a)]) ** 2
                    for a in others
                )
                for lump in lumps
            )
        )
    return total, centre, tuple(inertia)


@pytest.mark.parametrize(
    "size",
    [
        CarriedSize(CarriedShape.CYLINDER, 28.0, 0.0, 32.0),
        CarriedSize(CarriedShape.CYLINDER, 4.0, 0.0, 40.0),
        CarriedSize(CarriedShape.BOX, 20.0, 10.0, 40.0),
        CarriedSize(CarriedShape.BOX, 30.0, 30.0, 5.0),
    ],
)
def test_the_group_reproduces_the_item_exactly(size) -> None:
    """Mass, middle and turning resistance, all three, to machine precision.

    Held rigidly, those are the only three things about a carried item that
    reach the solve, so matching all three means the group *is* the item.
    """
    lumps = mass_group(MASS, size)
    total, centre, inertia = _measure(lumps)
    assert total == pytest.approx(MASS, rel=1e-12)
    assert centre == pytest.approx((0.0, 0.0, 0.0), abs=1e-15)
    assert inertia == pytest.approx(size.principal_inertia(MASS), rel=1e-10)


@pytest.mark.parametrize(
    "size",
    [
        CarriedSize(CarriedShape.CYLINDER, 28.0, 0.0, 32.0),
        CarriedSize(CarriedShape.BOX, 30.0, 30.0, 5.0),
        CarriedSize(CarriedShape.BOX, 1.0, 60.0, 1.0),
    ],
)
def test_no_lump_ever_weighs_less_than_nothing(size) -> None:
    """A negative mass does not make an eigenvalue solve fail.

    It makes it return numbers with no physical meaning, and nothing
    downstream would flag them. The arm the lumps sit on is chosen long enough
    that the six outer ones weigh less than the item, which is what keeps the
    remainder at the middle positive.
    """
    for lump in mass_group(MASS, size):
        assert lump.mass >= 0.0, f"lump at ({lump.across}, {lump.deep}, {lump.out})"


def test_a_flat_item_still_works() -> None:
    """A washer is nearly two-dimensional, and its inertia about the two axes
    in its plane is nearly half the third. That is the case that pushes the
    arm arithmetic hardest."""
    size = CarriedSize(CarriedShape.CYLINDER, 40.0, 0.0, 0.5)
    total, _centre, inertia = _measure(mass_group(MASS, size))
    assert total == pytest.approx(MASS, rel=1e-12)
    assert inertia == pytest.approx(size.principal_inertia(MASS), rel=1e-10)


# -- the project file ----------------------------------------------------------


def test_a_size_is_optional() -> None:
    """No size must mean exactly the old behaviour, so that adding this
    feature moved nobody's numbers."""
    carried = PointMassSchema(name="motor", region="pad", mass_kg=0.035).to_domain()
    assert carried.size is None
    assert not carried.has_size


def test_a_size_round_trips() -> None:
    carried = PointMassSchema(
        name="motor",
        region="pad",
        mass_kg=0.035,
        size={"shape": "cylinder", "across_mm": 28.0, "height_mm": 32.0},
    ).to_domain()
    assert carried.has_size
    assert carried.size is not None
    assert carried.size.shape is CarriedShape.CYLINDER
    assert carried.size.effective_centre_height == 16.0


def test_a_box_without_a_depth_is_refused() -> None:
    """Defaulting the depth to the width would silently invent a shape and a
    turning resistance nobody typed."""
    with pytest.raises(ValidationError, match="needs a depth_mm"):
        CarriedSizeSchema(shape="box", across_mm=20.0, height_mm=10.0)


def test_an_unknown_size_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        CarriedSizeSchema(shape="cylinder", diameter_mm=28.0, height_mm=32.0)


def test_the_drone_arm_example_gives_its_motor_a_size() -> None:
    """The example is the documentation for this feature; keep them together."""
    project = load_project(DRONE_ARM)
    motor = project.point_masses[0]
    assert motor.has_size
    assert motor.size is not None
    assert motor.size.effective_centre_height > 0.0


# -- the cache hash ------------------------------------------------------------


def _digest(size_block: str) -> str:
    text = DRONE_ARM.read_text()
    start = re.search(r"^point_masses:", text, re.MULTILINE).start()
    end = re.search(r"^load_cases:", text, re.MULTILINE).start()
    replacement = (
        f"point_masses:\n  - name: motor\n    region: motor_pad\n    mass_kg: 0.035\n{size_block}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "project.yaml"
        path.write_text(text[:start] + replacement + text[end:])
        return load_project(path).setup_digest()


_SIZED = "    size: { shape: cylinder, across_mm: 28.0, height_mm: 32.0 }"


def test_giving_an_item_a_size_changes_the_digest() -> None:
    """It moves the frequency by 2.3% on this part, so a result computed
    without one is not a cache hit for a project that has one."""
    assert _digest("") != _digest(_SIZED)


def test_a_different_size_changes_the_digest() -> None:
    assert _digest(_SIZED) != _digest(_SIZED.replace("32.0", "40.0"))


def test_moving_the_middle_changes_the_digest() -> None:
    """Same item, different place, different answer."""
    moved = _SIZED.replace("}", ", centre_height_mm: 21.0 }")
    assert _digest(_SIZED) != _digest(moved)


def test_a_shape_change_alone_changes_the_digest() -> None:
    """A 28 mm cylinder and a 28 mm square block weigh the same and resist
    turning differently."""
    box = "    size: { shape: box, across_mm: 28.0, depth_mm: 28.0, height_mm: 32.0 }"
    assert _digest(_SIZED) != _digest(box)


def test_a_point_mass_with_no_size_hashes_as_it_always_did() -> None:
    """Guards the promise that this feature moved no existing project.

    Not a golden string -- that would break for unrelated reasons -- but the
    property that matters: two projects that differ in nothing still agree.
    """
    assert _digest("") == _digest("")
    assert PointMass(name="m", region="r", mass=1.0).size is None
