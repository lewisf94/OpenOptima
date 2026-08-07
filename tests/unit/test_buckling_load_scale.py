"""The buckle step is solved against a deliberately reduced load.

CalculiX silently skips the lowest buckling mode when the true factor against
the applied load falls below about 0.52, and returns the second mode instead --
roughly nine times too high, in the unsafe direction, with nothing in its
output to say so.

Scaling the reference load down moves every factor up by exactly the same
constant, because the stress stiffness matrix is linear in the load. Dividing
by that constant afterwards recovers the true factor and loses nothing.

These run without gmsh or a solver. The end-to-end evidence is in
``docs/verification-plan.md`` under V9.
"""

from __future__ import annotations

import pytest

from openoptima.solvers.calculix.dat import BucklingTable
from openoptima.solvers.calculix.deck import BUCKLING_LOAD_SCALE


def test_the_scale_is_large_enough_to_clear_the_threshold():
    """A part folding under a thousandth of its load must still land clear.

    The measured threshold is a true factor of about 0.52. With the load
    divided by this constant, a true factor of 0.001 is solved as 1.0 -- and
    even that extreme is comfortably above the threshold.
    """
    measured_threshold = 0.52
    worst_realistic_factor = 0.001

    assert worst_realistic_factor * BUCKLING_LOAD_SCALE > measured_threshold


def test_rescaling_recovers_the_original_factors():
    table = BucklingTable(factors=(3400.0, 3401.0, 30600.0), step=1)

    recovered = table.rescaled(1.0 / BUCKLING_LOAD_SCALE)

    assert recovered.factors == pytest.approx((3.4, 3.401, 30.6))


def test_rescaling_keeps_the_step_number():
    """Step number selects the static reaction total. Losing it would bring
    back the bug where a buckle step's reaction was summed with the real one."""
    assert BucklingTable(factors=(1.0,), step=3).rescaled(0.5).step == 3


def test_rescaling_preserves_order_and_sign():
    """A negative eigenvalue means the load would have to reverse before
    anything buckles. It must stay negative, not become the smallest positive
    factor."""
    table = BucklingTable(factors=(-2000.0, 3400.0, 30600.0))

    recovered = table.rescaled(1.0 / BUCKLING_LOAD_SCALE)

    assert recovered.factors[0] == pytest.approx(-2.0)
    assert recovered.critical == pytest.approx(3.4)


def test_a_purely_tensile_case_still_reports_no_buckling():
    table = BucklingTable(factors=(-2000.0, -3400.0))

    assert table.rescaled(1.0 / BUCKLING_LOAD_SCALE).critical is None


def test_rescaling_is_exact_for_the_verified_euler_value():
    """V3 measures 14.4086 against Euler's 14.3932.

    With the buckle step solved at a thousandth of the load, CalculiX returns
    14408.6, and the division must give back exactly the verified number.
    """
    table = BucklingTable(factors=(14408.6, 14409.0, 129000.0))

    recovered = table.rescaled(1.0 / BUCKLING_LOAD_SCALE)

    assert recovered.critical == pytest.approx(14.4086, rel=1e-9)


def test_the_close_pair_signal_survives_rescaling():
    """Two nearly equal lowest modes mean a symmetric part that can buckle
    either way. Scaling every factor equally cannot change that."""
    table = BucklingTable(factors=(3400.0, 3401.0, 30600.0))

    assert table.has_close_pair
    assert table.rescaled(1.0 / BUCKLING_LOAD_SCALE).has_close_pair
