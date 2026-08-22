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
class LoadCycle:
    """One repeating swing, described by the two load cases at its ends.

    Order does not matter: the amplitude is a magnitude either way, and the
    mean is the middle of the pair whichever way round they are given.
    """

    name: str
    #: The ids of the two load cases at the ends of the swing.
    between: tuple[str, str]

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

    def digest_fields(self) -> dict[str, object]:
        return {"name": self.name, "between": list(self.between)}


@dataclass(frozen=True)
class FatigueSettings:
    """Which swings the part has to survive, and how to measure them."""

    enabled: bool = False
    cycles: tuple[LoadCycle, ...] = field(default_factory=tuple)
    equivalent_stress: EquivalentStress = EquivalentStress.SIGNED_MISES_TRACE

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
        }
