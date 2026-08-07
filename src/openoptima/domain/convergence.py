"""Mesh convergence maths: does a number stop moving as the mesh gets finer?

Every result OpenOptima reports comes from one mesh. A mesh that is too coarse
gives a wrong answer that looks exactly like a right one. The only way to find
out is to run the same design at several mesh densities and watch what the
numbers do.

This module holds that judgement, expressed as arithmetic on a handful of
floats. It follows the standard procedure for grid-convergence assessment
(Roache's Grid Convergence Index, as adopted by ASME V&V 20).

Two decisions here are deliberate and easy to get wrong.

**This module never says "converged".** It reports what the numbers did, and
an uncertainty band. Whether that band is tight enough to trust is the
engineer's decision, not the software's. ``AGENTS.md`` states this rule.

**Divergence is detected, not hidden.** The textbook formula for the observed
order of convergence takes an absolute value, which silently turns a diverging
sequence into a plausible positive order. At a stress singularity the peak
grows without limit, and that absolute value would report a converging number
where none exists. So this module inspects the sign first and refuses to
extrapolate a sequence that is not settling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

#: Safety factor on the uncertainty band. Roache recommends 1.25 when the order
#: of convergence is observed from three or more meshes, rather than assumed.
SAFETY_FACTOR = 1.25

#: Relative size below which a difference between two meshes is treated as
#: numerical noise rather than signal. Comparing two values that agree to twelve
#: digits tells you nothing except that the arithmetic is deterministic.
NOISE_FLOOR = 1.0e-9

#: The observed order of convergence is only credible in a band around the
#: theoretical one. Well outside it, the meshes are not yet in the range where
#: the theory applies, and the uncertainty band is optimistic.
PLAUSIBLE_ORDER = (0.5, 3.0)

_MAX_ITERATIONS = 64
_ITERATION_TOLERANCE = 1.0e-8


class Behaviour(Enum):
    """What a quantity did as the mesh was refined.

    These describe observed behaviour. None of them is a verdict on whether a
    result is good enough to use.
    """

    #: Settling steadily towards a limit. An uncertainty band is available.
    SETTLING = "settling"
    #: Moving further with every refinement instead of less. This is what a
    #: stress singularity looks like, and no amount of refinement will fix it.
    DIVERGING = "diverging"
    #: Bouncing up and down rather than approaching a limit. The meshes are not
    #: in the range where the theory applies, so no band can be quoted.
    OSCILLATING = "oscillating"
    #: Already identical across meshes, to within numerical noise.
    FLAT = "flat"
    #: Fewer than three usable meshes, so nothing can be said.
    NOT_ENOUGH_DATA = "not_enough_data"


@dataclass(frozen=True)
class GridLevel:
    """One mesh density, and the value it produced.

    ``representative_size`` is the average element size, in mm, calculated as
    the cube root of (mesh volume / element count). This is the standard
    measure for an unstructured mesh. It is used instead of the requested
    element size because a mesher does not deliver exactly what it is asked
    for: curvature refinement, size limits and the geometry itself all change
    the result. Comparing meshes by what was requested, rather than by what was
    produced, gives a wrong refinement ratio and therefore a wrong answer.
    """

    label: str
    representative_size: float
    node_count: int
    element_count: int
    value: float


@dataclass(frozen=True)
class MetricConvergence:
    """What one quantity did across the meshes."""

    metric: str
    behaviour: Behaviour
    levels: tuple[GridLevel, ...]
    #: Observed order of convergence. Negative means the value is running away.
    observed_order: float | None = None
    #: The value the sequence appears to be heading for (Richardson
    #: extrapolation). Only available when the behaviour is SETTLING.
    extrapolated: float | None = None
    #: Estimated uncertainty on the finest mesh's value, as a fraction. 0.02
    #: means the finest value is estimated to sit within 2% of the answer a
    #: perfect mesh would give.
    uncertainty: float | None = None
    #: Ratio that should be near 1.0 if the meshes are fine enough for the
    #: theory behind the uncertainty band to hold. Far from 1.0 means the band
    #: is optimistic.
    asymptotic_ratio: float | None = None
    note: str = ""

    @property
    def finest_value(self) -> float | None:
        return self.levels[0].value if self.levels else None

    @property
    def spread(self) -> float | None:
        """Total relative range across every mesh, as a fraction.

        This is the plainest, least theoretical number in the report, and it
        is reported whatever the behaviour. A value can be labelled
        "oscillating" or "diverging" and still have moved by only a twentieth
        of one percent across every mesh tried, which is a different situation
        from one that moved by forty percent. The behaviour alone cannot tell
        those apart; this can, and the reader needs both to judge.
        """
        if len(self.levels) < 2:
            return None
        values = [level.value for level in self.levels]
        scale = max(abs(value) for value in values)
        if scale == 0.0:
            return 0.0
        return (max(values) - min(values)) / scale

    @property
    def order_is_plausible(self) -> bool:
        if self.observed_order is None:
            return False
        low, high = PLAUSIBLE_ORDER
        return low <= self.observed_order <= high

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "behaviour": self.behaviour.value,
            "observed_order": self.observed_order,
            "extrapolated": self.extrapolated,
            "uncertainty": self.uncertainty,
            "spread": self.spread,
            "asymptotic_ratio": self.asymptotic_ratio,
            "order_is_plausible": self.order_is_plausible,
            "note": self.note,
            "levels": [
                {
                    "label": level.label,
                    "representative_size_mm": level.representative_size,
                    "node_count": level.node_count,
                    "element_count": level.element_count,
                    "value": level.value,
                }
                for level in self.levels
            ],
        }


def representative_size(mesh_volume: float, element_count: int) -> float:
    """Average element size in mm, for comparing one mesh against another."""
    if mesh_volume <= 0 or element_count <= 0:
        return float("nan")
    return (mesh_volume / element_count) ** (1.0 / 3.0)


def analyse_metric(metric: str, levels: list[GridLevel]) -> MetricConvergence:
    """Assess one quantity across several meshes, finest first.

    Uses the three finest usable meshes. Extra meshes are kept in the record
    but do not change the numbers: the standard procedure is defined on three.
    """
    usable = [
        level
        for level in levels
        if math.isfinite(level.value) and math.isfinite(level.representative_size)
    ]
    usable.sort(key=lambda level: level.representative_size)
    ordered = tuple(usable)

    if len(usable) < 3:
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.NOT_ENOUGH_DATA,
            levels=ordered,
            note=(
                f"only {len(usable)} usable mesh(es); three are needed to "
                "estimate how fast a number is settling"
            ),
        )

    fine, medium, coarse = usable[0], usable[1], usable[2]
    ratio_fine = medium.representative_size / fine.representative_size
    ratio_coarse = coarse.representative_size / medium.representative_size

    if ratio_fine <= 1.0 or ratio_coarse <= 1.0:
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.NOT_ENOUGH_DATA,
            levels=ordered,
            note=(
                "the meshes are not distinct enough in size to compare "
                f"(size ratios {ratio_fine:.3f} and {ratio_coarse:.3f}; both "
                "must exceed 1.0)"
            ),
        )

    step_fine = medium.value - fine.value
    step_coarse = coarse.value - medium.value

    scale = max(abs(fine.value), abs(medium.value), abs(coarse.value))
    if scale == 0.0 or (
        abs(step_fine) <= NOISE_FLOOR * scale and abs(step_coarse) <= NOISE_FLOOR * scale
    ):
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.FLAT,
            levels=ordered,
            extrapolated=fine.value,
            uncertainty=0.0,
            note="identical across all three meshes; refining further changes nothing",
        )

    if abs(step_fine) <= NOISE_FLOOR * scale:
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.FLAT,
            levels=ordered,
            extrapolated=fine.value,
            uncertainty=0.0,
            note=(
                "the two finest meshes agree to within numerical noise, so the "
                "rate of settling cannot be measured, and does not matter"
            ),
        )

    change_ratio = step_coarse / step_fine

    # A negative ratio means the value went up, then down (or the reverse). It
    # is not approaching a limit from one side, so the theory behind the
    # uncertainty band does not hold and no band is quoted.
    if change_ratio < 0.0:
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.OSCILLATING,
            levels=ordered,
            note=(
                "the value moved one way and then back, instead of settling. "
                "The meshes are too coarse for a reliable estimate. Add a "
                "finer mesh."
            ),
        )

    # A ratio at or below 1 means each refinement changed the answer by *more*
    # than the last one. The number is running away, not settling. This is the
    # signature of a stress singularity, where the true value is unbounded and
    # no mesh will ever converge. The order is reported as the negative number
    # it really is, and nothing is extrapolated.
    if change_ratio <= 1.0:
        order = math.log(change_ratio) / math.log(ratio_fine) if change_ratio > 0 else None
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.DIVERGING,
            levels=ordered,
            observed_order=order,
            note=(
                "each refinement changed this number by more than the last "
                "one, so it is not settling on a value. If this is a peak "
                "stress at a sharp internal corner or at a fixed face, that is "
                "expected: the true stress there is infinite and no mesh will "
                "ever converge. Use a percentile measure, or model a real "
                "fillet radius."
            ),
        )

    order = _observed_order(change_ratio, ratio_fine, ratio_coarse)
    if order is None:
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.OSCILLATING,
            levels=ordered,
            note=(
                "the rate of settling could not be calculated from these three "
                "meshes. Add a finer mesh."
            ),
        )

    denominator = ratio_fine**order - 1.0
    if denominator <= 0.0:  # pragma: no cover - guarded by order > 0 above
        return MetricConvergence(
            metric=metric,
            behaviour=Behaviour.OSCILLATING,
            levels=ordered,
            observed_order=order,
            note="the rate of settling could not be used to estimate a band.",
        )

    extrapolated = fine.value + (fine.value - medium.value) / denominator

    uncertainty: float | None = None
    if fine.value != 0.0:
        relative_step = abs(step_fine / fine.value)
        uncertainty = SAFETY_FACTOR * relative_step / denominator

    asymptotic = _asymptotic_ratio(
        fine.value, medium.value, coarse.value, ratio_fine, ratio_coarse, order
    )

    notes: list[str] = []
    low, high = PLAUSIBLE_ORDER
    # A measured rate outside the expected range means one of two opposite
    # things, and telling a reader the wrong one is worse than saying nothing.
    # When the value has all but stopped moving, the remaining differences are
    # close to solver noise and the measured rate becomes erratic -- usually
    # high. That is not a problem: the band is tiny and the answer is settled.
    # It is only a warning when the value is still moving appreciably.
    settled_enough = uncertainty is not None and uncertainty < 0.001
    if not low <= order <= high:
        if settled_enough:
            notes.append(
                f"the measured rate ({order:.2f}) is outside the usual range "
                f"of {low:g} to {high:g}, which is normal for a value that has "
                "essentially stopped moving: the differences left between "
                "meshes are near the solver's own noise. The band is small, "
                "and the value is settled"
            )
        else:
            notes.append(
                f"the measured rate of settling ({order:.2f}) is outside the "
                f"expected range of {low:g} to {high:g}, and the value is "
                "still moving. The meshes are probably not yet fine enough "
                "for this estimate to be reliable, so treat the band as "
                "optimistic"
            )
    if asymptotic is not None and not 0.85 <= asymptotic <= 1.15 and not settled_enough:
        notes.append(
            f"the consistency check came out at {asymptotic:.2f} rather than "
            "near 1.00, which points the same way: the band is probably "
            "optimistic"
        )

    return MetricConvergence(
        metric=metric,
        behaviour=Behaviour.SETTLING,
        levels=ordered,
        observed_order=order,
        extrapolated=extrapolated,
        uncertainty=uncertainty,
        asymptotic_ratio=asymptotic,
        note="; ".join(notes),
    )


def _observed_order(change_ratio: float, ratio_fine: float, ratio_coarse: float) -> float | None:
    """How fast the value is settling, in powers of element size.

    An order of 2 means halving the element size cuts the remaining error by
    four. Where the two refinement steps use the same size ratio, this is a
    single division. Where they do not — and a mesher rarely delivers exactly
    the ratio it was asked for — the order appears on both sides of the
    equation, so it is solved by repeated substitution.
    """
    if change_ratio <= 0.0:
        return None

    log_change = math.log(change_ratio)
    log_ratio_fine = math.log(ratio_fine)

    # Equal refinement ratios make the correction term vanish exactly.
    if abs(ratio_fine - ratio_coarse) < 1.0e-12:
        return log_change / log_ratio_fine

    order = log_change / log_ratio_fine
    for _ in range(_MAX_ITERATIONS):
        fine_term = ratio_fine**order - 1.0
        coarse_term = ratio_coarse**order - 1.0
        if fine_term <= 0.0 or coarse_term <= 0.0:
            return None
        correction = math.log(fine_term / coarse_term)
        updated = (log_change + correction) / log_ratio_fine
        if not math.isfinite(updated):
            return None
        if abs(updated - order) < _ITERATION_TOLERANCE:
            return updated
        order = updated
    return order


def _asymptotic_ratio(
    fine: float,
    medium: float,
    coarse: float,
    ratio_fine: float,
    ratio_coarse: float,
    order: float,
) -> float | None:
    """Consistency check on the uncertainty band.

    Compares the band estimated from the two coarse meshes against the band
    from the two fine meshes, scaled by how much finer they are. A result near
    1.0 means the meshes behave the way the theory expects. A result far from
    1.0 means they do not, and the quoted band is optimistic.
    """
    if fine == 0.0 or medium == 0.0:
        return None
    fine_denominator = ratio_fine**order - 1.0
    coarse_denominator = ratio_coarse**order - 1.0
    if fine_denominator <= 0.0 or coarse_denominator <= 0.0:
        return None

    band_fine = abs((medium - fine) / fine) / fine_denominator
    band_coarse = abs((coarse - medium) / medium) / coarse_denominator
    if band_fine == 0.0:
        return None
    return band_coarse / (ratio_fine**order * band_fine)
