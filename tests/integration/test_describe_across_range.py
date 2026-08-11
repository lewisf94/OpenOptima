"""A description written on one shape must still find the face on another.

That sentence is the entire feature. Everything else about turning a click
into a selector is bookkeeping; this is the part that decides whether the
result can be trusted, and it cannot be checked without rebuilding real
geometry at different dimensions.

The part is the L-bracket, whose design range moves things a long way: both
plate thicknesses run 5 to 20 mm and the internal fillet radius runs 3 to
25 mm. A fillet that can be smaller than the bolt holes at one end of its
range and three times their size at the other is exactly the case that breaks
a naive description.
"""

from __future__ import annotations

import pytest

from openoptima.domain.failures import EvaluationFailure
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.regions.describe import BuildSample, describe_faces
from openoptima.regions.matcher import resolve_region
from openoptima.regions.signature import solid_face_signatures

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh, pytest.mark.gmsh]

FIXED = {
    "length": 120.0,
    "height": 90.0,
    "width": 60.0,
    "bolt_diameter": 9.0,
    "bolt_inset": 15.0,
}
SPACE = DesignSpace(
    (
        DesignVariable(id="thickness_h", minimum=5.0, maximum=20.0, default=10.0),
        DesignVariable(id="thickness_v", minimum=5.0, maximum=20.0, default=10.0),
        DesignVariable(id="fillet_radius", minimum=3.0, maximum=25.0, default=8.0),
    )
)
MINIMUM = {"thickness_h": 5.0, "thickness_v": 5.0, "fillet_radius": 3.0}
DEFAULT = {"thickness_h": 10.0, "thickness_v": 10.0, "fillet_radius": 8.0}
MAXIMUM = {"thickness_h": 20.0, "thickness_v": 20.0, "fillet_radius": 25.0}


def _build(values, directory):
    provider = OccGeometryProvider(
        GeometryDefinition(provider="occ", template="l_bracket", parameters=FIXED)
    )
    artifact = provider.build(SPACE.decode(values), directory)
    with gmsh_session() as gmsh:
        gmsh.model.add("described")
        gmsh.model.occ.importShapes(str(artifact.brep_path))
        gmsh.model.occ.synchronize()
        volume_tag = gmsh.model.getEntities(3)[0][1]
        signatures = solid_face_signatures(gmsh, volume_tag)
    return artifact, signatures


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    root = tmp_path_factory.mktemp("describe")
    built = {}
    for label, values in (
        ("minimum", MINIMUM),
        ("default", DEFAULT),
        ("maximum", MAXIMUM),
    ):
        artifact, signatures = _build(values, root / label)
        built[label] = (artifact, signatures)
    return built


@pytest.fixture(scope="module")
def picks(shapes):
    """The faces a user would click, found on the default shape."""
    _artifact, signatures = shapes["default"]
    mounting = max(
        (s for s in signatures if s.normal and s.normal[0] < -0.99), key=lambda s: s.area
    )
    load = max(
        (s for s in signatures if s.normal and s.normal[0] > 0.99 and s.centroid[0] > 100.0),
        key=lambda s: s.area,
    )
    holes = [
        s for s in signatures if s.surface_type is SurfaceType.CYLINDER and s.centroid[2] > 50.0
    ]
    fillet = [
        s for s in signatures if s.surface_type is SurfaceType.CYLINDER and s.centroid[2] < 50.0
    ]
    assert len(holes) == 2, "expected two bolt holes on the default bracket"
    assert len(fillet) == 1, "expected one internal fillet"
    return {
        "mounting_face": [mounting],
        "load_face": [load],
        "bolt_holes": holes,
        "fillet": fillet,
    }


@pytest.fixture(scope="module")
def described(shapes, picks):
    """Descriptions written from the default shape, checked against both ends."""
    _artifact, signatures = shapes["default"]
    scale = shapes["default"][0].bbox.diagonal
    alternatives = [
        BuildSample(shapes[label][1], shapes[label][0].bbox.diagonal, label)
        for label in ("minimum", "maximum")
    ]
    return {
        name: describe_faces(
            targets,
            signatures,
            scale_length=scale,
            name=name,
            alternatives=alternatives,
        )
        for name, targets in picks.items()
    }


def _resolve(described_region, shapes, label, name):
    artifact, signatures = shapes[label]
    return resolve_region(
        SemanticRegion(name=name, selector=described_region.selector),
        signatures,
        scale_length=artifact.bbox.diagonal,
    )


class TestEveryDescriptionSurvivesTheWholeDesignRange:
    @pytest.mark.parametrize("label", ["minimum", "default", "maximum"])
    @pytest.mark.parametrize("name", ["mounting_face", "load_face", "bolt_holes", "fillet"])
    def test_it_still_resolves(self, described, shapes, label, name):
        match = _resolve(described[name], shapes, label, name)
        assert match.face_tags, f"{name} found nothing at {label}"

    @pytest.mark.parametrize("label", ["minimum", "default", "maximum"])
    @pytest.mark.parametrize(
        ("name", "count"),
        [("mounting_face", 1), ("load_face", 1), ("bolt_holes", 2), ("fillet", 1)],
    )
    def test_it_finds_the_right_number_of_faces(self, described, shapes, label, name, count):
        match = _resolve(described[name], shapes, label, name)
        assert len(match.face_tags) == count

    @pytest.mark.parametrize("label", ["minimum", "default", "maximum"])
    @pytest.mark.parametrize(
        ("name", "surface"),
        [
            ("mounting_face", SurfaceType.PLANE),
            ("load_face", SurfaceType.PLANE),
            ("bolt_holes", SurfaceType.CYLINDER),
            ("fillet", SurfaceType.CYLINDER),
        ],
    )
    def test_it_finds_the_right_kind_of_surface(self, described, shapes, label, name, surface):
        match = _resolve(described[name], shapes, label, name)
        assert {s.surface_type for s in match.signatures} == {surface}

    @pytest.mark.parametrize("label", ["minimum", "default", "maximum"])
    @pytest.mark.parametrize(("name", "normal"), [("mounting_face", -1.0), ("load_face", 1.0)])
    def test_a_flat_face_still_points_the_same_way(self, described, shapes, label, name, normal):
        """The check that catches a load moving to the opposite side."""
        match = _resolve(described[name], shapes, label, name)
        for signature in match.signatures:
            assert signature.normal is not None
            assert signature.normal[0] == pytest.approx(normal, abs=0.01)


