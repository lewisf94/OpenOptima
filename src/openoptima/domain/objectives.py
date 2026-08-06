"""Objectives, constraints and the user preference model.

Two rules are load-bearing here:

1. Never collapse a genuine trade-off into one weighted score by default.  A
   weighted sum changes meaning when units change and cannot reach concave
   parts of a Pareto front.  OpenOptima always produces the front; preferences
   *rank* it, they do not replace it.

2. Preferences are expressed the way engineers actually think: an absolute
   limit, a target worth aiming for, a point beyond which more is pointless,
   and an exchange rate between two quantities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    MINIMISE = "minimise"
    MAXIMISE = "maximise"


class Operator(str, Enum):
    LE = "less_than_or_equal"
    GE = "greater_than_or_equal"

    @property
    def symbol(self) -> str:
        return "<=" if self is Operator.LE else ">="


@dataclass(frozen=True)
class Objective:
    metric: str
    direction: Direction = Direction.MINIMISE
    label: str = ""

    @property
    def display_name(self) -> str:
        return self.label or self.metric

    def signed(self, value: float) -> float:
        """Value in a form where smaller is always better."""
        return value if self.direction is Direction.MINIMISE else -value


@dataclass(frozen=True)
class Constraint:
    """A hard limit.  Violating it makes a design infeasible, not merely poor."""

    metric: str
    operator: Operator
    value: float
    label: str = ""
    #: Used to normalise the violation so that constraints in different units
    #: contribute comparably to the optimiser's feasibility measure.
    scale: float | None = None

    @property
    def display_name(self) -> str:
        return self.label or self.metric

    def violation(self, value: float) -> float:
        """Zero when satisfied, positive and normalised when violated."""
        if self.operator is Operator.LE:
            raw = value - self.value
        else:
            raw = self.value - value
        if raw <= 0.0:
            return 0.0
        scale = self.scale if self.scale else max(abs(self.value), 1e-9)
        return raw / scale

    def satisfied(self, value: float, tolerance: float = 0.0) -> bool:
        return self.violation(value) <= tolerance

    def describe(self) -> str:
        return f"{self.display_name} {self.operator.symbol} {self.value:g}"


@dataclass(frozen=True)
class MetricPreference:
    """How the user values one metric.

    ``ideal`` / ``acceptable`` define a desirability ramp; anything better than
    ``ideal`` scores 1.0 (this is the saturation point — the answer to "a factor
    of safety above 2.5 buys me nothing"), anything worse than ``acceptable``
    scores 0.0.
    """

    metric: str
    direction: Direction
    ideal: float
    acceptable: float
    weight: float = 1.0
    #: Shape of the ramp. >1 is intolerant of mid-range values, <1 is forgiving.
    exponent: float = 1.0

    def desirability(self, value: float) -> float:
        if self.direction is Direction.MINIMISE:
            if value <= self.ideal:
                return 1.0
            if value >= self.acceptable:
                return 0.0
            span = self.acceptable - self.ideal
        else:
            if value >= self.ideal:
                return 1.0
            if value <= self.acceptable:
                return 0.0
            span = self.ideal - self.acceptable
        if span <= 0:
            return 1.0
        fraction = abs(value - self.acceptable) / span
        return float(fraction**self.exponent)


@dataclass(frozen=True)
class TradeRule:
    """An explicit exchange rate between two metrics.

    "Accept up to 25 g of extra mass for each 0.1 improvement in factor of
    safety" becomes ``TradeRule('mass_kg', 0.025, 'factor_of_safety', 0.1)``.
    This is what lets the software recognise that a *tiny* penalty on one
    objective is worth a large gain on another — the case a pure Pareto sort
    cannot decide on its own.
    """

    give_metric: str
    give_amount: float
    gain_metric: str
    gain_amount: float

    @property
    def rate(self) -> float:
        """Units of ``give_metric`` the user will pay per unit of ``gain_metric``."""
        if self.gain_amount == 0:
            raise ValueError("TradeRule gain_amount must be non-zero")
        return self.give_amount / self.gain_amount

    def worthwhile(self, give_delta: float, gain_delta: float) -> bool:
        """True when paying ``give_delta`` to gain ``gain_delta`` is acceptable."""
        if gain_delta <= 0:
            return False
        return give_delta <= self.rate * gain_delta + 1e-12


@dataclass(frozen=True)
class PreferenceModel:
    """The four levels of user preference, in increasing subtlety."""

    #: Level 1 — designs outside these are rejected outright.
    hard_limits: tuple[Constraint, ...] = ()
    #: Level 2 — an aspiration point used to steer the search.
    targets: tuple[Constraint, ...] = ()
    #: Level 3 — desirability ramps with saturation.
    desirability: tuple[MetricPreference, ...] = ()
    #: Level 4 — explicit marginal exchange rules.
    trade_rules: tuple[TradeRule, ...] = ()

    def overall_desirability(self, metrics: dict[str, float]) -> float:
        """Weighted geometric mean of the individual desirabilities.

        Geometric, not arithmetic: one unacceptable metric drives the whole
        score to zero, which is the behaviour an engineer expects.
        """
        if not self.desirability:
            return float("nan")
        total_weight = 0.0
        accumulated = 0.0
        for preference in self.desirability:
            if preference.metric not in metrics:
                continue
            value = preference.desirability(metrics[preference.metric])
            if value <= 0.0:
                return 0.0
            accumulated += preference.weight * math.log(value)
            total_weight += preference.weight
        if total_weight == 0.0:
            return float("nan")
        return float(math.exp(accumulated / total_weight))

    def target_point(self) -> dict[str, float]:
        return {constraint.metric: constraint.value for constraint in self.targets}
