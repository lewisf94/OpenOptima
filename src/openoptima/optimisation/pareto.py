"""Pareto analysis and decision support.

The optimiser's job is to produce the trade-off surface.  Choosing a point on it
is the engineer's job, and this module exists to make that choice informed
rather than arbitrary.

The interesting question — the one a pure Pareto sort cannot answer — is the one
about marginal rates: *"it may be worth a little more mass for a lot more
strength, but the code does not know that."*  Three tools here address it:

:func:`knee_point`     finds where the trade-off curve bends, i.e. where you
                       stop getting a good return for what you pay;
:func:`marginal_rates` reports, for each neighbouring pair on the front, what
                       one improvement actually costs in the other objective;
:func:`apply_trade_rules` applies the user's own stated exchange rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..domain.objectives import Direction, Objective, PreferenceModel, TradeRule
from ..domain.results import EvaluationResult


def objective_matrix(
    results: list[EvaluationResult], objectives: tuple[Objective, ...]
) -> np.ndarray:
    """Objective values in minimise-is-better form, shape (n, m)."""
    rows = []
    for result in results:
        rows.append([objective.signed(result.metric(objective.metric)) for objective in objectives])
    return np.array(rows, dtype=float)


def non_dominated_mask(matrix: np.ndarray) -> np.ndarray:
    """Boolean mask of the Pareto-optimal rows (all objectives minimised)."""
    count = len(matrix)
    mask = np.ones(count, dtype=bool)
    for index in range(count):
        if not mask[index]:
            continue
        candidate = matrix[index]
        dominated_by_others = np.all(matrix <= candidate, axis=1) & np.any(
            matrix < candidate, axis=1
        )
        if dominated_by_others.any():
            mask[index] = False
    return mask


def pareto_front(
    results: list[EvaluationResult], objectives: tuple[Objective, ...]
) -> list[EvaluationResult]:
    """Feasible, non-dominated results.

    Only feasible designs are eligible: a design that violates a hard limit is
    not on the trade-off surface however attractive its objectives look.
    """
    feasible = [r for r in results if r.ok and r.feasible and r.metrics]
    if not feasible:
        return []
    matrix = objective_matrix(feasible, objectives)
    finite = np.all(np.isfinite(matrix), axis=1)
    feasible = [r for r, keep in zip(feasible, finite, strict=True) if keep]
    if not feasible:
        return []
    matrix = matrix[finite]
    mask = non_dominated_mask(matrix)
    return [r for r, keep in zip(feasible, mask, strict=True) if keep]


def _normalise(matrix: np.ndarray) -> np.ndarray:
    lower = matrix.min(axis=0)
    upper = matrix.max(axis=0)
    span = np.where(upper - lower > 1e-12, upper - lower, 1.0)
    return (matrix - lower) / span


def knee_point(
    front: list[EvaluationResult], objectives: tuple[Objective, ...]
) -> EvaluationResult | None:
    """The best-compromise design, by maximum distance from the chord.

    On a two-objective front this is the classic "elbow": the point furthest
    from the line joining the two extremes, i.e. where a small concession in one
    objective stops buying a large gain in the other.  For more than two
    objectives it degrades gracefully to the point closest to the ideal corner.
    """
    if not front:
        return None
    if len(front) <= 2:
        return front[0]

    matrix = _normalise(objective_matrix(front, objectives))

    if matrix.shape[1] == 2:
        order = np.argsort(matrix[:, 0])
        ordered = matrix[order]
        start, end = ordered[0], ordered[-1]
        chord = end - start
        length = float(np.linalg.norm(chord))
        if length < 1e-12:
            return front[0]
        distances = []
        for point in ordered:
            offset = point - start
            cross = abs(float(chord[0] * offset[1] - chord[1] * offset[0]))
            distances.append(cross / length)
        best = int(np.argmax(distances))
        return front[int(order[best])]

    distances = np.linalg.norm(matrix, axis=1)
    return front[int(np.argmin(distances))]


@dataclass(frozen=True)
class MarginalRate:
    """What moving between two adjacent front designs costs and buys."""

    from_index: int
    to_index: int
    give_metric: str
    give_delta: float
    gain_metric: str
    gain_delta: float

    @property
    def rate(self) -> float:
        """Units of ``give`` paid per unit of ``gain``."""
        if abs(self.gain_delta) < 1e-15:
            return float("inf")
        return self.give_delta / self.gain_delta

    def describe(self, give_unit: str = "", gain_unit: str = "") -> str:
        return (
            f"{self.give_delta:+.4g}{give_unit} {self.give_metric} "
            f"buys {self.gain_delta:+.4g}{gain_unit} {self.gain_metric}"
        )


def marginal_rates(
    front: list[EvaluationResult], give_metric: str, gain_metric: str
) -> list[MarginalRate]:
    """Exchange rates between neighbouring designs on the front.

    This is the evidence behind "20 g buys 0.26 of factor of safety, but the
    next 130 g only buys 0.08" — the sentence that actually helps someone
    choose.
    """
    usable = [
        r
        for r in front
        if np.isfinite(r.metric(give_metric)) and np.isfinite(r.metric(gain_metric))
    ]
    if len(usable) < 2:
        return []
    order = sorted(range(len(usable)), key=lambda i: usable[i].metric(give_metric))
    rates: list[MarginalRate] = []
    for position in range(len(order) - 1):
        first, second = usable[order[position]], usable[order[position + 1]]
        rates.append(
            MarginalRate(
                from_index=order[position],
                to_index=order[position + 1],
                give_metric=give_metric,
                give_delta=second.metric(give_metric) - first.metric(give_metric),
                gain_metric=gain_metric,
                gain_delta=second.metric(gain_metric) - first.metric(gain_metric),
            )
        )
    return rates


def apply_trade_rules(
    front: list[EvaluationResult], rules: tuple[TradeRule, ...]
) -> EvaluationResult | None:
    """Walk the front while the user's stated exchange rate says it is worth it.

    Start from the cheapest design and keep stepping to the next one for as long
    as each step's cost is within what the user said they would pay.  Stop at
    the first step that is not worth it.  This is the direct answer to "the code
    may not know a tiny pressure/mass penalty is worth a big performance gain" —
    it knows because the user said so, in engineering terms rather than weights.
    """
    if not front or not rules:
        return None
    rule = rules[0]
    rates = marginal_rates(front, rule.give_metric, rule.gain_metric)
    if not rates:
        return None

    ordered = sorted(front, key=lambda r: r.metric(rule.give_metric))
    chosen = ordered[0]
    for position, rate in enumerate(rates):
        if rate.gain_delta > 0 and rule.worthwhile(rate.give_delta, rate.gain_delta):
            chosen = ordered[position + 1]
        else:
            break
    return chosen


def rank_by_preference(
    front: list[EvaluationResult], preferences: PreferenceModel
) -> list[tuple[EvaluationResult, float]]:
    """Order the front by overall desirability, best first."""
    if not preferences.desirability:
        return [(result, float("nan")) for result in front]
    scored = [(result, preferences.overall_desirability(result.metrics)) for result in front]
    scored.sort(key=lambda pair: -pair[1] if np.isfinite(pair[1]) else 0.0)
    return scored


def distance_to_targets(result: EvaluationResult, preferences: PreferenceModel) -> float:
    """Normalised distance from a result to the user's aspiration point."""
    targets = preferences.target_point()
    if not targets:
        return float("nan")
    total = 0.0
    for metric, target in targets.items():
        value = result.metric(metric)
        if not np.isfinite(value):
            continue
        scale = abs(target) if abs(target) > 1e-12 else 1.0
        total += ((value - target) / scale) ** 2
    return float(np.sqrt(total))


def summarise_front(
    front: list[EvaluationResult],
    objectives: tuple[Objective, ...],
    preferences: PreferenceModel,
) -> dict[str, object]:
    """Everything needed to present the trade-off to a user."""
    knee = knee_point(front, objectives)
    ranked = rank_by_preference(front, preferences)
    preferred = apply_trade_rules(front, preferences.trade_rules)
    return {
        "size": len(front),
        "objectives": [
            {
                "metric": o.metric,
                "direction": o.direction.value,
                "label": o.display_name,
                "best": (
                    min(r.metric(o.metric) for r in front)
                    if front and o.direction is Direction.MINIMISE
                    else (max(r.metric(o.metric) for r in front) if front else None)
                ),
            }
            for o in objectives
        ],
        "knee_run_id": knee.run_id if knee else None,
        "most_desirable_run_id": ranked[0][0].run_id if ranked else None,
        "trade_rule_choice_run_id": preferred.run_id if preferred else None,
    }
