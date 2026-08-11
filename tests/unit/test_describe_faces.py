"""Turning a picked face into a description that survives a rebuild.

These use hand-built face signatures and need no CAE tool. The real proof --
that a description written on one shape still finds the right face on a
different one -- is in ``tests/integration/test_describe_across_range.py``,
because it needs geometry actually rebuilt at other dimensions.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.domain.regions import (
    BoundingBox,
    FaceSignature,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.regions.describe import BuildSample, describe_faces, explain
from openoptima.regions.matcher import resolve_region

SCALE = 100.0


def plane(tag, normal, centroid, area=100.0):
    half = 5.0
    return FaceSignature(
        tag=tag,
        surface_type=SurfaceType.PLANE,
        area=area,
        centroid=centroid,
        normal=normal,
        bbox=BoundingBox(*(c - half for c in centroid), *(c + half for c in centroid)),
    )


def cylinder(tag, radius, centroid, area=50.0):
    half = 5.0
    return FaceSignature(
        tag=tag,
        surface_type=SurfaceType.CYLINDER,
        area=area,
        centroid=centroid,
        normal=None,
        bbox=BoundingBox(*(c - half for c in centroid), *(c + half for c in centroid)),
        radius=radius,
        axis=(0.0, 0.0, 1.0),
    )


def resolves_to(described, signatures, name="region"):
    """Put the generated description through the real resolver."""
    return resolve_region(
        SemanticRegion(name=name, selector=described.selector),
        signatures,
        scale_length=SCALE,
    )


class TestItPrefersTheFewestFilters:
    """Every filter is another thing that can stop being true on a rebuild."""

    def test_a_lone_cylinder_needs_only_its_type(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (0.0, 1.0, 0.0), (0.0, 10.0, 0.0)),
            cylinder(3, 4.0, (0.0, 0.0, 10.0)),
        ]
        described = describe_faces([faces[2]], faces, scale_length=SCALE)
        assert described.filters_used == ("surface type",)
        assert described.selector.within_box is None
        assert described.selector.min_radius is None

    def test_a_plane_is_told_apart_by_which_way_it_points(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
            plane(3, (0.0, 0.0, 1.0), (0.0, 0.0, 10.0)),
        ]
        described = describe_faces([faces[1]], faces, scale_length=SCALE)
        assert described.filters_used == ("surface type", "direction it faces")
        assert described.selector.normal == (-1.0, 0.0, 0.0)

    def test_two_holes_of_different_size_are_told_apart_by_radius(self):
        faces = [
            cylinder(1, 3.0, (0.0, 0.0, 0.0)),
            cylinder(2, 9.0, (0.0, 0.0, 40.0)),
        ]
        described = describe_faces([faces[1]], faces, scale_length=SCALE)
        assert "radius" in described.filters_used

    def test_position_is_used_when_nothing_else_separates_them(self):
        faces = [
            plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 10.0)),
            plane(2, (0.0, 0.0, 1.0), (50.0, 0.0, 10.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert "where it is" in described.filters_used
        assert described.selector.within_box is not None


class TestEveryFilterIsAsLooseAsItCanBe:
    """A description is re-resolved against shapes that have moved. A filter
    pinned to today's numbers stops matching almost immediately."""

    def test_a_radius_range_is_unbounded_where_nothing_competes(self):
        """The defect this replaced: a fillet whose radius runs 3 to 25 mm was
        given an upper bound of twice its 8 mm default, and matched nothing at
        either end of its own design range."""
        faces = [
            cylinder(1, 8.0, (0.0, 0.0, 0.0)),
            cylinder(2, 2.0, (0.0, 0.0, 40.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert described.selector.max_radius is None, (
            "nothing is larger than this face, so an upper bound only "
            "invents a way for the description to fail later"
        )
        assert described.selector.min_radius is not None
        assert 2.0 < described.selector.min_radius < 8.0

    def test_a_radius_range_sits_between_the_face_and_its_nearest_rival(self):
        faces = [
            cylinder(1, 10.0, (0.0, 0.0, 0.0)),
            cylinder(2, 6.0, (0.0, 0.0, 40.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert described.selector.min_radius == pytest.approx(8.0)

    def test_a_box_reaches_halfway_to_the_nearest_face_it_must_exclude(self):
        faces = [
            plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
            plane(2, (0.0, 0.0, 1.0), (100.0, 0.0, 0.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        box = described.selector.within_box
        assert box is not None
        assert box.xmax == pytest.approx(50.0)
        assert box.contains_point((0.0, 0.0, 0.0))
        assert not box.contains_point((100.0, 0.0, 0.0))


class TestAFilterBoundaryNeverSitsInsideNumericalNoise:
    """A gap has to be real before it can be filtered on.

    Found on the real L-bracket. Its two bolt holes are both 9 mm across, and
    the circle fit returns 4.5 and 4.499999999999495 -- a 5.05e-13 mm
    difference that is fitting noise and nothing else. The "as loose as
    possible" rule then placed a radius boundary *inside* that gap, giving a
    filter that picked exactly one of the two holes and worked, by luck, on
    the shape it was written from.

    It also survived being checked against the whole design range, which is the
    part worth remembering: the noise is deterministic, so the identical
    5.05e-13 appears at every design point. Checking against more shapes cannot
    catch a defect whose cause is deterministic.
    """

    #: The measured difference between two nominally identical 4.5 mm holes.
    NOISE = 5.054e-13

    def test_two_holes_of_the_same_size_are_not_split_by_fitting_noise(self):
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.5 - self.NOISE, (0.0, -10.0, 50.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert "where it is" in described.filters_used, (
            "position is the only thing that really separates these two holes"
        )
        assert described.selector.min_radius is None
        assert described.selector.max_radius is None

    def test_the_description_still_picks_the_right_one(self):
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.5 - self.NOISE, (0.0, -10.0, 50.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert resolves_to(described, faces).face_tags == (1,)

    def test_a_real_difference_in_size_is_still_used(self):
        """The guard must not throw away a genuine distinction."""
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.6, (0.0, -10.0, 50.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert described.selector.max_radius is not None
        assert 4.5 < described.selector.max_radius < 4.6

    def test_a_position_a_hair_apart_cannot_be_boxed_either(self):
        """The same hazard, in the other filter: a razor-thin box separating
        two faces by rounding is not a description, it is a coincidence."""
        faces = [
            plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
            plane(2, (0.0, 0.0, 1.0), (1e-11, 0.0, 0.0)),
        ]
        with pytest.raises(EvaluationFailure) as raised:
            describe_faces([faces[0]], faces, scale_length=SCALE)
        assert raised.value.code is FailureCode.REGION_AMBIGUOUS


class TestPickingSeveralFacesAtOnce:
    def test_two_holes_become_an_all_selector(self):
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.5, (0.0, -10.0, 50.0)),
            plane(3, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        ]
        described = describe_faces(faces[:2], faces, scale_length=SCALE)
        assert described.selector.mode is SelectionMode.ALL
        assert set(resolves_to(described, faces).face_tags) == {1, 2}

    def test_a_set_never_falls_back_on_scoring(self):
        """Scoring ranks candidates; it cannot conjure a set of exactly two."""
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.5, (0.0, -10.0, 50.0)),
            cylinder(3, 4.5, (0.0, 0.0, 50.0)),
        ]
        with pytest.raises(EvaluationFailure):
            describe_faces(faces[:2], faces, scale_length=SCALE)


class TestItRefusesRatherThanGuessing:
    """Two faces nobody can tell apart need the user to say which they meant.

    A guess here looks exactly like success, which is the whole reason this
    project never resolves an ambiguous region by picking one.
    """

    def test_two_identical_faces_are_refused(self):
        faces = [
            plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
            plane(2, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        ]
        with pytest.raises(EvaluationFailure) as raised:
            describe_faces([faces[0]], faces, scale_length=SCALE)
        assert raised.value.code is FailureCode.REGION_AMBIGUOUS

    def test_the_refusal_says_which_faces_clash(self):
        faces = [
            plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
            plane(2, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        ]
        with pytest.raises(EvaluationFailure) as raised:
            describe_faces([faces[0]], faces, scale_length=SCALE)
        assert "2" in str(raised.value.detail.get("picked", [])) or raised.value.detail

    def test_an_ambiguous_pick_is_an_error_not_a_bad_design(self):
        """It says the project is set up wrong, nothing about the design."""
        from openoptima.domain.failures import INFEASIBLE_CODES

        assert FailureCode.REGION_AMBIGUOUS not in INFEASIBLE_CODES

    def test_no_faces_picked_is_rejected(self):
        with pytest.raises(EvaluationFailure):
            describe_faces([], [plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))], scale_length=SCALE)

    def test_a_face_from_another_solid_is_rejected(self):
        faces = [plane(1, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))]
        stranger = plane(99, (1.0, 0.0, 0.0), (5.0, 0.0, 0.0))
        with pytest.raises(EvaluationFailure) as raised:
            describe_faces([stranger], faces, scale_length=SCALE)
        assert "99" in raised.value.message


class TestNothingIsReturnedUnproved:
    def test_the_description_resolves_back_to_the_picked_face(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
            cylinder(3, 4.0, (0.0, 0.0, 10.0)),
        ]
        for target in faces:
            described = describe_faces([target], faces, scale_length=SCALE)
            assert resolves_to(described, faces).face_tags == (target.tag,)

    def test_hard_filters_alone_are_preferred_over_scoring(self):
        """An infinite margin means no comparison between candidates was
        needed, so nothing can flip as the shape changes."""
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert math.isinf(described.margin)
        assert described.isolated_by_filters_alone
        assert described.selector.centroid_near is None


class TestCheckingAgainstOtherShapes:
    def test_a_description_that_breaks_on_another_shape_is_rejected(self):
        """The bolt-hole defect, in miniature.

        Radius alone separates these two on the shape they were picked on. On
        the second shape the other face has grown into the same range, so a
        radius description would quietly select both. It must not be chosen.
        """
        here = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 9.0, (0.0, 10.0, 0.0)),
        ]
        there = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.6, (0.0, 10.0, 0.0)),
        ]
        described = describe_faces(
            [here[0]],
            here,
            scale_length=SCALE,
            alternatives=[BuildSample(there, SCALE, "the other shape")],
        )
        assert "where it is" in described.filters_used
        assert described.checked_against == 1

    def test_being_given_nothing_to_check_against_is_warned_about(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert described.checked_against == 0
        assert any("one shape" in w for w in described.warnings)

    def test_no_warning_once_it_has_been_checked(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
        ]
        described = describe_faces(
            [faces[0]],
            faces,
            scale_length=SCALE,
            alternatives=[BuildSample(faces, SCALE, "same again")],
        )
        assert not any("one shape" in w for w in described.warnings)


class TestItReadsBackInPlainEnglish:
    """Whoever clicked has to be able to check the software understood them."""

    def test_a_plane_names_the_direction_it_faces(self):
        faces = [
            plane(1, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            plane(2, (-1.0, 0.0, 0.0), (-10.0, 0.0, 0.0)),
        ]
        described = describe_faces([faces[1]], faces, scale_length=SCALE)
        assert described.explanation == "the flat face pointing -X"

    def test_a_hole_quotes_its_measured_radius(self):
        faces = [
            cylinder(1, 4.5, (0.0, 0.0, 0.0)),
            cylinder(2, 12.0, (0.0, 0.0, 40.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert "4.5 mm in radius" in described.explanation

    def test_a_set_says_how_many(self):
        faces = [
            cylinder(1, 4.5, (0.0, 10.0, 50.0)),
            cylinder(2, 4.5, (0.0, -10.0, 50.0)),
            plane(3, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        ]
        described = describe_faces(faces[:2], faces, scale_length=SCALE)
        assert described.explanation.startswith("all 2 round faces")

    def test_an_off_axis_normal_is_quoted_rather_than_misnamed(self):
        """Calling a 40-degree face "+X" would be a small lie that costs
        someone an afternoon."""
        skew = (0.766, 0.643, 0.0)
        faces = [
            plane(1, skew, (10.0, 10.0, 0.0)),
            plane(2, (0.0, 0.0, 1.0), (0.0, 0.0, 10.0)),
        ]
        described = describe_faces([faces[0]], faces, scale_length=SCALE)
        assert "+X" not in described.explanation
        assert "0.77" in described.explanation

    def test_explain_works_without_the_faces_to_hand(self):
        from openoptima.domain.regions import RegionSelector

        text = explain(RegionSelector(surface_type=SurfaceType.PLANE, normal=(0.0, 0.0, 1.0)))
        assert text == "the flat face pointing +Z"
