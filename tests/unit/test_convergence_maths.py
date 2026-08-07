"""The convergence arithmetic, checked against sequences with known answers.

Every case here is built from a formula whose exact answer is known in advance,
so a wrong result is a real defect and not a judgement call. No CAE tool is
needed to run these.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.convergence import (
    Behaviour,
    GridLevel,
    analyse_metric,
    representative_size,
)


def _levels(sizes: list[float], values: list[float]) -> list[GridLevel]:
    return [
        GridLevel(
            label=f"h={size:g}",
            representative_size=size,
            node_count=int(1000 / size**3),
            element_count=int(500 / size**3),
            value=value,
        )
        for size, value in zip(sizes, values, strict=True)
    ]


def test_second_order_sequence_recovers_order_two_and_the_exact_limit():
    """f(h) = 10 + h^2, sampled at h = 1, 2, 4.

    The answer a perfect mesh would give is exactly 10, and the rate of
    settling is exactly 2. Both must come back exactly, because this sequence
    is the definition of second-order convergence.
    """
    exact, coefficient = 10.0, 1.0
    sizes = [1.0, 2.0, 4.0]
    values = [exact + coefficient * h**2 for h in sizes]
    assert values == [11.0, 14.0, 26.0]

    result = analyse_metric("displacement_max_mm", _levels(sizes, values))

    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(2.0, rel=1e-9)
    assert result.extrapolated == pytest.approx(exact, rel=1e-9)
    assert result.order_is_plausible
    assert result.uncertainty is not None and result.uncertainty > 0


def test_first_order_sequence_recovers_order_one():
    exact = 4.0
    sizes = [1.0, 2.0, 4.0]
    values = [exact + 0.5 * h for h in sizes]

    result = analyse_metric("stress_max_mpa", _levels(sizes, values))

    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(1.0, rel=1e-9)
    assert result.extrapolated == pytest.approx(exact, rel=1e-9)


def test_uneven_refinement_still_recovers_the_true_order():
    """The mesher never gives exactly the size ratio it was asked for.

    With unequal ratios the order appears on both sides of the equation and has
    to be solved by repeated substitution. The data here is still exactly
    second order, so the answer must still be 2 despite the uneven spacing.
    """
    exact = 10.0
    sizes = [1.0, 2.5, 4.0]
    values = [exact + h**2 for h in sizes]

    result = analyse_metric("displacement_max_mm", _levels(sizes, values))

    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(2.0, rel=1e-6)
    assert result.extrapolated == pytest.approx(exact, rel=1e-6)


def test_a_runaway_peak_stress_is_reported_as_diverging_not_converging():
    """The case this whole module exists to get right.

    At a sharp internal corner the true stress is infinite, so the computed
    peak grows with every refinement. Here the value follows h^-0.5, which
    grows without limit as the mesh is refined.

    The textbook formula for the observed order takes an absolute value, which
    would turn this into a positive order and a confident extrapolated value
    for a quantity that has no limit at all. It must instead be reported as
    diverging, and the measured order must come back negative -- exactly the
    exponent of the runaway.
    """
    sizes = [1.0, 2.0, 4.0]
    values = [100.0 * h**-0.5 for h in sizes]
    assert values[0] > values[1] > values[2]  # grows as the mesh refines

    result = analyse_metric("stress_raw_max_mpa", _levels(sizes, values))

    assert result.behaviour is Behaviour.DIVERGING
    assert result.observed_order == pytest.approx(-0.5, rel=1e-9)
    assert result.extrapolated is None, "a diverging quantity has no limit to quote"
    assert result.uncertainty is None
    assert "infinite" in result.note


def test_a_value_that_moves_then_comes_back_is_reported_as_oscillating():
    result = analyse_metric("stress_max_mpa", _levels([1.0, 2.0, 4.0], [10.0, 12.0, 11.0]))

    assert result.behaviour is Behaviour.OSCILLATING
    assert result.extrapolated is None
    assert result.uncertainty is None


def test_identical_values_are_flat_not_an_error():
    """Mass barely changes with the mesh, and that is not a failure."""
    result = analyse_metric("mass_kg", _levels([1.0, 2.0, 4.0], [0.5, 0.5, 0.5]))

    assert result.behaviour is Behaviour.FLAT
    assert result.extrapolated == pytest.approx(0.5)
    assert result.uncertainty == 0.0


def test_two_meshes_is_not_enough_to_say_anything():
    result = analyse_metric("displacement_max_mm", _levels([1.0, 2.0], [11.0, 14.0]))

    assert result.behaviour is Behaviour.NOT_ENOUGH_DATA
    assert result.observed_order is None


def test_meshes_of_the_same_size_are_rejected_rather_than_divided_by_zero():
    result = analyse_metric("displacement_max_mm", _levels([2.0, 2.0, 2.0], [11.0, 14.0, 26.0]))

    assert result.behaviour is Behaviour.NOT_ENOUGH_DATA
    assert "not distinct enough" in result.note


def test_non_finite_values_are_dropped_before_the_arithmetic():
    """An infinite factor of safety is a real, meaningful value elsewhere.

    It must not reach the logarithms here and produce a nonsense order.
    """
    levels = _levels([1.0, 2.0, 4.0, 8.0], [11.0, 14.0, 26.0, float("inf")])

    result = analyse_metric("factor_of_safety", levels)

    assert result.behaviour is Behaviour.SETTLING
    assert len(result.levels) == 3
    assert result.observed_order == pytest.approx(2.0, rel=1e-9)


def test_levels_are_sorted_finest_first_whatever_order_they_arrive_in():
    exact = 10.0
    sizes = [4.0, 1.0, 2.0]
    values = [exact + h**2 for h in sizes]

    result = analyse_metric("displacement_max_mm", _levels(sizes, values))

    assert [level.representative_size for level in result.levels] == [1.0, 2.0, 4.0]
    assert result.finest_value == pytest.approx(11.0)
    assert result.extrapolated == pytest.approx(exact, rel=1e-9)


def test_an_implausible_order_is_flagged_even_though_it_settles():
    """A very high measured order means the meshes are not yet fine enough.

    The arithmetic still produces a band. The point is that the band is
    optimistic, and the report must say so rather than quoting it plainly.
    """
    sizes = [1.0, 2.0, 4.0]
    values = [10.0 + h**6 for h in sizes]

    result = analyse_metric("displacement_max_mm", _levels(sizes, values))

    assert result.behaviour is Behaviour.SETTLING
    assert result.observed_order == pytest.approx(6.0, rel=1e-9)
    assert not result.order_is_plausible
    assert "outside the expected range" in result.note


def test_uncertainty_shrinks_as_the_answer_settles():
    """A sequence that has nearly settled must report a tighter band than one
    that is still moving. This is the property a reader will actually use."""
    still_moving = analyse_metric(
        "displacement_max_mm", _levels([1.0, 2.0, 4.0], [10.0 + h**2 for h in (1.0, 2.0, 4.0)])
    )
    nearly_settled = analyse_metric(
        "displacement_max_mm",
        _levels([1.0, 2.0, 4.0], [10.0 + 0.001 * h**2 for h in (1.0, 2.0, 4.0)]),
    )

    assert still_moving.uncertainty is not None
    assert nearly_settled.uncertainty is not None
    assert nearly_settled.uncertainty < still_moving.uncertainty


def test_spread_is_reported_whatever_the_behaviour():
    """The plainest number in the report, and the one that stops a false alarm.

    A real run of the L-bracket produced a raw peak stress that wobbled by
    0.05% between the three finest meshes. That is oscillation by the strict
    definition, and reporting it as "unsteady" alone reads as a problem. The
    total spread across every mesh is what tells the reader it is not one.
    """
    wobbling = analyse_metric("stress_raw_max_mpa", _levels([1.0, 2.0, 4.0], [100.1, 99.9, 100.0]))

    assert wobbling.behaviour is Behaviour.OSCILLATING
    assert wobbling.spread is not None
    assert wobbling.spread == pytest.approx(0.2 / 100.1, rel=1e-6)

    moving = analyse_metric("stress_max_mpa", _levels([1.0, 2.0, 4.0], [80.0, 100.0, 130.0]))
    assert moving.spread == pytest.approx(50.0 / 130.0, rel=1e-6)
    assert moving.spread > wobbling.spread


def test_a_settled_value_with_an_odd_rate_is_not_called_a_problem():
    """Regression test on the wording, from a real run.

    When a value has all but stopped moving, the differences left between
    meshes approach the solver's own noise and the measured rate becomes
    erratic -- usually high. The first version of this module told the reader
    that meant "the meshes are probably not yet fine enough", which is the
    opposite of the truth: the band was 0.0036% and the value was settled.

    A high rate with a wide band is a genuine warning. A high rate with a tiny
    band is not, and must not be worded as one.
    """
    settled = analyse_metric(
        "displacement_max_mm",
        _levels([1.0, 2.0, 4.0], [10.0 + 1e-5 * h**6 for h in (1.0, 2.0, 4.0)]),
    )

    assert settled.behaviour is Behaviour.SETTLING
    assert not settled.order_is_plausible, "the rate really is outside the usual range"
    assert settled.uncertainty is not None and settled.uncertainty < 0.001
    assert "essentially stopped moving" in settled.note
    assert "not yet fine enough" not in settled.note


def test_a_still_moving_value_with_an_odd_rate_is_still_flagged():
    """The other side of the same distinction: this one is a real warning."""
    moving = analyse_metric(
        "displacement_max_mm", _levels([1.0, 2.0, 4.0], [10.0 + h**6 for h in (1.0, 2.0, 4.0)])
    )

    assert moving.behaviour is Behaviour.SETTLING
    assert moving.uncertainty is not None and moving.uncertainty > 0.001
    assert "not yet fine enough" in moving.note


def test_representative_size_is_the_average_element_edge():
    """A cube of side 10 chopped into 1000 elements has 1 mm elements."""
    assert representative_size(1000.0, 1000) == pytest.approx(1.0)
    assert representative_size(8000.0, 1000) == pytest.approx(2.0)
    assert math.isnan(representative_size(0.0, 1000))
    assert math.isnan(representative_size(1000.0, 0))


def test_the_module_reports_behaviour_and_never_a_verdict_on_trust():
    """AGENTS.md forbids the software deciding a mesh is good enough.

    That judgement is the engineer's. This test pins the vocabulary so a later
    change cannot quietly introduce a 'converged: yes' verdict.
    """
    names = {behaviour.value for behaviour in Behaviour}
    for banned in ("converged", "trusted", "acceptable", "good", "pass", "ok"):
        assert banned not in names
