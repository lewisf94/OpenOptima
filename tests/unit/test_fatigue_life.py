"""Turning a stress swing into a life, and refusing to when it cannot be done.

The arithmetic is pyLife's. What is checked here is that OpenOptima hands it
the right numbers, reads the answer back correctly, and refuses rather than
guesses where a number is missing.

Three things carry real engineering weight and are pinned:

* **The life must follow the curve the engineer supplied**, against the
  closed form ``N = ND * (SD / S) ** k`` rather than against an earlier run.
* **A mean stress that pulls the material apart must shorten the life**, and
  one that presses it together must lengthen it. Getting this backwards, or
  ignoring it, is wrong in the unsafe direction.
* **A missing mean stress sensitivity is refused, not defaulted.** Assuming a
  mean stress does not matter flatters the part.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome, outcome_for
from openoptima.domain.fatigue import FatigueCurve
from openoptima.results.fatigue import CycleStress, cycle_life

from ..conftest import requires_pylife

pytestmark = requires_pylife

#: Endurance limit 100 MPa at ten million cycles, Basquin slope 5.
CURVE = FatigueCurve(
    endurance_stress=100.0,
    endurance_cycles=1.0e7,
    slope=5.0,
    mean_stress_sensitivity=0.3,
)


def _swing(amplitude: float, mean: float = 0.0) -> CycleStress:
    return CycleStress(
        name="cycle",
        between=("a", "b"),
        amplitude_max=amplitude,
        mean_at_worst=mean,
        amplitude_measure=amplitude,
        measure_name="raw maximum",
        amplitude_raw_max=amplitude,
    )


@pytest.mark.parametrize("amplitude", [200.0, 150.0, 120.0, 100.0])
def test_the_life_follows_the_supplied_curve(amplitude) -> None:
    """Against the closed form, not against a recorded number.

    Above the endurance limit a Basquin curve gives
    ``N = ND * (SD / S) ** k``. The cycle here has no mean stress, so the
    correction is a no-op and this isolates the curve itself.
    """
    expected = 1.0e7 * (100.0 / amplitude) ** 5.0
    assert cycle_life(_swing(amplitude), CURVE) == pytest.approx(expected, rel=1.0e-9)


def test_a_swing_below_the_endurance_limit_reports_unlimited_life() -> None:
    """And 'unlimited' means below the limit of the curve supplied, which is
    what the warning on the result says in words."""
    assert math.isinf(cycle_life(_swing(80.0), CURVE))


def test_a_mean_that_pulls_the_material_apart_shortens_the_life() -> None:
    """The direction that matters. A crack held open by a tensile mean grows
    under a swing that a compressive mean would hold shut."""
    neutral = cycle_life(_swing(120.0, mean=0.0), CURVE)
    pulled = cycle_life(_swing(120.0, mean=+60.0), CURVE)
    pressed = cycle_life(_swing(120.0, mean=-60.0), CURVE)

    assert pulled < neutral < pressed


def test_a_cycle_with_no_mean_is_unaffected_by_the_correction() -> None:
    """Which is why requiring the sensitivity costs a fully reversed project
    nothing, and why requiring it always is reasonable."""
    sensitive = FatigueCurve(
        endurance_stress=100.0, endurance_cycles=1.0e7, slope=5.0, mean_stress_sensitivity=0.9
    )
    insensitive = FatigueCurve(
        endurance_stress=100.0, endurance_cycles=1.0e7, slope=5.0, mean_stress_sensitivity=0.0
    )
    assert cycle_life(_swing(150.0), sensitive) == pytest.approx(
        cycle_life(_swing(150.0), insensitive), rel=1.0e-12
    )


def test_a_missing_mean_stress_sensitivity_is_refused_not_assumed() -> None:
    """Prefer an explicit error over a default when the correct value is
    unknowable. Treating a mean stress as harmless flatters the part."""
    bare = FatigueCurve(endurance_stress=100.0, endurance_cycles=1.0e7, slope=5.0)
    with pytest.raises(EvaluationFailure) as caught:
        cycle_life(_swing(120.0, mean=40.0), bare)
    assert caught.value.code is FailureCode.FATIGUE_CURVE_INCOMPLETE
    assert "mean_stress_sensitivity" in str(caught.value)


def test_an_incomplete_curve_is_an_error_never_a_bad_design() -> None:
    """The same curve applies to every design in a study, so a missing number
    is a setup mistake. Feeding it back as a poor design teaches the optimiser
    something false, and retrying it cannot help."""
    from openoptima.domain.failures import is_retryable

    assert outcome_for(FailureCode.FATIGUE_CURVE_INCOMPLETE) is Outcome.ERROR
    assert not is_retryable(FailureCode.FATIGUE_CURVE_INCOMPLETE)


def test_a_larger_slope_punishes_an_overload_harder() -> None:
    """Which way the slope runs, since getting it backwards gives a perfectly
    plausible number.

    Life goes as ``(SD / S) ** k``, and above the limit ``SD / S`` is below
    one -- so a larger exponent gives a shorter life. At twice the endurance
    stress a slope of 3 leaves an eighth of the cycles and a slope of 10
    leaves about a thousandth.
    """
    gentle = FatigueCurve(
        endurance_stress=100.0, endurance_cycles=1.0e7, slope=3.0, mean_stress_sensitivity=0.0
    )
    harsh = FatigueCurve(
        endurance_stress=100.0, endurance_cycles=1.0e7, slope=10.0, mean_stress_sensitivity=0.0
    )
    assert cycle_life(_swing(200.0), harsh) < cycle_life(_swing(200.0), gentle)
    assert cycle_life(_swing(200.0), gentle) == pytest.approx(1.0e7 / 8.0)


def test_the_curve_refuses_numbers_that_are_not_a_curve() -> None:
    for bad in ({"endurance_stress": 0.0}, {"endurance_cycles": -1.0}, {"slope": 0.0}):
        fields = {"endurance_stress": 100.0, "endurance_cycles": 1.0e7, "slope": 5.0} | bad
        with pytest.raises(ValueError, match="above zero"):
            FatigueCurve(**fields)


def test_the_second_fkm_segment_defaults_to_a_third_of_the_first() -> None:
    """The FKM guideline's own convention, not a number invented here."""
    curve = FatigueCurve(
        endurance_stress=100.0, endurance_cycles=1.0e7, slope=5.0, mean_stress_sensitivity=0.3
    )
    assert curve.second_sensitivity == pytest.approx(0.1)
    stated = FatigueCurve(
        endurance_stress=100.0,
        endurance_cycles=1.0e7,
        slope=5.0,
        mean_stress_sensitivity=0.3,
        mean_stress_sensitivity_2=0.15,
    )
    assert stated.second_sensitivity == pytest.approx(0.15)


