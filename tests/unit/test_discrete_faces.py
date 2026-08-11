"""Working out the faces of a shape made only of triangles.

These tests use a stand-in for gmsh rather than the real thing, because the
part being tested is not gmsh: it is the arithmetic that turns a bag of
triangles into named faces a load can be put on.  The real thing is exercised
in ``tests/integration`` and ``tests/verification``.

The stand-in is three methods wide, which is all this module asks gmsh for.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openoptima.domain.regions import SurfaceType
from openoptima.regions.discrete import (
    MINIMUM_ARC_DEG,
    MINIMUM_POINTS_FOR_A_CIRCLE,
    _arc_covered_deg,
    measure_discrete_surface,
)


class FakeGmsh:
    """Just enough gmsh to hand over a set of triangle patches."""

    def __init__(self, points: np.ndarray, patches: dict[int, np.ndarray]) -> None:
        self._points = np.asarray(points, dtype=np.float64)
        self._patches = {int(tag): np.asarray(t, dtype=np.int64) for tag, t in patches.items()}
        self.model = self._Model(self)

    class _Model:
        def __init__(self, outer: FakeGmsh) -> None:
            self._outer = outer
            self.mesh = FakeGmsh._Mesh(outer)

        def getEntities(self, dimension: int):  # gmsh spells it this way
            assert dimension == 2
            return [(2, tag) for tag in sorted(self._outer._patches)]

    class _Mesh:
        def __init__(self, outer: FakeGmsh) -> None:
            self._outer = outer

        def getNodes(self):  # gmsh spells it this way
            count = len(self._outer._points)
            # Node tags are 1-based, exactly as gmsh numbers them.
            return (
                np.arange(1, count + 1, dtype=np.int64),
                self._outer._points.reshape(-1),
                None,
            )

        def getElements(self, dimension: int, tag: int):  # gmsh spells it this way
            assert dimension == 2
            triangles = self._outer._patches[int(tag)] + 1  # to 1-based node tags
            return ([2], [np.arange(len(triangles))], [triangles.reshape(-1)])


def cube(size: float = 10.0) -> tuple[np.ndarray, list[list[int]]]:
    """A closed cube, wound so its faces point outwards."""
    s = size
    points = np.array(
        [
            [0, 0, 0],
            [s, 0, 0],
            [s, s, 0],
            [0, s, 0],
            [0, 0, s],
            [s, 0, s],
            [s, s, s],
            [0, s, s],
        ],
        dtype=np.float64,
    )
    quads = [
        (0, 3, 2, 1),  # z = 0
        (4, 5, 6, 7),  # z = s
        (0, 1, 5, 4),  # y = 0
        (1, 2, 6, 5),  # x = s
        (2, 3, 7, 6),  # y = s
        (3, 0, 4, 7),  # x = 0
    ]
    triangles = []
    for a, b, c, d in quads:
        triangles.append([a, b, c])
        triangles.append([a, c, d])
    return points, triangles


def split_cube(points, triangles, *, one_face_per_patch: bool) -> dict[int, np.ndarray]:
    """Hand the cube over either as six patches, or as twelve half-faces."""
    if one_face_per_patch:
        return {i + 1: np.array(triangles[2 * i : 2 * i + 2]) for i in range(6)}
    return {i + 1: np.array([triangles[i]]) for i in range(12)}


class TestMeasuringWholeFaces:
    def test_a_cube_has_six_faces_and_the_right_volume(self):
        points, triangles = cube(10.0)
        surface = measure_discrete_surface(
            FakeGmsh(points, split_cube(points, triangles, one_face_per_patch=True))
        )

        assert len(surface.signatures) == 6
        assert surface.volume == pytest.approx(1000.0)
        assert surface.surface_area == pytest.approx(600.0)
        assert all(s.surface_type is SurfaceType.PLANE for s in surface.signatures)

    def test_a_face_handed_over_in_pieces_is_put_back_together(self):
        """The reason this module exists.

        gmsh cuts one flat face into several patches. Measured on a real
        topology result, the top face of the part arrived as five. A selector
        asking for one face would then find five equally good candidates and
        stop, correctly but uselessly.
        """
        points, triangles = cube(10.0)
        pieces = split_cube(points, triangles, one_face_per_patch=False)
        assert len(pieces) == 12

        surface = measure_discrete_surface(FakeGmsh(points, pieces))

        assert len(surface.signatures) == 6
        assert sorted(len(p) for p in surface.patches.values()) == [2] * 6
        for signature in surface.signatures:
            assert signature.area == pytest.approx(100.0)

    def test_every_face_points_out_of_the_solid(self):
        points, triangles = cube(10.0)
        surface = measure_discrete_surface(
            FakeGmsh(points, split_cube(points, triangles, one_face_per_patch=True))
        )
        # A normal pointing outwards has a positive dot product with the vector
        # from the centre of the cube to the centre of the face.
        for signature in surface.signatures:
            assert signature.normal is not None
            outward = np.array(signature.centroid) - 5.0
            assert float(np.dot(signature.normal, outward)) > 0

    def test_a_shape_wound_inside_out_is_turned_round_and_says_so(self):
        """Trusting the file's own winding would put every load on the wrong side."""
        points, triangles = cube(10.0)
        flipped = {
            tag: value[:, ::-1]
            for tag, value in split_cube(points, triangles, one_face_per_patch=True).items()
        }
        surface = measure_discrete_surface(FakeGmsh(points, flipped))

        assert surface.volume == pytest.approx(1000.0)
        assert any("inside out" in w for w in surface.warnings)
        for signature in surface.signatures:
            assert signature.normal is not None
            outward = np.array(signature.centroid) - 5.0
            assert float(np.dot(signature.normal, outward)) > 0

    def test_two_flat_pieces_that_do_not_touch_stay_apart(self):
        """Measured on a real topology result, and it is the right answer.

        The face the part bolted to came back as two separate pads, because the
        optimiser removed the material between them. They are two faces now,
        exactly as CAD would report them.
        """
        points, triangles = cube(10.0)
        patches = split_cube(points, triangles, one_face_per_patch=True)
        # A second cube, well away from the first, sharing no point with it.
        offset = points + np.array([100.0, 0.0, 0.0])
        both = np.vstack([points, offset])
        for tag, triangle_block in list(patches.items()):
            patches[tag + 100] = triangle_block + len(points)

        surface = measure_discrete_surface(FakeGmsh(both, patches))

        assert len(surface.signatures) == 12
        assert len(surface.shells) == 2
        assert surface.volume == pytest.approx(2000.0)


