"""A stress swing must survive the load reversing.

The defect these guard against is not a crash. It is a number: taking a stress
range as the difference of two von Mises values reports a fully reversed load
-- the most damaging cycle there is -- as no swing at all, and the part then
appears to last for ever.

Measured on the example L-bracket, top of the cycle at full load:

    bottom of cycle   from von Mises   from the tensors     error
      +0.5 x load          17.9189           17.9189         0.0%
       0   (off load)      35.8378           35.8378         0.0%
      -0.25 x load         26.8783           44.7972       -40.0%
      -0.5  x load         17.9189           53.7567       -66.7%
      -1.0  x load          0.0000           71.6756      -100.0%

Exact while the load never reverses, then collapsing, and every error in the
direction that says the part is safe. ``test_a_fully_reversed_cycle_...``
below fails with exactly 0.0 if the tensor subtraction is replaced by a von
Mises subtraction.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome, outcome_for
from openoptima.domain.fatigue import EquivalentStress, FatigueSettings, LoadCycle
from openoptima.domain.model import StressEvaluation
from openoptima.solvers.base import LoadCaseFields

from ..conftest import requires_pylife

pytestmark = requires_pylife


#: A plain uniaxial pull along x, in MPa: sxx only.
UNIAXIAL = np.array([[100.0, 0.0, 0.0, 0.0, 0.0, 0.0]])


def _fields(case_id: str, tensor: np.ndarray, tags: np.ndarray | None = None) -> LoadCaseFields:
    count = tensor.shape[0]
    node_tags = np.arange(1, count + 1) if tags is None else tags
    return LoadCaseFields(
        load_case_id=case_id,
        node_tags=node_tags,
        displacement=np.zeros((count, 3)),
        von_mises=np.zeros(count),
        reaction_force=(0.0, 0.0, 0.0),
        stress_tensor=tensor,
    )


def _measure(
    cases: dict[str, np.ndarray],
    between: tuple[str, str],
    convention: EquivalentStress = EquivalentStress.SIGNED_MISES_TRACE,
):
    from openoptima.results.fatigue import cycle_stress

    return cycle_stress(
        LoadCycle(name="cycle", between=between),
        {name: _fields(name, tensor) for name, tensor in cases.items()},
        FatigueSettings(
            enabled=True,
            cycles=(LoadCycle(name="cycle", between=between),),
            equivalent_stress=convention,
        ),
        StressEvaluation(measure="raw_max"),
    )


def test_a_fully_reversed_cycle_swings_the_whole_stress_not_zero() -> None:
    """The defect, stated as a number.

    Pushed as hard one way as the other, von Mises is identical at both ends,
    so a range taken from it is zero and the part appears immortal. The real
    swing is the full stress, and the middle of it is nothing.
    """
    measured = _measure({"up": UNIAXIAL, "down": -UNIAXIAL}, ("up", "down"))

    naive = abs(100.0 - 100.0) / 2.0  # what a von Mises difference would give
    assert naive == 0.0

    assert measured.amplitude_max == pytest.approx(100.0)
    assert measured.mean_at_worst == pytest.approx(0.0, abs=1e-9)


def test_an_on_off_cycle_is_half_the_stress_either_way() -> None:
    """Where the load never reverses the two methods agree, which is exactly
    why the defect above survives casual testing."""
    measured = _measure({"off": UNIAXIAL * 0.0, "on": UNIAXIAL}, ("off", "on"))
    assert measured.amplitude_max == pytest.approx(50.0)
    assert measured.mean_at_worst == pytest.approx(50.0)


def test_the_mean_says_which_way_the_material_is_being_worked() -> None:
    """Pulled apart is not the same as pressed together.

    A mean stress that pulls holds a crack open and makes the same swing far
    more damaging; one that presses holds it shut. The two must not report the
    same number, which is what an unsigned von Mises would do.
    """
    pulled = _measure({"off": UNIAXIAL * 0.0, "on": UNIAXIAL}, ("off", "on"))
    pressed = _measure({"off": UNIAXIAL * 0.0, "on": -UNIAXIAL}, ("off", "on"))

    assert pulled.amplitude_max == pytest.approx(pressed.amplitude_max)
    assert pulled.mean_at_worst == pytest.approx(50.0)
    assert pressed.mean_at_worst == pytest.approx(-50.0)
    assert pulled.mean_at_worst != pytest.approx(pressed.mean_at_worst)


def test_the_order_of_the_two_ends_does_not_matter() -> None:
    """An amplitude is a magnitude and a mean is the middle of the pair, so
    naming the ends the other way round changes neither."""
    forward = _measure({"a": UNIAXIAL, "b": -UNIAXIAL * 0.5}, ("a", "b"))
    backward = _measure({"a": UNIAXIAL, "b": -UNIAXIAL * 0.5}, ("b", "a"))
    assert forward.amplitude_max == pytest.approx(backward.amplitude_max)
    assert forward.mean_at_worst == pytest.approx(backward.mean_at_worst)


def test_amplitude_and_mean_follow_superposition() -> None:
    """The closed form, for any linear-elastic model.

    With one end at ``a`` times a reference load and the other at ``b`` times
    it, the swing is ``|a - b| / 2`` of the reference stress and the middle is
    ``(a + b) / 2`` of it. This holds node by node and needs no beam theory.
    """
    for a, b in [(1.0, -1.0), (1.0, 0.0), (1.0, 0.5), (0.25, -0.75), (-0.5, -1.0)]:
        measured = _measure({"a": UNIAXIAL * a, "b": UNIAXIAL * b}, ("a", "b"))
        assert measured.amplitude_max == pytest.approx(abs(a - b) / 2.0 * 100.0)
        assert measured.mean_at_worst == pytest.approx((a + b) / 2.0 * 100.0)


def test_the_two_sign_conventions_are_both_available() -> None:
    """Which one to use is an engineering choice, so both must work.

    On a plain pull they agree. They do not always: measured on the example
    L-bracket they give opposite signs at 137 of 19 787 points.
    """
    for convention in EquivalentStress:
        measured = _measure(
            {"off": UNIAXIAL * 0.0, "on": UNIAXIAL}, ("off", "on"), convention=convention
        )
        assert measured.mean_at_worst == pytest.approx(50.0)


def test_a_missing_load_case_is_refused_rather_than_guessed() -> None:
    with pytest.raises(EvaluationFailure) as caught:
        _measure({"up": UNIAXIAL}, ("up", "nowhere"))
    assert caught.value.code is FailureCode.FATIGUE_CYCLE_INCOMPLETE
    assert "nowhere" in str(caught.value)


def test_a_load_case_with_no_stress_state_is_refused() -> None:
    """A swing cannot be measured from von Mises alone, which is the whole
    point, so a load case that produced no tensor is refused rather than
    quietly measured the wrong way."""
    from openoptima.results.fatigue import cycle_stress

    cycle = LoadCycle(name="cycle", between=("a", "b"))
    good = _fields("a", UNIAXIAL)
    bare = _fields("b", UNIAXIAL)
    object.__setattr__(bare, "stress_tensor", None)

    with pytest.raises(EvaluationFailure) as caught:
        cycle_stress(
            cycle,
            {"a": good, "b": bare},
            FatigueSettings(enabled=True, cycles=(cycle,)),
            StressEvaluation(measure="raw_max"),
        )
    assert caught.value.code is FailureCode.FATIGUE_CYCLE_INCOMPLETE


def test_load_cases_numbered_differently_are_refused_not_subtracted() -> None:
    """Subtracting two fields that were ordered differently would pair up the
    wrong points and give a plausible, wrong answer."""
    from openoptima.results.fatigue import cycle_stress

    cycle = LoadCycle(name="cycle", between=("a", "b"))
    pair = np.vstack([UNIAXIAL, UNIAXIAL * 2.0])
    a = _fields("a", pair, tags=np.array([1, 2]))
    b = _fields("b", -pair, tags=np.array([2, 1]))

    with pytest.raises(EvaluationFailure) as caught:
        cycle_stress(
            cycle,
            {"a": a, "b": b},
            FatigueSettings(enabled=True, cycles=(cycle,)),
            StressEvaluation(measure="raw_max"),
        )
    assert caught.value.code is FailureCode.FATIGUE_CYCLE_INCOMPLETE
    assert "different" in str(caught.value)


def test_an_incomplete_cycle_is_an_error_never_a_bad_design() -> None:
    """The same cycle is declared for every design in a study, so a broken one
    is a setup mistake. Feeding it back as a poor design would teach the
    optimiser something false."""
    assert outcome_for(FailureCode.FATIGUE_CYCLE_INCOMPLETE) is Outcome.ERROR

    from openoptima.domain.failures import is_retryable

    assert not is_retryable(FailureCode.FATIGUE_CYCLE_INCOMPLETE)