def _settings(*cycles_and_repeats, curve=CURVE):
    from openoptima.domain.fatigue import FatigueSettings, LoadCycle

    return FatigueSettings(
        enabled=True,
        cycles=tuple(
            LoadCycle(name=name, between=("a", "b"), repeats=repeats)
            for name, repeats in cycles_and_repeats
        ),
        curve=curve,
    )


def test_damage_adds_up_across_cycles() -> None:
    """Miner's rule: each cycle uses a share of the life, and the shares add.

    At 1.0 the part is used up. This is deliberately checked against hand
    arithmetic on lives the previous tests already pinned to the closed form.
    """
    from openoptima.results.fatigue import _life_metrics

    warnings: list[str] = []
    measured = [_swing(200.0), _swing(150.0)]
    measured[0] = CycleStress(**{**measured[0].__dict__, "name": "heavy"})
    measured[1] = CycleStress(**{**measured[1].__dict__, "name": "light"})

    life_heavy = 1.0e7 * (100.0 / 200.0) ** 5.0
    life_light = 1.0e7 * (100.0 / 150.0) ** 5.0

    metrics = _life_metrics(_settings(("heavy", 1.0e5), ("light", 1.0e6)), measured, warnings)
    assert metrics["fatigue_damage"] == pytest.approx(
        1.0e5 / life_heavy + 1.0e6 / life_light, rel=1.0e-9
    )
    # The shortest life governs, and each cycle's own is reported beside it.
    assert metrics["fatigue_life_cycles"] == pytest.approx(life_heavy, rel=1.0e-9)
    assert metrics["fatigue_life_cycles.light"] == pytest.approx(life_light, rel=1.0e-9)


def test_a_partial_damage_total_is_refused_rather_than_understated() -> None:
    """A total built from only some of the cycles is not a smaller total, it
    is a wrong one -- and wrong in the direction that says the part lasts."""
    from openoptima.results.fatigue import _life_metrics

    warnings: list[str] = []
    measured = [
        CycleStress(**{**_swing(200.0).__dict__, "name": "counted"}),
        CycleStress(**{**_swing(150.0).__dict__, "name": "uncounted"}),
    ]
    metrics = _life_metrics(_settings(("counted", 1.0e5), ("uncounted", None)), measured, warnings)
    assert "fatigue_damage" not in metrics
    assert any("uncounted" in w for w in warnings)


def test_every_life_carries_the_caveat_it_needs() -> None:
    """The software cannot check from one mesh that the stress at the hottest
    point has settled, so it says so rather than implying it has."""
    from openoptima.results.fatigue import _life_metrics

    warnings: list[str] = []
    _life_metrics(_settings(("c", None)), [_swing(200.0)], warnings)
    assert any("converge" in w for w in warnings)

    unlimited: list[str] = []
    _life_metrics(_settings(("c", None)), [_swing(50.0)], unlimited)
    assert any("never that the part cannot break" in w for w in unlimited)