class TestTheTwoDefectsThisFeatureWasBuiltAround:
    """Both were found by running the thing, not by reasoning about it, and
    both come from the same cause: a description written from one shape cannot
    know which properties are about to be varied.
    """

    def test_the_fillet_description_holds_at_both_extremes(self, described, shapes):
        """Measured before the fix: a radius range written from the 8 mm
        default matched nothing at either end of the fillet's own 3 to 25 mm
        design range, because the range itself is what the optimiser varies."""
        for label, expected_area in (
            # A 90-degree fillet of radius r across the 60 mm width:
            # area = (pi/2) * r * 60.
            ("minimum", 3.14159265 / 2 * 3.0 * 60.0),
            ("maximum", 3.14159265 / 2 * 25.0 * 60.0),
        ):
            match = _resolve(described["fillet"], shapes, label, "fillet")
            assert len(match.face_tags) == 1
            assert match.total_area == pytest.approx(expected_area, rel=1e-3)

    def test_the_bolt_holes_never_swallow_the_fillet(self, described, shapes):
        """The dangerous one.

        Describing the 4.5 mm bolt holes by radius produced a range that also
        caught the fillet once it shrank to 3 mm, and ``ALL`` mode reports no
        ambiguity -- it simply returned three faces where two were picked. A
        load or a constraint would have been applied to the fillet as though it
        were a bolt hole, with nothing anywhere to say so.
        """
        for label in ("minimum", "default", "maximum"):
            match = _resolve(described["bolt_holes"], shapes, label, "bolt_holes")
            assert len(match.face_tags) == 2, (
                f"at {label} the bolt hole description selected {len(match.face_tags)} faces"
            )

    def test_the_bolt_hole_areas_are_the_holes_and_nothing_else(self, described, shapes):
        """Area is the independent check that the right faces were found.

        Two holes of radius 4.5 through a plate of thickness t have a combined
        area of 2 * (2 pi * 4.5 * t).
        """
        for label, thickness in (("minimum", 5.0), ("default", 10.0), ("maximum", 20.0)):
            match = _resolve(described["bolt_holes"], shapes, label, "bolt_holes")
            expected = 2.0 * (2.0 * 3.14159265 * 4.5 * thickness)
            assert match.total_area == pytest.approx(expected, rel=1e-3)


class TestWhatTheDescriptionsCameOutAs:
    """Recorded because the choices are the interesting part: the same
    algorithm picked a *different* strategy for the two round faces, from
    evidence rather than from anything hardcoded."""

    def test_the_fillet_dropped_the_radius_filter(self, described):
        selector = described["fillet"].selector
        assert selector.min_radius is None and selector.max_radius is None
        assert selector.within_box is not None

    def test_the_bolt_holes_kept_theirs(self, described):
        """Their radius is fixed by the bolt size, so it is a good
        discriminator -- and it is kept, with position added alongside."""
        selector = described["bolt_holes"].selector
        assert selector.min_radius is not None or selector.max_radius is not None
        assert selector.within_box is not None

    def test_the_mounting_face_needed_only_which_way_it_points(self, described):
        assert described["mounting_face"].filters_used == (
            "surface type",
            "direction it faces",
        )

    def test_none_of_them_fell_back_on_scoring(self, described):
        """Hard filters are facts about a face. Scoring is a comparison, and a
        comparison can flip when the shape changes."""
        for name, result in described.items():
            assert result.selector.centroid_near is None, f"{name} used scoring"
            assert result.isolated_by_filters_alone, f"{name} has a finite margin"

    def test_they_were_all_checked_against_both_extremes(self, described):
        for result in described.values():
            assert result.checked_against == 2
            assert not any("one shape" in w for w in result.warnings)


class TestItStillRefusesWhenItShould:
    def test_picking_one_of_two_identical_bolt_holes_is_refused(self, shapes, picks):
        """The two holes are mirror images: same size, same kind of surface.
        Only their position differs, and a box around one of them is a
        genuinely fragile thing to rely on across a design range -- so this
        must either isolate it properly or refuse, never guess.
        """
        _artifact, signatures = shapes["default"]
        scale = shapes["default"][0].bbox.diagonal
        alternatives = [
            BuildSample(shapes[label][1], shapes[label][0].bbox.diagonal, label)
            for label in ("minimum", "maximum")
        ]
        try:
            result = describe_faces(
                [picks["bolt_holes"][0]],
                signatures,
                scale_length=scale,
                name="one_hole",
                alternatives=alternatives,
            )
        except EvaluationFailure:
            return  # refusing is a correct outcome here
        # If it did produce one, it must genuinely work everywhere.
        for label in ("minimum", "default", "maximum"):
            match = _resolve(result, shapes, label, "one_hole")
            assert len(match.face_tags) == 1
