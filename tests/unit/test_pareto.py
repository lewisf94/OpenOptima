from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.failures import EvaluationState, Outcome
from openoptima.domain.objectives import (
    Direction,
    Objective,
    PreferenceModel,
    TradeRule,
)
from openoptima.domain.results import EvaluationResult
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.optimisation.pareto import (
    apply_trade_rules,
    knee_point,
    marginal_rates,
    non_dominated_mask,
    pareto_front,
)

SPACE = DesignSpace((DesignVariable(id="t", minimum=1.0, maximum=10.0, default=5.0),))
OBJECTIVES = (
    Objective(metric="mass_kg", direction=Direction.MINIMISE),
    Objective(metric="factor_of_safety", direction=Direction.MAXIMISE),
)


def result(mass: float, fos: float, run_id: str = "", feasible: bool = True):
    return EvaluationResult(
        design=SPACE.decode({"t": mass * 10}),
        outcome=Outcome.OK,
        state=EvaluationState.ACCEPTED,
        metrics={"mass_kg": mass, "factor_of_safety": fos},
        constraint_violations={} if feasible else {"fos": 0.5},
        run_id=run_id,
    )


def test_non_dominated_mask_on_a_known_set():
    matrix = np.array([[1.0, 1.0], [2.0, 2.0], [1.0, 3.0], [3.0, 1.0]])
    mask = non_dominated_mask(matrix)
    assert mask.tolist() == [True, False, False, False]


def test_identical_points_are_both_kept():
    matrix = np.array([[1.0, 1.0], [1.0, 1.0]])
    assert non_dominated_mask(matrix).sum() == 2


def test_pareto_front_excludes_dominated_designs():
    results = [
        result(0.72, 1.81, "a"),
        result(0.76, 2.05, "b"),
        result(0.80, 1.90, "c"),  # heavier and weaker than b -> dominated
    ]
    front = pareto_front(results, OBJECTIVES)
    assert {r.run_id for r in front} == {"a", "b"}


def test_infeasible_designs_never_reach_the_front():
    results = [
        result(0.50, 3.00, "cheat", feasible=False),
        result(0.72, 1.81, "a"),
    ]
    front = pareto_front(results, OBJECTIVES)
    assert {r.run_id for r in front} == {"a"}


def test_errored_designs_never_reach_the_front():
    bad = result(0.1, 9.0, "boom")
    bad.outcome = Outcome.ERROR
    front = pareto_front([bad, result(0.72, 1.81, "a")], OBJECTIVES)
    assert {r.run_id for r in front} == {"a"}


def test_knee_point_finds_the_elbow():
    """The classic case: cheap gains then expensive ones."""
    front = [
        result(0.72, 1.81, "a"),
        result(0.76, 2.05, "b"),
        result(0.78, 2.31, "c"),  # knee: small mass, big strength gain
        result(0.91, 2.39, "d"),  # much more mass, little strength
    ]
    knee = knee_point(front, OBJECTIVES)
    assert knee is not None
    assert knee.run_id == "c"


def test_knee_point_of_a_tiny_front_is_defined():
    assert knee_point([], OBJECTIVES) is None
    single = [result(1.0, 2.0, "only")]
    assert knee_point(single, OBJECTIVES).run_id == "only"


def test_marginal_rates_expose_the_collapsing_return():
    front = [
        result(0.72, 1.81, "a"),
        result(0.74, 2.07, "b"),
        result(0.87, 2.15, "c"),
    ]
    rates = marginal_rates(front, "mass_kg", "factor_of_safety")
    assert len(rates) == 2
    assert rates[0].give_delta == pytest.approx(0.02)
    assert rates[0].gain_delta == pytest.approx(0.26)
    # The second step costs far more per unit gained than the first.
    assert abs(rates[1].rate) > 10 * abs(rates[0].rate)


class TestTradeRuleSelection:
    """The user's question, end to end: does a small penalty for a big gain get taken?"""

    RULE = (TradeRule("mass_kg", 0.025, "factor_of_safety", 0.1),)

    def test_walks_up_while_each_step_is_worth_it(self):
        front = [
            result(0.72, 1.81, "a"),
            result(0.74, 2.07, "b"),  # 20 g for 0.26 -> worth it
            result(0.87, 2.15, "c"),  # 130 g for 0.08 -> not worth it
        ]
        assert apply_trade_rules(front, self.RULE).run_id == "b"

    def test_stops_immediately_when_the_first_step_is_poor_value(self):
        front = [
            result(0.72, 1.81, "a"),
            result(0.95, 1.85, "b"),  # 230 g for 0.04 -> no
        ]
        assert apply_trade_rules(front, self.RULE).run_id == "a"

    def test_takes_every_step_when_all_are_good_value(self):
        front = [
            result(0.72, 1.80, "a"),
            result(0.73, 2.00, "b"),
            result(0.74, 2.20, "c"),
        ]
        assert apply_trade_rules(front, self.RULE).run_id == "c"

    def test_no_rules_means_no_recommendation(self):
        assert apply_trade_rules([result(1.0, 2.0)], ()) is None


def test_preference_ranking_orders_by_desirability():
    from openoptima.domain.objectives import MetricPreference
    from openoptima.optimisation.pareto import rank_by_preference

    preferences = PreferenceModel(
        desirability=(
            MetricPreference("mass_kg", Direction.MINIMISE, ideal=0.7, acceptable=1.0),
            MetricPreference("factor_of_safety", Direction.MAXIMISE, ideal=2.4, acceptable=1.8),
        )
    )
    front = [result(0.95, 1.85, "poor"), result(0.75, 2.30, "good")]
    ranked = rank_by_preference(front, preferences)
    assert ranked[0][0].run_id == "good"
    assert ranked[0][1] > ranked[1][1]
