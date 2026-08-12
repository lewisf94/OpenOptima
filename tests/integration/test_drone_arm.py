"""The drone arm template, built with a real CAD kernel.

Volumes are checked against the closed form, not against numbers recorded from
an earlier run. The arm is a box with a rectangular cavity and a pad on top::

    V = L*W*H  -  cavity_length*(W - 2t)*(H - 2t)  +  pad_L*pad_W*pad_t

A cavity placed at the wrong depth, or a wall applied to one side only, does
not land on that figure by accident.

The test that matters most is
:func:`test_the_motor_pad_is_the_same_face_at_every_design_point`. The arm
section is a design variable, so most of this part moves as the optimiser
works. The motor mount plane is deliberately anchored at ``z = 0`` so that one
region selector stays correct across the whole design range -- see
``regions/AGENTS.md`` on why a selector written from one shape is a selector
nobody has tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openoptima.domain.failures import EvaluationFailure, Outcome
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.geometry.occ.templates import get_template
from openoptima.regions.matcher import resolve_region
from openoptima.regions.signature import outward_normal_check, solid_face_signatures
from openoptima.schema.loader import load_project

pytestmark = pytest.mark.gmsh

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROJECT = EXAMPLES / "drone_arm" / "project.yaml"

#: Fixed by the aircraft, not by the optimiser.
LENGTH = 150.0
ROOT_LENGTH = 18.0
PAD_LENGTH = 32.0
PAD_WIDTH = 32.0
PAD_THICKNESS = 4.0

#: The pad is 32 x 32 mm and stays that size whatever the section does.
PAD_AREA = PAD_WIDTH * PAD_LENGTH


def _expected_volume(width: float, height: float, wall: float) -> float:
    cavity_length = LENGTH - ROOT_LENGTH - PAD_LENGTH
    outer = LENGTH * width * height
    cavity = cavity_length * (width - 2.0 * wall) * (height - 2.0 * wall)
    pad = PAD_LENGTH * PAD_WIDTH * PAD_THICKNESS
    return outer - cavity + pad


def _build(**overrides) -> tuple[float, list]:
    """Build the arm and return its volume and face signatures."""
    template = get_template("drone_arm")
    parameters = {**template.defaults, **overrides}
    with gmsh_session() as gmsh:
        gmsh.model.add("arm")
        tag = template.build(gmsh, parameters)
        volume = float(gmsh.model.occ.getMass(3, tag))
        signatures = solid_face_signatures(gmsh, tag)
    return volume, signatures


#: The default and both ends of the range the example searches.
CORNERS = [
    pytest.param(20.0, 18.0, 3.0, id="default"),
    pytest.param(12.0, 12.0, 2.0, id="smallest"),
    pytest.param(30.0, 30.0, 6.0, id="largest"),
    pytest.param(12.0, 30.0, 2.0, id="narrow-and-deep"),
    pytest.param(30.0, 12.0, 2.0, id="wide-and-shallow"),
]


@pytest.mark.parametrize(("width", "height", "wall"), CORNERS)
def test_the_volume_matches_the_closed_form(width: float, height: float, wall: float) -> None:
    volume, _ = _build(width=width, height=height, wall=wall)
    assert volume == pytest.approx(_expected_volume(width, height, wall), rel=1e-9)


@pytest.mark.parametrize(("width", "height", "wall"), CORNERS)
def test_the_normals_point_out_of_the_solid(width: float, height: float, wall: float) -> None:
    """Verified with the divergence theorem, not by trusting a convention.

    For outward normals the surface integral of ``n . r`` equals ``3V``. A
    single inward-facing normal would put a pressure load the wrong way round.
    """
    volume, signatures = _build(width=width, height=height, wall=wall)
    outward, ratio = outward_normal_check(signatures, volume)
    assert outward
    assert ratio == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize(("width", "height", "wall"), CORNERS)
def test_the_motor_pad_is_the_same_face_at_every_design_point(
    width: float, height: float, wall: float
) -> None:
    """One selector, five very different shapes, the same 1024 mm2 face.

    This is the property the template is built around. If the arm were centred
    on z = 0 instead of hanging below it, the pad would move as ``height``
    changed, and a selector with a box around it would find the arm's own top
    face at some design points and the pad at others -- placing the motor
    thrust on the wrong face with nothing to say so.
    """
    project = load_project(PROJECT)
    region = project.region("motor_pad")
    _, signatures = _build(width=width, height=height, wall=wall)

    match = resolve_region(region, list(signatures), scale_length=LENGTH)
    assert len(match.face_tags) == 1
    assert match.total_area == pytest.approx(PAD_AREA, rel=1e-9)


@pytest.mark.parametrize(("width", "height", "wall"), CORNERS)
def test_the_root_face_resolves_uniquely(width: float, height: float, wall: float) -> None:
    """Three faces on this part point back along -x; the box picks the right one.

    The other two are the back of the motor pad and the end wall of the
    cavity, both at x = 118. Only the bolted face is at x = 0.
    """
    project = load_project(PROJECT)
    region = project.region("root_face")
    _, signatures = _build(width=width, height=height, wall=wall)

    match = resolve_region(region, list(signatures), scale_length=LENGTH)
    assert len(match.face_tags) == 1
    assert match.total_area == pytest.approx(width * height, rel=1e-9)


def test_a_wall_thicker_than_the_arm_is_an_infeasible_design_not_an_error() -> None:
    """A bad design teaches the optimiser something; an error teaches it nothing.

    Asking for a 7 mm wall on both sides of a 12 mm wide arm leaves no arm.
    That is a fact about this design, so the search should learn to avoid that
    corner and carry on -- not retry it at four coarser mesh settings and then
    report that the run broke. See ``domain/failures.py``.
    """
    with pytest.raises(EvaluationFailure) as caught:
        _build(width=12.0, height=12.0, wall=7.0)
    assert caught.value.outcome is Outcome.INFEASIBLE


def test_a_cavity_longer_than_the_arm_is_infeasible() -> None:
    with pytest.raises(EvaluationFailure) as caught:
        _build(length=40.0)
    assert caught.value.outcome is Outcome.INFEASIBLE


def test_the_hollow_arm_is_lighter_than_a_solid_one() -> None:
    """A sanity check on the cut actually happening.

    A cavity that silently failed to cut would leave a solid arm, and every
    stress in the study would be optimistic while the mass was simply wrong.
    """
    hollow, _ = _build(width=20.0, height=18.0, wall=3.0)
    thick_walled, _ = _build(width=20.0, height=18.0, wall=6.0)
    assert hollow < thick_walled
    # 100 mm of 14 x 12 cavity is 16 800 mm3 of PLA not printed.
    assert thick_walled - hollow == pytest.approx(16800.0 - 100.0 * 8.0 * 6.0, rel=1e-9)
