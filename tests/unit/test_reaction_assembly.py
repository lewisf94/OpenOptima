"""Reactions must be summed one direction at a time.

CalculiX reports a full ``(fx, fy, fz)`` total for every node set asked for,
including the directions that set leaves free. Those free-direction figures are
not reactions. Adding them in corrupts the total, and the equilibrium check
then reports an error on a model that is correct.

These run without gmsh or a solver. The end-to-end behaviour is covered by
``tests/verification/test_thick_cylinder.py``.
"""

from __future__ import annotations

import pytest

from openoptima.domain.model import BoundaryCondition, Load, LoadCase, LoadKind
from openoptima.solvers.calculix.dat import ReactionTotal
from openoptima.solvers.calculix.solver import CalculiXSolver

#: The numbers CalculiX actually reported for the quarter-cylinder benchmark.
#: Each symmetry set restrains one direction and reports a spurious figure in
#: the direction it leaves free.
_QUARTER_CYLINDER = [
    ReactionTotal("R_SYMMETRY_X", 1.0, (-100001.3, 1560.4, 0.04), step=1),
    ReactionTotal("R_SYMMETRY_Y", 1.0, (1733.7, -100000.8, -0.19), step=1),
    ReactionTotal("R_BOTTOM_FACE", 1.0, (-61.5, 75.1, -64795.3), step=1),
    ReactionTotal("R_TOP_FACE", 1.0, (55.7, -7.3, 64795.3), step=1),
]

_SYMMETRY_CASE = LoadCase(
    id="internal_pressure",
    boundary_conditions=(
        BoundaryCondition(region="symmetry_x", dofs=(1,)),
        BoundaryCondition(region="symmetry_y", dofs=(2,)),
        BoundaryCondition(region="bottom_face", dofs=(3,)),
        BoundaryCondition(region="top_face", dofs=(3,)),
    ),
    loads=(Load(kind=LoadKind.PRESSURE, region="bore_surface", magnitude=50.0),),
)


def _reaction(totals, load_case, step: int = 1):
    return CalculiXSolver._reaction_for_step(totals, step, load_case)


def test_each_direction_comes_only_from_the_sets_that_restrain_it():
    """Regression test, with the real numbers from the V5 benchmark.

    Adding every component of every set gave (-98273.4, -98372.6, -0.15)
    against an exact (-100000, -100000, 0) -- 1.7% short. The equilibrium
    check then reported a 1.7% error and told the user not to proceed, on an
    analysis correct to one part in 100 000.
    """
    reaction = _reaction(_QUARTER_CYLINDER, _SYMMETRY_CASE)

    assert reaction[0] == pytest.approx(-100001.3)
    assert reaction[1] == pytest.approx(-100000.8)
    assert reaction[2] == pytest.approx(0.0, abs=1e-9)


def test_the_naive_total_really_is_wrong_by_the_amount_claimed():
    """Pins the size of the defect, so the comment describing it stays true."""
    naive = [sum(total.force[axis] for total in _QUARTER_CYLINDER) for axis in range(3)]

    assert naive[0] == pytest.approx(-98273.4, abs=0.1)
    assert naive[1] == pytest.approx(-98372.6, abs=0.1)
    assert abs(naive[0] + 100000.0) / 100000.0 > 0.015, "the error was about 1.7%"


def test_opposing_restraints_in_the_same_direction_cancel():
    """Both ends restrain the axis, and internal pressure has no axial
    component, so their equal and opposite reactions must sum to zero."""
    reaction = _reaction(_QUARTER_CYLINDER, _SYMMETRY_CASE)
    assert reaction[2] == pytest.approx(0.0, abs=1e-9)


def test_a_fully_fixed_face_is_unaffected():
    """The cantilever case: one region restraining all three directions.

    Per-direction assembly must give exactly what summing everything gave, or
    this change would have moved a verified benchmark.
    """
    totals = [ReactionTotal("R_FIXED_FACE", 1.0, (0.0, 0.0, 1000.0), step=1)]
    case = LoadCase(
        id="tip_load",
        boundary_conditions=(BoundaryCondition(region="fixed_face"),),
        loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -1000.0)),),
    )

    assert _reaction(totals, case) == pytest.approx((0.0, 0.0, 1000.0))


def test_two_conditions_on_one_region_contribute_both_directions():
    """A region restrained in x by one condition and in y by another restrains
    both, and its total must count both."""
    totals = [ReactionTotal("R_CORNER", 1.0, (10.0, 20.0, 30.0), step=1)]
    case = LoadCase(
        id="case",
        boundary_conditions=(
            BoundaryCondition(region="corner", dofs=(1,)),
            BoundaryCondition(region="corner", dofs=(2,)),
        ),
        loads=(Load(kind=LoadKind.FORCE, region="load", vector=(1.0, 0.0, 0.0)),),
    )

    assert _reaction(totals, case) == pytest.approx((10.0, 20.0, 0.0))


def test_an_unrecognised_set_keeps_every_component():
    """An unknown set is included whole, which is what this did before.

    Dropping it silently could hide a real missing reaction. Including it may
    produce a wrong total, which raises a warning a human then looks at. For an
    unknown, the noisy option is the safe one.
    """
    totals = [ReactionTotal("R_MYSTERY", 1.0, (5.0, 6.0, 7.0), step=1)]

    assert _reaction(totals, _SYMMETRY_CASE) == pytest.approx((5.0, 6.0, 7.0))


def test_no_load_case_falls_back_to_summing_everything():
    """Callers that cannot supply a load case get the old behaviour."""
    assert CalculiXSolver._reaction_for_step(_QUARTER_CYLINDER, 1, None) == pytest.approx(
        (-98273.4, -98372.6, -0.15), abs=0.1
    )


def test_totals_from_another_step_are_ignored():
    """A *BUCKLE step also emits a reaction total, which is an artefact of the
    eigenvalue solve and not a real reaction."""
    totals = [
        *_QUARTER_CYLINDER,
        ReactionTotal("R_SYMMETRY_X", 1.0, (999999.0, 0.0, 0.0), step=2),
    ]

    reaction = _reaction(totals, _SYMMETRY_CASE, step=1)

    assert reaction[0] == pytest.approx(-100001.3)


def test_no_totals_at_all_is_zero_not_an_error():
    assert _reaction([], _SYMMETRY_CASE) == (0.0, 0.0, 0.0)
