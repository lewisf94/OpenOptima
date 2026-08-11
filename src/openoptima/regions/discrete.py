"""Measuring faces on a surface that has no CAD behind it.

``regions/signature.py`` measures a face by asking OpenCASCADE: what is your
area, where is your centre, what kind of surface are you.  A shape that arrived
as a triangle mesh -- an STL from a topology optimisation, a scan, a mesh
somebody exported -- has no OpenCASCADE model to ask.  Every number here is
therefore measured from the triangles themselves.

The point of doing this is that it lets **the same region selectors** work on
both.  A project says "the load goes on the flat face at the far end"; that
sentence has to mean the same thing whether the part came from the parametric
model or came back from a topology run.  Without this module a topology result
could never be analysed, because nothing could work out where to put the loads.

Three things here were measured rather than assumed, and each would have been
wrong if guessed:

**Gmsh splits one physical face into several patches.**  It cuts the surface
wherever the angle between triangles is sharp, and then cuts further so each
piece is simple enough to describe.  Measured on a real topology result: the
top face of the part came back as **5 separate patches**, and the bottom face
as 5 more.  A selector asking for one face would have found five candidates
that all fit equally well, and stopped with an ambiguity error -- correctly,
but uselessly.  So patches that lie in the same flat plane and touch each other
are put back together first.  57 patches became 46 faces, and the top face came
back as one piece of 571.89 mm2.

**Two flat pieces that do not touch stay separate, and that is deliberate.**
On the same result, the face the part bolts to came back as two pads of 30.99
and 23.03 mm2 with a gap between them: the optimiser removed the material in
the middle.  They are genuinely two faces now, exactly as CAD would report
them, and a selector that wants both must ask for both.

**Fit a round face to the corner points, not to the middle of each triangle.**
The middles sit *inside* the true circle, so they fit a circle that is too
small.  Measured on a 3.000 mm hole: fitting the triangle centres gives 2.967
mm, and fitting the corner points gives 3.0000 mm.  A 1.1 per cent error in a
hole radius is enough to make a selector pick the wrong hole.  This is the same
trap as fitting a cylinder from its own centroid, which is already recorded in
``signature.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..domain.regions import BoundingBox, FaceSignature, SurfaceType
from .signature import _fit_circle_2d, _orthonormal_basis

#: gmsh element type numbers for a 3-node and a 6-node triangle.
_TRIANGLE_TYPES = {2: 3, 9: 6}

#: How far a triangle normal may sit from the patch average before the patch
#: stops counting as flat.  Measured on a real topology result the gap is not
#: close: the flat faces came out at exactly 0.0 degrees and the next-flattest
#: thing on the part was 5.8 degrees.  One degree sits comfortably between, and
#: leaves room for the rounding that writing an STL introduces.
PLANARITY_TOLERANCE_DEG = 1.0

#: How far two flat patches may sit out of the same plane and still be treated
#: as one face, as a fraction of the model's overall size.
COPLANAR_TOLERANCE = 1.0e-5

#: How far a round patch's points may sit off the fitted circle, as a fraction
#: of the radius, before it stops counting as round.  Measured: a real hole fits
#: to 0.0009 and 0.0019 of its radius, and the flattest false candidate on the
#: same part is 0.0039 -- so this threshold is tight on purpose.
ROUNDNESS_TOLERANCE = 0.003

#: How far a round patch's normals may sit from square to its own axis.  A
#: faceted hole is not exact: a real 3 mm hole measured 4.3 and 4.7 degrees.
AXIS_SQUARENESS_TOLERANCE_DEG = 12.0

#: How many separate points a patch needs before its roundness means anything.
#: A circle has three unknowns, so **three points always fit one exactly** and a
#: patch of a single triangle would be called round every time.  Measured: the
#: two pieces of a real topology result that were wrongly called round had five
#: points each.  A real 3 mm hole had forty.
MINIMUM_POINTS_FOR_A_CIRCLE = 12

#: How much of a full turn a patch must cover before its radius is believable.
#: A short arc cannot be told apart from a gentle bend, and its fitted radius
#: swings wildly.  Measured on a real topology result: two smoothed bumps
#: covering 19.9 and 38.3 degrees were fitted as holes of 7.8 and 4.8 mm radius,
#: to within a thousandth of the radius.  Both are meaningless.
MINIMUM_ARC_DEG = 45.0


@dataclass(frozen=True)
class _Patch:
    """One piece of surface as gmsh handed it over, and what it measures."""

    tag: int
    triangles: np.ndarray  # (T, 3) node tags, corners only
    area: float
    centroid: np.ndarray  # (3,)
    normal: np.ndarray  # (3,) area-weighted, unit
    #: Worst angle in degrees between a triangle's normal and the patch average.
    spread_deg: float
    #: Twice the signed area vector, summed. Its dot with the centroid gives the
    #: patch's contribution to the enclosed volume.
    volume_moment: float
    axis: np.ndarray | None = None
    radius: float | None = None
    centre: np.ndarray | None = None

    @property
    def is_planar(self) -> bool:
        return self.spread_deg <= PLANARITY_TOLERANCE_DEG

    @property
    def is_round(self) -> bool:
        return self.axis is not None and self.radius is not None


@dataclass(frozen=True)
class DiscreteSurface:
    """Everything the mesher and the region matcher need about a triangle mesh."""

    signatures: tuple[FaceSignature, ...]
    #: Face tag as the selectors see it -> the gmsh patches it is made of.
    #: A face is usually several patches, so this is how a region turns back
    #: into something gmsh can put a physical group on.
    patches: dict[int, tuple[int, ...]]
    #: Enclosed volume in mm3, from the divergence theorem over every triangle.
    volume: float
    surface_area: float
    bbox: BoundingBox
    #: Gmsh patch tags grouped into closed shells, biggest first.  One shell is
    #: an ordinary solid.  More than one means the part has a sealed bubble
    #: inside it, and the mesher needs to know so it does not fill the bubble in.
    shells: tuple[tuple[int, ...], ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def gmsh_tags(self, face_tags: tuple[int, ...]) -> list[int]:
        """Expand face tags into the gmsh patch tags underneath them."""
        expanded: list[int] = []
        for tag in face_tags:
            expanded.extend(self.patches[int(tag)])
        return expanded


def _node_coordinates(gmsh: Any) -> np.ndarray:
    """All node coordinates, indexed by node tag.

    Indexed rather than mapped because the lookup happens once per triangle
    corner, and a dictionary lookup per corner is measurably slower on a mesh
    with a hundred thousand triangles.
    """
    tags, coordinates, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    if tags.size == 0:
        return np.zeros((1, 3), dtype=np.float64)
    table = np.zeros((int(tags.max()) + 1, 3), dtype=np.float64)
    table[tags] = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)
    return table


def _triangles_of(gmsh: Any, tag: int) -> np.ndarray:
    """Corner nodes of every triangle on one patch.

    Midside nodes are dropped: they describe the same triangle, and the surface
    is treated as flat-sided facets throughout.
    """
    types, _tags, node_blocks = gmsh.model.mesh.getElements(2, tag)
    blocks: list[np.ndarray] = []
    for element_type, nodes in zip(types, node_blocks, strict=True):
        per_element = _TRIANGLE_TYPES.get(int(element_type))
        if per_element is None:
            continue
        block = np.asarray(nodes, dtype=np.int64).reshape(-1, per_element)
        blocks.append(block[:, :3])
    if not blocks:
        return np.zeros((0, 3), dtype=np.int64)
    return np.vstack(blocks)


def _fit_cylinder(
    normals: np.ndarray, points: np.ndarray
) -> tuple[np.ndarray, float, np.ndarray] | None:
    """Fit an axis and a radius to a patch that curves in one direction.

    The axis is the direction every surface normal is square to, which is the
    smallest right-singular vector of the normals.  The radius then comes from
    fitting a circle to the corner points in the plane square to that axis.

    Returns ``None`` unless the fit is actually good, because a flat face fits a
    very large circle rather well and must not be mistaken for a round one.  A
    fit is only believed when there are enough points to make it mean something
    and the patch covers enough of a turn for the radius to be real -- see
    :data:`MINIMUM_POINTS_FOR_A_CIRCLE` and :data:`MINIMUM_ARC_DEG`.
    """
    if len(normals) < 3 or len(points) < MINIMUM_POINTS_FOR_A_CIRCLE:
        return None
    _u, _s, vt = np.linalg.svd(normals)
    axis = vt[-1] / (np.linalg.norm(vt[-1]) + 1e-30)

    squareness = math.degrees(math.asin(min(1.0, float(np.abs(normals @ axis).max()))))
    if squareness > AXIS_SQUARENESS_TOLERANCE_DEG:
        return None

    e1, e2 = _orthonormal_basis(axis)
    projected = np.column_stack([points @ e1, points @ e2])
    fit = _fit_circle_2d(projected)
    if fit is None:
        return None
    cx, cy, radius = fit
    if radius <= 0:
        return None
    residual = np.abs(np.linalg.norm(projected - np.array([cx, cy]), axis=1) - radius)
    if float(residual.max()) / radius > ROUNDNESS_TOLERANCE:
        return None
    if _arc_covered_deg(projected, cx, cy) < MINIMUM_ARC_DEG:
        return None
    centre = cx * e1 + cy * e2
    return axis, radius, centre


def _arc_covered_deg(projected: np.ndarray, cx: float, cy: float) -> float:
    """How much of a full turn the points cover, in degrees.

    Found by sorting the points by angle and taking the largest empty gap: what
    is left is the arc.  A full hole gives 360, half a hole gives 180, and a
    stray bump gives twenty or thirty.
    """
    angles = np.sort(np.degrees(np.arctan2(projected[:, 1] - cy, projected[:, 0] - cx)))
    gaps = np.diff(np.concatenate([angles, [angles[0] + 360.0]]))
    return float(360.0 - gaps.max())


def _measure_patch(tag: int, triangles: np.ndarray, table: np.ndarray) -> _Patch | None:
    """Everything measurable about one patch, straight from its triangles."""
    if len(triangles) == 0:
        return None
    corners = np.stack([table[triangles[:, i]] for i in range(3)], axis=1)
    doubled = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = 0.5 * np.linalg.norm(doubled, axis=1)
    total = float(areas.sum())
    if total <= 0.0:
        return None

    centres = corners.mean(axis=1)
    centroid = (areas[:, None] * centres).sum(axis=0) / total
    average = doubled.sum(axis=0)
    length = float(np.linalg.norm(average))
    if length <= 1e-30:
        # A patch whose normals cancel exactly: a fold, or a degenerate sheet.
        # Nothing here can describe it, so it is left unclassified rather than
        # given a made-up normal.
        return None
    normal = average / length
    units = doubled / (np.linalg.norm(doubled, axis=1)[:, None] + 1e-30)
    spread = float(np.degrees(np.arccos(np.clip(units @ normal, -1.0, 1.0))).max())
    # Divergence theorem: the volume a closed surface encloses is the sum over
    # its triangles of (normal . position) x area, divided by three.
    moment = float((0.5 * doubled * centres).sum())

    axis = radius = centre = None
    if spread > PLANARITY_TOLERANCE_DEG:
        # Only a patch that actually curves is offered to the cylinder fit. A
        # flat face fits an enormous circle to well inside the tolerance -- one
        # measured 0.0039 of its radius, tighter than the real hole beside it --
        # so testing a flat face for roundness at all would misclassify it.
        fitted = _fit_cylinder(units, table[np.unique(triangles)])
        if fitted is not None:
            axis, radius, centre = fitted

    return _Patch(
        tag=int(tag),
        triangles=triangles,
        area=total,
        centroid=centroid,
        normal=normal,
        spread_deg=spread,
        volume_moment=moment,
        axis=axis,
        radius=radius,
        centre=centre,
    )


def _shared_edges(patches: dict[int, _Patch]) -> list[tuple[int, int]]:
    """Pairs of patches that touch along at least one triangle edge."""
    owners: dict[tuple[int, int], set[int]] = {}
    for patch in patches.values():
        for triangle in patch.triangles:
            for first, second in ((0, 1), (1, 2), (2, 0)):
                a, b = int(triangle[first]), int(triangle[second])
                owners.setdefault((a, b) if a < b else (b, a), set()).add(patch.tag)
    pairs: set[tuple[int, int]] = set()
    for shared in owners.values():
        if len(shared) < 2:
            continue
        ordered = sorted(shared)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                pairs.add((first, second))
    return sorted(pairs)


def _shells(
    patches: dict[int, _Patch], pairs: list[tuple[int, int]]
) -> tuple[tuple[int, ...], ...]:
    """Patches grouped into separate closed surfaces, biggest volume first.

    A part with a sealed bubble inside it has two: the outside, and the wall of
    the bubble.  The mesher has to be told about both, or it fills the bubble
    with material that is not there.
    """
    parent = {tag: tag for tag in patches}

    def find(tag: int) -> int:
        while parent[tag] != tag:
            parent[tag] = parent[parent[tag]]
            tag = parent[tag]
        return tag

    for first, second in pairs:
        left, right = find(first), find(second)
        if left != right:
            parent[right] = left

    groups: dict[int, list[int]] = {}
    for tag in patches:
        groups.setdefault(find(tag), []).append(tag)
    ordered = sorted(
        groups.values(),
        key=lambda members: -abs(sum(patches[tag].volume_moment for tag in members)),
    )
    return tuple(tuple(sorted(members)) for members in ordered)


def _same_face(a: _Patch, b: _Patch, *, scale: float) -> bool:
    """Are two touching patches two halves of the same physical face?"""
    if a.is_planar and b.is_planar:
        parallel = math.degrees(math.acos(min(1.0, abs(float(a.normal @ b.normal)))))
        if parallel > PLANARITY_TOLERANCE_DEG:
            return False
        offset = abs(float(a.normal @ (b.centroid - a.centroid)))
        return offset <= COPLANAR_TOLERANCE * scale

    if a.is_round and b.is_round:
        assert a.axis is not None and b.axis is not None
        assert a.radius is not None and b.radius is not None
        assert a.centre is not None and b.centre is not None
        parallel = math.degrees(math.acos(min(1.0, abs(float(a.axis @ b.axis)))))
        if parallel > AXIS_SQUARENESS_TOLERANCE_DEG:
            return False
        if abs(a.radius - b.radius) > ROUNDNESS_TOLERANCE * max(a.radius, b.radius) * 10.0:
            return False
        # Same axis line, not merely the same direction: two holes of equal size
        # drilled parallel to each other are two faces, not one.
        separation = b.centre - a.centre
        sideways = separation - float(separation @ a.axis) * a.axis
        return float(np.linalg.norm(sideways)) <= COPLANAR_TOLERANCE * scale + 0.01 * a.radius

    return False


def _merge(
    patches: dict[int, _Patch], pairs: list[tuple[int, int]], *, scale: float
) -> dict[int, list[int]]:
    """Group patches into physical faces. Returns face tag -> patch tags."""
    parent = {tag: tag for tag in patches}

    def find(tag: int) -> int:
        while parent[tag] != tag:
            parent[tag] = parent[parent[tag]]
            tag = parent[tag]
        return tag

    for first, second in pairs:
        if not _same_face(patches[first], patches[second], scale=scale):
            continue
        left, right = find(first), find(second)
        if left != right:
            parent[right] = left

    groups: dict[int, list[int]] = {}
    for tag in patches:
        groups.setdefault(find(tag), []).append(tag)
    # Name each face after its lowest patch, so the same shape always produces
    # the same face tags however gmsh happened to number the pieces.
    return {min(members): sorted(members) for members in groups.values()}


def _signature_of(face_tag: int, members: list[_Patch], table: np.ndarray) -> FaceSignature:
    """Re-measure a merged face as a whole."""
    area = sum(patch.area for patch in members)
    centroid = sum(patch.area * patch.centroid for patch in members) / area
    triangles = np.vstack([patch.triangles for patch in members])
    points = table[np.unique(triangles)]
    bbox = BoundingBox(
        *(float(v) for v in points.min(axis=0)), *(float(v) for v in points.max(axis=0))
    )

    corners = np.stack([table[triangles[:, i]] for i in range(3)], axis=1)
    doubled = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    units = doubled / (np.linalg.norm(doubled, axis=1)[:, None] + 1e-30)
    average = doubled.sum(axis=0)
    normal_vector = average / (np.linalg.norm(average) + 1e-30)
    spread = float(np.degrees(np.arccos(np.clip(units @ normal_vector, -1.0, 1.0))).max())

    if spread <= PLANARITY_TOLERANCE_DEG:
        return FaceSignature(
            tag=face_tag,
            surface_type=SurfaceType.PLANE,
            area=area,
            centroid=(float(centroid[0]), float(centroid[1]), float(centroid[2])),
            normal=(float(normal_vector[0]), float(normal_vector[1]), float(normal_vector[2])),
            bbox=bbox,
        )

    # Refit across the whole face rather than reusing one piece's numbers: two
    # halves of a hole each see half a circle, and a half-circle fit is exactly
    # the case that made fitting from a centroid wrong in the first place.
    fitted = _fit_cylinder(units, points)
    if fitted is not None:
        axis, radius, _centre = fitted
        return FaceSignature(
            tag=face_tag,
            surface_type=SurfaceType.CYLINDER,
            area=area,
            centroid=(float(centroid[0]), float(centroid[1]), float(centroid[2])),
            normal=None,
            bbox=bbox,
            radius=radius,
            axis=(float(axis[0]), float(axis[1]), float(axis[2])),
        )

    return FaceSignature(
        tag=face_tag,
        surface_type=SurfaceType.OTHER,
        area=area,
        centroid=(float(centroid[0]), float(centroid[1]), float(centroid[2])),
        normal=None,
        bbox=bbox,
    )


def measure_discrete_surface(gmsh: Any) -> DiscreteSurface:
    """Measure every face of a triangle mesh already loaded into ``gmsh``.

    Expects the surface to have been split into patches already, by
    ``gmsh.model.mesh.classifySurfaces``.  Returns faces, not patches: touching
    pieces of the same plane or the same round hole are put back together, so a
    selector written against the original CAD keeps its meaning.

    The enclosed volume comes from the divergence theorem over every triangle,
    which doubles as the check that the surface faces outwards.  A shape whose
    triangles are wound inside out gives a negative volume, and every normal is
    then turned round -- silently trusting the file's own winding would put
    every load on the wrong side of the part.
    """
    table = _node_coordinates(gmsh)
    patches: dict[int, _Patch] = {}
    skipped: list[int] = []
    for _dimension, tag in gmsh.model.getEntities(2):
        measured = _measure_patch(int(tag), _triangles_of(gmsh, int(tag)), table)
        if measured is None:
            skipped.append(int(tag))
            continue
        patches[measured.tag] = measured

    if not patches:
        return DiscreteSurface(
            signatures=(),
            patches={},
            volume=0.0,
            surface_area=0.0,
            bbox=BoundingBox(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            warnings=("the surface has no triangles, so nothing could be measured",),
        )

    every_triangle = np.vstack([patch.triangles for patch in patches.values()])
    points = table[np.unique(every_triangle)]
    low, high = points.min(axis=0), points.max(axis=0)
    bbox = BoundingBox(*(float(v) for v in low), *(float(v) for v in high))

    volume = sum(patch.volume_moment for patch in patches.values()) / 3.0
    warnings: list[str] = []
    if volume < 0.0:
        warnings.append(
            "The surface was wound inside out, so every face pointed into the "
            "part instead of out of it. It has been turned the right way round. "
            "Check the file that produced it."
        )
        patches = {
            tag: _Patch(
                tag=patch.tag,
                triangles=patch.triangles[:, ::-1],
                area=patch.area,
                centroid=patch.centroid,
                normal=-patch.normal,
                spread_deg=patch.spread_deg,
                volume_moment=-patch.volume_moment,
                axis=patch.axis,
                radius=patch.radius,
                centre=patch.centre,
            )
            for tag, patch in patches.items()
        }
        volume = -volume
    if skipped:
        warnings.append(
            f"{len(skipped)} piece(s) of the surface could not be measured and "
            f"were left out: they have no area, or their triangles cancel out. "
            f"Loads cannot be placed on them."
        )

    pairs = _shared_edges(patches)
    groups = _merge(patches, pairs, scale=bbox.diagonal)
    signatures = tuple(
        _signature_of(face_tag, [patches[tag] for tag in members], table)
        for face_tag, members in sorted(groups.items())
    )
    return DiscreteSurface(
        signatures=signatures,
        patches={face: tuple(members) for face, members in sorted(groups.items())},
        volume=volume,
        surface_area=float(sum(patch.area for patch in patches.values())),
        bbox=bbox,
        shells=_shells(patches, pairs),
        warnings=tuple(warnings),
    )
