from __future__ import annotations

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.domain.variables import (
    ActivationRule,
    DesignSpace,
    DesignVariable,
    VariableType,
)


def space() -> DesignSpace:
    return DesignSpace(
        (
            DesignVariable(id="t", minimum=1.0, maximum=10.0, default=5.0),
            DesignVariable(id="ribs", type=VariableType.INTEGER, minimum=1, maximum=6, default=3),
            DesignVariable(
                id="profile",
                type=VariableType.CATEGORICAL,
                choices=("round", "square"),
                default="round",
            ),
        )
    )


def test_continuous_clamping_and_rounding():
    variable = DesignVariable(id="t", minimum=2.0, maximum=8.0)
    assert variable.clamp(0.0) == 2.0
    assert variable.clamp(99.0) == 8.0
    assert variable.clamp(5.5) == 5.5


def test_step_snaps_to_grid():
    variable = DesignVariable(id="t", minimum=2.0, maximum=8.0, step=0.5)
    assert variable.clamp(5.3) == pytest.approx(5.5)
    assert variable.clamp(5.1) == pytest.approx(5.0)


def test_integer_variable_rounds():
    variable = DesignVariable(id="n", type=VariableType.INTEGER, minimum=1, maximum=5)
    assert variable.clamp(3.4) == 3
    assert variable.clamp(3.6) == 4
    assert isinstance(variable.clamp(3.6), int)


def test_categorical_maps_from_index():
    variable = DesignVariable(id="p", type=VariableType.CATEGORICAL, choices=("a", "b", "c"))
    assert variable.clamp(0) == "a"
    assert variable.clamp(2.4) == "c"
    assert variable.clamp("b") == "b"


def test_out_of_range_value_is_an_infeasible_design_not_a_crash():
    variable = DesignVariable(id="t", minimum=1.0, maximum=2.0)
    with pytest.raises(EvaluationFailure) as info:
        variable.validate(5.0)
    assert info.value.code is FailureCode.INVALID_DESIGN_VARIABLES
    assert info.value.outcome.value == "infeasible"


def test_nan_is_rejected():
    variable = DesignVariable(id="t", minimum=1.0, maximum=2.0)
    with pytest.raises(EvaluationFailure):
        variable.validate(float("nan"))


def test_round_trip_through_array():
    original = space().decode({"t": 4.25, "ribs": 4, "profile": "square"})
    restored = space().from_array(original.to_array())
    assert restored.as_dict() == original.as_dict()


def test_digest_is_stable_and_order_independent():
    first = space().decode({"t": 4.25, "ribs": 4, "profile": "square"})
    second = space().decode({"profile": "square", "ribs": 4, "t": 4.25})
    assert first.digest() == second.digest()


def test_digest_changes_with_the_design():
    first = space().decode({"t": 4.25, "ribs": 4, "profile": "square"})
    second = space().decode({"t": 4.26, "ribs": 4, "profile": "square"})
    assert first.digest() != second.digest()


def test_inactive_conditional_variable_is_pinned_so_the_cache_still_hits():
    conditional = DesignSpace(
        (
            DesignVariable(id="rib_on", type=VariableType.BOOLEAN, default=False),
            DesignVariable(
                id="rib_thickness",
                minimum=1.0,
                maximum=9.0,
                default=3.0,
                active_when=ActivationRule(variable="rib_on", equals=True),
            ),
        )
    )
    off_a = conditional.decode({"rib_on": False, "rib_thickness": 2.0})
    off_b = conditional.decode({"rib_on": False, "rib_thickness": 8.0})
    assert off_a.digest() == off_b.digest(), (
        "designs differing only in an inactive variable are the same analysis "
        "and must share a cache entry"
    )

    on_a = conditional.decode({"rib_on": True, "rib_thickness": 2.0})
    on_b = conditional.decode({"rib_on": True, "rib_thickness": 8.0})
    assert on_a.digest() != on_b.digest()


def test_duplicate_variable_ids_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        DesignSpace(
            (
                DesignVariable(id="t", minimum=1.0, maximum=2.0),
                DesignVariable(id="t", minimum=1.0, maximum=2.0),
            )
        )


def test_conditional_on_unknown_variable_is_rejected():
    with pytest.raises(ValueError, match="unknown variable"):
        DesignSpace(
            (
                DesignVariable(
                    id="t",
                    minimum=1.0,
                    maximum=2.0,
                    active_when=ActivationRule(variable="missing", equals=True),
                ),
            )
        )


def test_missing_bounds_are_rejected():
    with pytest.raises(ValueError, match="minimum and maximum"):
        DesignVariable(id="t")
