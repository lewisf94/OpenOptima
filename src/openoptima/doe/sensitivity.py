"""Sensitivity analysis over a completed DOE.

Deliberately simple and honest: rank-order (Spearman) correlations between each
variable and each metric, plus a linear R^2.  Rank correlation is used because
the relationships here are usually monotonic but rarely linear — stiffness goes
as thickness cubed — and Pearson would understate a strong but curved effect.

This is screening, not attribution.  It tells you which variables are worth
keeping in the study; it does not resolve interactions.  Anything stronger
needs a variance-based method (Sobol indices), which needs far more samples.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from ..domain.results import EvaluationResult
from ..domain.variables import DesignSpace


@dataclass(frozen=True)
class VariableEffect:
    variable: str
    metric: str
    spearman: float
    p_value: float
    linear_r2: float
    #: Metric change across the variable's range, from a linear fit.
    span_effect: float

    @property
    def strength(self) -> float:
        return abs(self.spearman)

    def describe(self) -> str:
        direction = "increases" if self.spearman > 0 else "decreases"
        significance = "" if self.p_value < 0.05 else " (not significant, p>=0.05)"
        return (
            f"{self.variable}: {direction} {self.metric} "
            f"(rho={self.spearman:+.3f}, R2={self.linear_r2:.3f}){significance}"
        )


@dataclass(frozen=True)
class SensitivityReport:
    metric: str
    effects: tuple[VariableEffect, ...]
    sample_count: int

    @property
    def ranked(self) -> tuple[VariableEffect, ...]:
        return tuple(sorted(self.effects, key=lambda e: -e.strength))

    def unimportant(self, threshold: float = 0.15) -> tuple[str, ...]:
        """Variables with almost no measured influence on this metric."""
        return tuple(e.variable for e in self.effects if e.strength < threshold)


def analyse(
    results: list[EvaluationResult],
    space: DesignSpace,
    metrics: list[str],
    *,
    include_infeasible: bool = True,
) -> dict[str, SensitivityReport]:
    """Correlate each design variable against each metric.

    Infrastructure errors are always excluded — they carry no information about
    the design.  Infeasible designs are included by default, because excluding
    them biases the sample towards the feasible region and hides the very
    variables that drive designs out of it.
    """
    usable = [
        r
        for r in results
        if r.metrics and (include_infeasible or r.feasible) and r.outcome.value != "error"
    ]
    reports: dict[str, SensitivityReport] = {}
    if len(usable) < 4:
        return reports

    matrix = np.array([r.design.to_array() for r in usable], dtype=float)

    for metric in metrics:
        values = np.array([r.metrics.get(metric, np.nan) for r in usable], dtype=float)
        finite = np.isfinite(values)
        if finite.sum() < 4:
            continue
        effects: list[VariableEffect] = []
        for column, variable in enumerate(space.ids):
            x = matrix[finite, column]
            y = values[finite]
            if np.allclose(x, x[0]):
                continue
            rho, p_value = stats.spearmanr(x, y)
            slope, _intercept, r_value, _p, _stderr = stats.linregress(x, y)
            effects.append(
                VariableEffect(
                    variable=variable,
                    metric=metric,
                    spearman=float(rho) if np.isfinite(rho) else 0.0,
                    p_value=float(p_value) if np.isfinite(p_value) else 1.0,
                    linear_r2=float(r_value**2),
                    span_effect=float(slope * (x.max() - x.min())),
                )
            )
        if effects:
            reports[metric] = SensitivityReport(
                metric=metric, effects=tuple(effects), sample_count=int(finite.sum())
            )
    return reports


def failure_summary(results: list[EvaluationResult]) -> dict[str, int]:
    """How the DOE failed, by code. A high mesh-failure rate is a setup problem."""
    summary: dict[str, int] = {}
    for result in results:
        if result.failure_code is not None:
            summary[result.failure_code.value] = summary.get(result.failure_code.value, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: -item[1]))
