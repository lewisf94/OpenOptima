"""How big a carried item is, and what that does to a natural frequency.

A motor bolted to a pad is not flat. Its middle sits some way above the face,
and it resists being turned. Both make the part vibrate more slowly than a
model that treats it as a point in the face.

**Measured on ``examples/drone_arm``**, at the design that example reports as
its answer, with a 35 g motor 28 mm across and 32 mm tall:

    motor flat in the pad                169.8 Hz
    motor centre 16 mm up, no size       166.6 Hz
    motor centre 16 mm up, full size     165.9 Hz

The example holds that arm to 170 Hz, so the design it reports moves from
passing to failing. The error always runs the same way -- the reported
frequency is too high -- and an optimiser converges onto a constraint
boundary by construction, so every design it returns sits exactly where that
error bites.

**CalculiX has no rotary inertia element.** Version 2.21 knows ``MASS`` and
nothing else of that family. So "it resists being turned" cannot be asked for
directly: it is built from ordinary point masses at real positions, held
rigidly to the face. Only three things about such a group reach the solve --
the total mass, where its centre is, and how it resists turning about each of
its three axes -- so seven masses reproduce any real item exactly.

This module owns the arithmetic. Where the masses actually land in space
needs the face's direction, so the solver adapter does that part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

#: A lump this far out on each axis, as a multiple of the smallest arm that
#: leaves a non-negative mass at the centre. Any value above 1 works; 1.3
#: keeps the centre lump comfortably positive without spreading the group so
#: wide that it is hard to read in a deck.
_ARM_MARGIN = 1.3


class CarriedShape(str, Enum):
    """The shape a carried item is treated as, for working out its inertia."""

    CYLINDER = "cylinder"
    BOX = "box"


@dataclass(frozen=True)
class CarriedSize:
    """How big a carried item is, in millimetres.

    ``height`` is measured along the face's outward direction -- straight up
    off the surface the item bolts to. ``across`` and ``deep`` are the two
    directions in the plane of that face. A cylinder ignores ``deep`` and
    treats ``across`` as its diameter.

    **The item is treated as a uniform solid of this size.** A real motor is
    not uniform, and where its mass actually sits is not on any datasheet.
    Assuming uniform puts the centre at half the height, which is usually
    close and is not guaranteed conservative: a motor carrying a propeller on
    top has its centre higher than that, and the true frequency is lower than
    the one reported. Set ``centre_height`` when you know better.
    """

    shape: CarriedShape
    #: Across the face. The diameter, for a cylinder.
    across: float
    #: The other direction in the face. Ignored for a cylinder.
    deep: float
    #: Along the face's outward direction.
    height: float
    #: How far the item's middle sits above the face. Defaults to half the
    #: height, which is what a uniform solid sitting on the face gives.
    centre_height: float | None = None

    def __post_init__(self) -> None:
        for label, value in (("across", self.across), ("height", self.height)):
            if value <= 0:
                raise ValueError(
                    f"A carried item's {label} must be positive, received {value:g} mm"
                )
        if self.shape is CarriedShape.BOX and self.deep <= 0:
            raise ValueError(f"A carried item's depth must be positive, received {self.deep:g} mm")
        if self.centre_height is not None and self.centre_height < 0:
            raise ValueError(
                "A carried item's centre must sit on or above the face it bolts to, "
                f"received {self.centre_height:g} mm"
            )

    @property
    def effective_centre_height(self) -> float:
        """Where the middle sits, above the face."""
        return self.height / 2.0 if self.centre_height is None else self.centre_height

    def principal_inertia(self, mass: float) -> tuple[float, float, float]:
        """Resistance to being turned about each of its own three axes.

        Ordered (across, deep, height) to match the axes above. Units follow
        the mass: tonnes in, ``t mm^2`` out.
        """
        if self.shape is CarriedShape.CYLINDER:
            radius = self.across / 2.0
            transverse = mass * (3.0 * radius**2 + self.height**2) / 12.0
            axial = mass * radius**2 / 2.0
            return transverse, transverse, axial
        return (
            mass * (self.deep**2 + self.height**2) / 12.0,
            mass * (self.across**2 + self.height**2) / 12.0,
            mass * (self.across**2 + self.deep**2) / 12.0,
        )

    def digest_fields(self) -> tuple[str, float, float, float, float]:
        """Everything about this size that can change a number."""
        return (
            self.shape.value,
            self.across,
            self.deep,
            self.height,
            self.effective_centre_height,
        )


@dataclass(frozen=True)
class Lump:
    """One point mass in the group that stands in for a carried item.

    The offsets are from the item's own centre, along the three axes of
    :class:`CarriedSize`: across the face, along the face, and out of it.
    """

    across: float
    deep: float
    out: float
    mass: float


def mass_group(mass: float, size: CarriedSize) -> tuple[Lump, ...]:
    """Point masses reproducing an item's mass, centre and turning resistance.

    One lump at the centre and a pair on each axis. Writing ``A_i`` for the
    pair contribution ``2 m_i a^2``, a pair on axis *i* adds nothing to the
    inertia about axis *i* and ``A_i`` to the other two::

        I1 = A2 + A3          A1 = (I2 + I3 - I1) / 2
        I2 = A1 + A3    so    A2 = (I1 + I3 - I2) / 2
        I3 = A1 + A2          A3 = (I1 + I2 - I3) / 2

    Every physical inertia satisfies the triangle inequality, so no ``A_i``
    comes out negative. The arm is then chosen long enough that the six outer
    lumps weigh less than the item, leaving the remainder at the centre --
    a shorter arm needs heavier lumps and would drive that remainder below
    zero, and a negative mass makes an eigenvalue solve return numbers with
    no physical meaning at all.

    The group's positions are not the item's real ones and are not meant to
    be: held rigidly, only the mass, the centre and the turning resistance
    reach the solve, and this reproduces all three exactly.
    """
    inertia = size.principal_inertia(mass)
    pairs = (
        (inertia[1] + inertia[2] - inertia[0]) / 2.0,
        (inertia[0] + inertia[2] - inertia[1]) / 2.0,
        (inertia[0] + inertia[1] - inertia[2]) / 2.0,
    )
    # Guard rather than trust: a shape whose inertia broke the triangle
    # inequality would put a negative mass into the deck, and CalculiX would
    # solve it and report a frequency.
    if min(pairs) < 0.0:
        raise ValueError(
            f"A {size.shape.value} {size.across:g} x {size.deep:g} x {size.height:g} mm "
            f"gives an inertia no real object can have: {inertia}"
        )

    total_pairs = sum(pairs)
    if total_pairs <= 0.0:
        return (Lump(0.0, 0.0, 0.0, mass),)

    arm = math.sqrt(total_pairs / mass) * _ARM_MARGIN
    lumps: list[Lump] = []
    used = 0.0
    for axis, pair in enumerate(pairs):
        each = pair / (2.0 * arm * arm)
        used += 2.0 * each
        for sign in (1.0, -1.0):
            offsets = [0.0, 0.0, 0.0]
            offsets[axis] = sign * arm
            lumps.append(Lump(offsets[0], offsets[1], offsets[2], each))
    lumps.append(Lump(0.0, 0.0, 0.0, mass - used))
    return tuple(lumps)
