"""Constraints, desirability and the trade rules.

The trade-rule tests encode the specific question this feature exists to answer:
that a *small* penalty on one objective can be worth a *large* gain on another,
and that the software should know it because the engineer said so.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.objectives import (
    Constraint,
    Direction,
    MetricPreference,
    Objective,
    Operator,
    PreferenceModel,
    TradeRule,
)


class TestConstraint:
    def test_satisfied_constraint_has_zero_violation(self):
        constraint = Constraint(metric="fos", operator=Operator.GE, value=2.0)
        assert constraint.violation(2.5) == 0.0
        assert constraint.satisfied(2.5)

    def test_violation_is_normalised_by_the_limit(self):
        constraint = Constraint(metric="fos", operator=Operator.GE, value=2.0)
        assert constraint.violation(1.0) == pytest.approx(0.5)

    def test_less_than_constraint(self):
        constraint = Constraint(metric="mass", operator=Operator.LE, value=1.0)
        assert constraint.violation(0.9) == 0.0
        assert constraint.violation(1.5) == pytest.approx(0.5)

    def test_explicit_scale_overrides_normalisation(self):
        constraint = Constraint(metric="disp", operator=Operator.LE, value=0.25, scale=1.0)
        assert constraint.violation(0.75) == pytest.approx(0.5)

    def test_constraints_in_different_units_compare_sensibly(self):
        """A 10% overshoot should score the same regardless of units."""
        stress = Constraint(metric="s", operator=Operator.LE, value=160.0)
        mass = Constraint(metric="m", operator=Operator.LE, value=0.8)
        assert stress.violation(176.0) == pytest.approx(mass.violation(0.88))


class TestObjective:
    def test_maximise_is_negated_so_smaller_is_always_better(self):
        objective = Objective(metric="fos", direction=Direction.MAXIMISE)
        assert objective.signed(3.0) == -3.0

    def test_minimise_passes_through(self):
        objective = Objective(metric="mass", direction=Direction.MINIMISE)
        assert objective.signed(3.0) == 3.0


class TestDesirability:
    def test_saturates_at_ideal(self):
        """Better than ideal buys nothing — the answer to 'FoS above 2.5 is pointless'."""
        preference = MetricPreference(
            metric="fos", direction=Direction.MAXIMISE, ideal=2.5, acceptable=1.8
        )
        assert preference.desirability(2.5) == 1.0
        assert preference.desirability(4.0) == 1.0

    def test_zero_below_acceptable(self):
        preference = MetricPreference(
            metric="fos", direction=Direction.MAXIMISE, ideal=2.5, acceptable=1.8
        )
        assert preference.desirability(1.5) == 0.0

    def test_ramps_between(self):
        preference = MetricPreference(
            metric="mass", direction=Direction.MINIMISE, ideal=1.0, acceptable=2.0
        )
        assert preference.desirability(1.5) == pytest.approx(0.5)

    def test_exponent_shapes_the_ramp(self):
        forgiving = MetricPreference(
            metric="m", direction=Direction.MINIMISE, ideal=1.0, acceptable=2.0, exponent=0.5
        )
        strict = MetricPreference(
            metric="m", direction=Direction.MINIMISE, ideal=1.0, acceptable=2.0, exponent=2.0
        )
        assert forgiving.desirability(1.5) > strict.desirability(1.5)


class TestOverallDesirability:
    def test_one_unacceptable_metric_zeroes_the_score(self):
        """Geometric, not arithmetic: a good number must not rescue a bad one."""
        model = PreferenceModel(
            desirability=(
                MetricPreference("mass", Direction.MINIMISE, ideal=1.0, acceptable=2.0),
                MetricPreference("fos", Direction.MAXIMISE, ideal=2.5, acceptable=2.0),
            )
        )
        assert model.overall_desirability({"mass": 1.0, "fos": 1.0}) == 0.0

    def test_both_ideal_scores_one(self):
        model = PreferenceModel(
            desirability=(
                MetricPreference("mass", Direction.MINIMISE, ideal=1.0, acceptable=2.0),
                MetricPreference("fos", Direction.MAXIMISE, ideal=2.5, acceptable=2.0),
            )
        )
        assert model.overall_desirability({"mass": 0.5, "fos": 3.0}) == pytest.approx(1.0)

    def test_no_preferences_gives_nan_not_a_misleading_zero(self):
        assert math.isnan(PreferenceModel().overall_desirability({"mass": 1.0}))


class TestTradeRule:
    def rule(self) -> TradeRule:
        # "I will pay 25 g for each 0.1 of factor of safety."
        return TradeRule(
            give_metric="mass_kg",
            give_amount=0.025,
            gain_metric="factor_of_safety",
            gain_amount=0.1,
        )

    def test_rate_is_give_per_unit_gain(self):
        assert self.rule().rate == pytest.approx(0.25)

    def test_small_penalty_for_large_gain_is_worth_it(self):
        """The case the user raised: a tiny cost for a big improvement."""
        assert self.rule().worthwhile(give_delta=0.020, gain_delta=0.26)

    def test_large_penalty_for_small_gain_is_not(self):
        assert not self.rule().worthwhile(give_delta=0.130, gain_delta=0.08)

    def test_exactly_at_the_rate_is_accepted(self):
        assert self.rule().worthwhile(give_delta=0.025, gain_delta=0.1)

    def test_no_gain_is_never_worth_paying_for(self):
        assert not self.rule().worthwhile(give_delta=0.001, gain_delta=0.0)
        assert not self.rule().worthwhile(give_delta=0.0, gain_delta=-0.1)

    def test_zero_gain_amount_is_rejected_at_construction_use(self):
        with pytest.raises(ValueError, match="non-zero"):
            _ = TradeRule("a", 1.0, "b", 0.0).rate
