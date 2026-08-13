"""Whether a shape can actually be printed, and what it costs to print it.

This module holds the settings and the rules. The mesh geometry lives behind
an adapter in ``printing/``, because ``domain/`` depends on no CAE tool.

**Printability is a trade-off, not a gate.** A design that needs support
material is not wrong, it is more work -- and how much performance somebody
will give up to avoid a support is a personal call that differs by part and by
printer. So this produces *metrics*, which a project may constrain, trade
against mass, or ignore entirely. It never silently deletes a design. The one
exception is the genuinely impossible: a part that does not fit the bed cannot
be printed at any price, and that is reported as an overflow a constraint can
refuse outright.

**What these numbers are not.** They are a measure of the shape against a
printer, and nothing about the print itself. They do not know that a short gap
can be bridged rather than supported, they do not know whether support can be
reached to remove it, and they do not know the difference between a support on
an outside face and one sealed inside a cavity forever. A part reported as
needing 500 mm2 of support may be easy or impossible depending on where that
500 mm2 is, and only a person looking at the shape can tell.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Angle from horizontal, in degrees, below which a downward-facing surface is
#: normally taken to need support. The common rule of thumb for FDM. It is a
#: *default for the metric*, not a fact: a well-tuned printer manages shallower,
#: and a fast one struggles sooner.
DEFAULT_OVERHANG_ANGLE_DEG = 45.0


@dataclass(frozen=True)
class BuildVolume:
    """The printer's usable space, in millimetres.

    ``height`` is measured along the build direction. ``width`` and ``depth``
    are the bed, and the part may be turned about the build axis to fit them,
    so the two are interchangeable.
    """

    width: float
    depth: float
    height: float

    def __post_init__(self) -> None:
        for name in ("width", "depth", "height"):
            if getattr(self, name) <= 0:
                raise ValueError(f"Build volume {name} must be positive")

    @property
    def footprint(self) -> tuple[float, float]:
        """Bed size, smaller dimension first."""
        return (min(self.width, self.depth), max(self.width, self.depth))


#: Triangle size used when nothing asks for anything finer. Measured on a real
#: part: the support area is bit-identical from 2 010 to 95 814 triangles on
#: flat faces, so this only has to be fine enough to follow a curve.
DEFAULT_TESSELLATION_MM = 3.0

#: Below this, a wall limit is far more likely to be a slipped decimal point
#: than a real intention -- no nozzle lays a bead this thin -- and the
#: tessellation it would ask for could take hours per design.
MINIMUM_WALL_CHECK_MM = 0.05


@dataclass(frozen=True)
class PrintingSettings:
    """What to measure about printing this part, and against which printer."""

    enabled: bool = False
    overhang_angle_deg: float = DEFAULT_OVERHANG_ANGLE_DEG
    #: ``None`` means do not check whether the part fits.
    build_volume: BuildVolume | None = None
    #: The thinnest wall worth measuring, in mm. ``None`` means do not measure
    #: wall thickness at all. **There is deliberately no default**: how thin is
    #: too thin depends on the nozzle, the material and what the wall is for,
    #: which makes it the engineer's number and not the software's -- the same
    #: rule as ``SemanticRegion.min_area_mm2``. It also sets how finely the
    #: shape is chopped up, so it decides what the check costs.
    min_wall_check_mm: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.overhang_angle_deg < 90.0:
            raise ValueError(
                f"overhang_angle_deg is measured from horizontal and must be "
                f"between 0 and 90, received {self.overhang_angle_deg:g}. "
                f"A vertical wall is 90 degrees and never needs support; a flat "
                f"ceiling is 0 and always does."
            )
        if self.min_wall_check_mm is not None and self.min_wall_check_mm < MINIMUM_WALL_CHECK_MM:
            raise ValueError(
                f"min_wall_check_mm is {self.min_wall_check_mm:g} mm, which is "
                f"thinner than any nozzle lays. A wall limit also sets how "
                f"finely the shape is chopped up to measure it, so a slipped "
                f"decimal point here costs hours per design rather than seconds. "
                f"The smallest accepted is {MINIMUM_WALL_CHECK_MM:g} mm."
            )

    @property
    def tessellation_mm(self) -> float:
        """How finely to chop the shape up before measuring anything.

        Tied to the wall limit, because that is the only setting that says how
        small a feature has to be resolved. **Measured**, on a curved wall
        wrapped round a small radius -- the case where flat facets hurt most --
        with the triangle size set to a multiple of the wall::

            5 x the wall    -16% to -34%
            2 x the wall     -8% to -11%
            1 x the wall   -1.7% to -3.0%
            0.5 x the wall  -0.6% to -0.8%

        consistently across walls of 0.6, 1.2 and 2.0 mm. Every one reads
        *low*, because a flat facet cuts the corner off a curve, so a coarse
        measurement over-reports how thin a wall is rather than under-reporting
        it. One times the wall costs a few percent in the safe direction and a
        fraction of the time that half would.
        """
        if self.min_wall_check_mm is None:
            return DEFAULT_TESSELLATION_MM
        return min(DEFAULT_TESSELLATION_MM, self.min_wall_check_mm)


def build_volume_overflow(extents: tuple[float, float, float], volume: BuildVolume | None) -> float:
    """How far the part exceeds the printer, in mm. Zero when it fits.

    ``extents`` is ``(along the build direction, and the two across it)``.

    Reported as a distance rather than a yes-or-no so a constraint can say
    "must not exceed", and so a design that is close to the limit is
    distinguishable from one that is far past it. The part may be turned about
    the build axis, so the two across-bed extents are matched to the bed
    smaller-against-smaller rather than in the order they arrive.
    """
    if volume is None:
        return 0.0
    height, *across = extents
    small, large = sorted(across)
    bed_small, bed_large = volume.footprint
    return max(
        0.0,
        height - volume.height,
        small - bed_small,
        large - bed_large,
    )
