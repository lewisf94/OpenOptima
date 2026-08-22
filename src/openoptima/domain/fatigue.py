"""How far the stress swings each cycle, which is what fatigue is driven by.

A part can fail after millions of load cycles at a stress it would shrug off
if the load were applied once. What decides that is not how high the stress
gets but how far it **swings**: a part going from nothing to 100 MPa and back,
over and over, is in a different situation from one sitting between 45 and
55 MPa, even though the peak is nearly the same.

So a cycle is described by naming two of the load cases the project already
has -- one at each end of the swing -- and two numbers come out of the pair:

* the **amplitude**, half the difference between the two ends. Always a
  magnitude, never negative: it is how far the stress moves, not which way.
* the **mean**, the middle of the swing. This one keeps its sign, and the
  sign matters more than its size: a mean that pulls the material apart holds
  a crack open and makes the swing far more damaging, while one that presses
  it together holds the crack shut.

**The whole reason this module exists rather than a two-line subtraction.**
The obvious way to get a swing out of what OpenOptima already reports is to
subtract one load case's von Mises stress from the other's. Von Mises throws
away the *direction* of the stress and keeps only its size, and that is fatal
here. Measured on the example L-bracket, top of the cycle at full load:

    bottom of cycle   from von Mises   from the tensors     error
      +0.5 x load          17.9189           17.9189         0.0%
       0   (off load)      35.8378           35.8378         0.0%
      -0.25 x load         26.8783           44.7972       -40.0%
      -0.5  x load         17.9189           53.7567       -66.7%
      -1.0  x load          0.0000           71.6756      -100.0%

It is exact while the load never reverses, and then collapses. At the bottom
of that table the load is fully reversed -- pushed as hard one way as the
other -- and von Mises reports the two ends as **identical to every digit**,
so the swing reads as zero and the part appears to last for ever. Every error
is in the direction that says the part is safe.

That is the worst possible place to be wrong, because fully reversed loading
is exactly what a vibrating part lives in, and vibration is the usual reason
anybody asks about fatigue at all. It is also invisible to casual testing:
the method is exactly right for an on-off load, which is what anybody would
try first.

The fix is to work with the stress **tensor** -- all six numbers, directions
kept -- subtract those, and only then reduce to a single number.

This module holds the description of a cycle. ``results/fatigue.py`` does the
arithmetic on real fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EquivalentStress(str, Enum):
    """How a six-number stress state is turned into one signed number.

    Both of these are von Mises stress -- the usual single number for how hard
    a material is being worked -- given a sign to say whether the material is
    being pulled apart or pressed together. They differ in where the sign
    comes from, and this is an engineering choice rather than a detail:
    measured on the example L-bracket they disagree on 137 of 19 787 nodes.
    On that part every disagreement was in a lightly loaded corner, at 5.70
    MPa or below against a 35.84 MPa peak, and the node that governs agreed
    either way -- but there is no guarantee of that on another part.
    """

    #: Signed by whether the material is being pulled apart or squeezed on
    #: average, over all directions at once.
    SIGNED_MISES_TRACE = "signed_mises_trace"
    #: Signed by the single strongest direction, whichever way it acts. Closer
    #: to what opens a crack, and the more conservative of the two where a
    #: strong pull and a strong push nearly cancel.
    SIGNED_MISES_MAX_PRINCIPAL = "signed_mises_abs_max_principal"


@dataclass(frozen=True)
class FatigueCurve:
    """How many cycles the material survives at a given swing.

    This is an **S-N curve**, also called a Wöhler curve: a published table,
    measured on test pieces, of how long a material lasts at each stress
    swing. Four numbers describe the usual shape.

    **OpenOptima has no default for any of them, and will not invent one.**
    A fatigue curve belongs to a material, a surface finish, a temperature
    and a failure probability all at once. A default would look
    authoritative and be wrong for almost every real material.

    Two honesty notes that are part of the answer rather than small print.
    Fatigue life from a curve like this is commonly out by a **factor of
    three** even when everything is done properly, so a life quoted to three
    significant figures implies a precision that does not exist. And an
    as-printed surface is rougher than a machined one, which lowers fatigue
    strength by more than layer weakness does -- a curve measured on polished
    test pieces flatters a printed part.
    """

    #: The swing the material survives indefinitely, in MPa. ``SD`` in the
    #: usual notation.
    endurance_stress: float
    #: The number of cycles at which that limit is quoted. ``ND``.
    endurance_cycles: float
    #: How fast life falls away above that limit. ``k_1``. Life goes as
    #: ``endurance_cycles * (endurance_stress / swing) ** slope``, so a
    #: **larger** value punishes an overload harder -- at twice the endurance
    #: stress, a slope of 3 leaves an eighth of the cycles and a slope of 10
    #: leaves about a thousandth.
    slope: float
    #: How the curve continues *below* the endurance limit. ``None`` means it
    #: goes flat -- the classic endurance limit, where a small enough swing
    #: never breaks the part. Give a number for a curve that keeps falling,
    #: which is what steels do in a corrosive setting and what aluminium does
    #: always.
    slope_beyond: float | None = None
    #: How much a mean stress that pulls the material apart matters, on the
    #: FKM scale. Required whenever a life is asked for: a swing about a
    #: tensile mean is more damaging than the same swing about zero, and
    #: assuming otherwise is wrong in the unsafe direction. If your cycle is
    #: fully reversed this has no effect, so stating it costs nothing.
    mean_stress_sensitivity: float | None = None
    #: The second FKM segment. Defaults to a third of the first, which is the
    #: FKM guideline's own convention rather than a guess by this project.
    mean_stress_sensitivity_2: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("endurance_stress", self.endurance_stress),
            ("endurance_cycles", self.endurance_cycles),
            ("slope", self.slope),
        ):
            if value <= 0.0:
                raise ValueError(f"fatigue curve {name} must be above zero, got {value!r}")
        if self.slope_beyond is not None and self.slope_beyond <= 0.0:
            raise ValueError("fatigue curve slope_beyond must be above zero when given")
        if self.mean_stress_sensitivity is not None and self.mean_stress_sensitivity < 0.0:
            raise ValueError("mean_stress_sensitivity cannot be negative")

    @property
    def second_sensitivity(self) -> float:
        """The FKM second segment, defaulted the way the guideline does."""
        if self.mean_stress_sensitivity_2 is not None:
            return self.mean_stress_sensitivity_2
        return (self.mean_stress_sensitivity or 0.0) / 3.0

    def digest_fields(self) -> dict[str, object]:
        return {
            "SD": self.endurance_stress,
            "ND": self.endurance_cycles,
            "k1": self.slope,
            "k2": self.slope_beyond,
            "M": self.mean_stress_sensitivity,
            "M2": self.mean_stress_sensitivity_2,
        }


@dataclass(frozen=True)
class LoadCycle:
    """One repeating swing, described by the two load cases at its ends.

    Order does not matter: the amplitude is a magnitude either way, and the
    mean is the middle of the pair whichever way round they are given.
    """

    name: str
    #: The ids of the two load cases at the ends of the swing.
    between: tuple[str, str]
    #: How many of this swing the part has to survive. Only needed to add up
    #: damage across several different cycles; a single cycle's life is
    #: reported in cycles regardless.
    repeats: float | None = None

    def __post_init__(self) -> None:
        if len(self.between) != 2:
            raise ValueError(
                f"Load cycle {self.name!r} needs exactly two load cases, one at each "
                f"end of the swing; got {len(self.between)}"
            )
        if self.between[0] == self.between[1]:
            raise ValueError(
                f"Load cycle {self.name!r} names the same load case at both ends "
                f"({self.between[0]!r}), so nothing swings and the stress never "
                f"changes. Name the two ends of the cycle."
            )

        if self.repeats is not None and self.repeats <= 0.0:
            raise ValueError(
                f"Load cycle {self.name!r} repeats {self.repeats!r} times, which is not "
                f"a number of cycles. Leave it out if the part only has to survive one "
                f"kind of swing."
            )

    def digest_fields(self) -> dict[str, object]:
        return {"name": self.name, "between": list(self.between), "repeats": self.repeats}


@dataclass(frozen=True)
class FatigueSettings:
    """Which swings the part has to survive, and how to measure them."""

    enabled: bool = False
    cycles: tuple[LoadCycle, ...] = field(default_factory=tuple)
    equivalent_stress: EquivalentStress = EquivalentStress.SIGNED_MISES_TRACE
    #: The material's S-N curve. Without one OpenOptima reports how far the
    #: stress swings and stops there, which is a real and useful number. With
    #: one it goes on to a life in cycles.
    curve: FatigueCurve | None = None

    def __post_init__(self) -> None:
        if self.enabled and not self.cycles:
            raise ValueError(
                "fatigue is switched on but no load cycle is described. A cycle "
                "names the two load cases at the ends of one swing."
            )
        seen: set[str] = set()
        for cycle in self.cycles:
            if cycle.name in seen:
                raise ValueError(f"Two load cycles are both called {cycle.name!r}")
            seen.add(cycle.name)

    @property
    def load_cases_used(self) -> tuple[str, ...]:
        names: list[str] = []
        for cycle in self.cycles:
            names.extend(cycle.between)
        return tuple(dict.fromkeys(names))

    def digest_fields(self) -> dict[str, object]:
        """Everything here can change a number, so all of it is hashed."""
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "equivalent_stress": self.equivalent_stress.value,
            "cycles": [cycle.digest_fields() for cycle in self.cycles],
            "curve": None if self.curve is None else self.curve.digest_fields(),
        }