class TestRoundFaces:
    """A hole has to survive, or a selector for a bolt hole stops working.

    The patches here are shaped the way gmsh really hands a hole over.  gmsh
    first joins facets whose fold is gentler than its crease angle, so a hole
    with many small facets arrives as a couple of curved pieces -- measured on a
    real 3 mm hole, two pieces of about half a turn each.  Each piece is then
    too short a turn to trust on its own, which is exactly why the pieces are
    put back together before the radius is fitted.
    """

    @staticmethod
    def _tube(radius: float, sides: int, arc_deg: float = 360.0, patches: int = 2):
        """A faceted tube handed over as ``patches`` curved pieces."""
        span = math.radians(arc_deg)
        closed = arc_deg >= 359.999
        count = sides if closed else sides + 1
        angles = [span * i / sides for i in range(count)]
        points = []
        for angle in angles:
            points.append([radius * math.cos(angle), radius * math.sin(angle), 0.0])
            points.append([radius * math.cos(angle), radius * math.sin(angle), 8.0])
        points = np.array(points, dtype=np.float64)

        blocks: dict[int, list[list[int]]] = {tag: [] for tag in range(1, patches + 1)}
        for i in range(sides):
            a, b = 2 * i, 2 * ((i + 1) % count)
            tag = 1 + (i * patches) // sides
            blocks[tag].extend([[a, b, b + 1], [a, b + 1, a + 1]])
        return points, {tag: np.array(block) for tag, block in blocks.items() if block}

    def test_a_faceted_hole_is_measured_as_one_round_face(self):
        points, patches = self._tube(radius=3.0, sides=24)
        surface = measure_discrete_surface(FakeGmsh(points, patches))

        round_faces = [s for s in surface.signatures if s.surface_type is SurfaceType.CYLINDER]
        assert len(round_faces) == 1
        assert round_faces[0].radius == pytest.approx(3.0, rel=1e-9)
        assert abs(round_faces[0].axis[2]) == pytest.approx(1.0, abs=1e-9)
        assert len(surface.patches[round_faces[0].tag]) == 2

    def test_a_short_arc_is_not_called_round(self):
        """A stray bump must never be offered to a selector as a hole.

        Measured on a real topology result: two smoothed bumps covering 19.9 and
        38.3 degrees were fitted as holes of 7.8 and 4.8 mm radius, to within a
        thousandth of the radius. Both numbers are meaningless.
        """
        points, patches = self._tube(radius=3.0, sides=12, arc_deg=30.0, patches=1)
        surface = measure_discrete_surface(FakeGmsh(points, patches))

        assert not any(s.surface_type is SurfaceType.CYLINDER for s in surface.signatures)

    def test_too_few_points_is_not_called_round(self):
        """Three points always fit a circle exactly, so a fit needs more than three."""
        points, patches = self._tube(radius=3.0, sides=4, patches=1)
        distinct = len(np.unique(np.vstack(list(patches.values()))))
        assert distinct < MINIMUM_POINTS_FOR_A_CIRCLE

        surface = measure_discrete_surface(FakeGmsh(points, patches))
        assert not any(s.surface_type is SurfaceType.CYLINDER for s in surface.signatures)

    def test_a_coarsely_faceted_hole_is_reported_as_the_flats_it_actually_is(self):
        """Not a shortcoming: an eight-sided hole is eight flat faces.

        When each facet folds more sharply than the crease angle, gmsh keeps
        them apart and each one really is flat. Calling that a cylinder would be
        inventing a curve the shape does not have.
        """
        points, patches = self._tube(radius=3.0, sides=8, patches=8)
        surface = measure_discrete_surface(FakeGmsh(points, patches))

        assert len(surface.signatures) == 8
        assert all(s.surface_type is SurfaceType.PLANE for s in surface.signatures)


class TestArcMeasurement:
    def test_a_full_circle_covers_the_whole_turn(self):
        angles = np.linspace(0, 2 * math.pi, 36, endpoint=False)
        points = np.column_stack([np.cos(angles), np.sin(angles)])
        assert _arc_covered_deg(points, 0.0, 0.0) == pytest.approx(350.0, abs=1.0)

    def test_a_quarter_circle_covers_a_quarter(self):
        angles = np.linspace(0, math.pi / 2, 30)
        points = np.column_stack([np.cos(angles), np.sin(angles)])
        assert _arc_covered_deg(points, 0.0, 0.0) == pytest.approx(90.0, abs=1.0)

    def test_the_threshold_sits_between_a_fillet_and_a_bump(self):
        """A 90-degree blend is a real round face; a 30-degree bump is not."""
        assert 30.0 < MINIMUM_ARC_DEG < 90.0
