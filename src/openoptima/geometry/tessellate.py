"""Turning a solid into triangles a browser can draw and a mouse can click.

This is a *display* mesh, not an analysis one. It shares nothing with
``meshing/gmsh_mesher.py``: no element quality gate, no second-order nodes, no
retry ladder, because none of those matter for a picture. What it must get
right is different -- every triangle has to remember which CAD face it came
from, because that face tag is what a click resolves to.

**This never touches a computed number.** The output feeds a WebGL canvas and
a click handler; it is never read by the mesher, the solver, or
``regions/describe.py``, which measures its own signatures independently from
the same solid. If this tessellation is coarse or ugly, the viewer looks
worse. It cannot make an analysis wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..domain.regions import BoundingBox

#: Aim for this many triangles around a full circle on a curved face -- the
#: same idea as ``curvature_elements`` in the real mesher, chosen for the same
#: reason: too few and a bolt hole looks octagonal instead of round.
_CURVATURE_ELEMENTS = 16.0


@dataclass(frozen=True)
class DisplayMesh:
    """A triangle soup for rendering, non-indexed: three full vertices per
    triangle rather than a shared vertex list. Larger on the wire, and simpler
    -- three.js can build a ``BufferGeometry`` straight from these arrays with
    no index buffer, and every triangle can have its own normal without
    needing to decide how to blend it with a neighbour across a sharp edge.

    ``face_tags`` has one entry per *triangle*, not per vertex: three.js
    reports which triangle a click landed on, and this is how that becomes a
    CAD face.
    """

    #: Flat [x0,y0,z0, x1,y1,z1, x2,y2,z2, ...], one triple per vertex.
    positions: list[float]
    #: Flat, same layout as positions. The analytic surface normal at each
    #: vertex, not the flat facet normal -- see :func:`_face_positions`.
    normals: list[float]
    #: One CAD face tag per triangle. len(face_tags) == len(positions) // 9.
    face_tags: list[int]
    bbox: BoundingBox
    triangle_count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "triangle_count", len(self.face_tags))

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": self.positions,
            "normals": self.normals,
            "face_tags": self.face_tags,
            "bbox": list(self.bbox.as_tuple()),
            "triangle_count": self.triangle_count,
        }


def tessellate_solid(gmsh: Any, volume_tag: int, *, scale_length: float) -> DisplayMesh:
    """Mesh every face of one solid for display, tagging each triangle.

    Must be called in the same gmsh session, on the same build, that produced
    the face signatures a caller intends to match triangles against --
    face tags are only stable within one build. See ``regions/AGENTS.md``.
    """
    size = max(scale_length / 60.0, 1e-6)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.1)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", _CURVATURE_ELEMENTS)
    # A display mesh is 2D only -- no volume, no second order, no quality
    # gate. Independent of whatever order/curvature settings an analysis mesh
    # for this same session might use, and cleared afterwards so it cannot
    # leak into one.
    gmsh.model.mesh.generate(2)

    all_positions: list[float] = []
    all_normals: list[float] = []
    all_face_tags: list[int] = []

    boundary = gmsh.model.getBoundary([(3, volume_tag)], combined=False, oriented=False)
    for _dim, signed_tag in boundary:
        tag = abs(int(signed_tag))
        positions, normals, triangle_count = _face_triangles(gmsh, tag)
        all_positions.extend(positions)
        all_normals.extend(normals)
        all_face_tags.extend([tag] * triangle_count)

    gmsh.model.mesh.clear()

    xs, ys, zs = all_positions[0::3], all_positions[1::3], all_positions[2::3]
    bbox = (
        BoundingBox(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
        if all_positions
        else BoundingBox(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    )
    return DisplayMesh(
        positions=all_positions, normals=all_normals, face_tags=all_face_tags, bbox=bbox
    )


def _face_triangles(gmsh: Any, tag: int) -> tuple[list[float], list[float], int]:
    """Triangles for one face, with the analytic surface normal per vertex.

    The flat facet normal would make every curved face look faceted --
    correct for the triangles gmsh actually drew, wrong for what the surface
    underneath them looks like. gmsh can be asked for a mesh node's (u, v)
    directly (``returnParametricCoord``), and ``getNormal`` turns that into the
    true surface normal at that exact point -- the same call
    ``regions/signature.py`` uses, and the same rule applies: it already
    accounts for the face's orientation, so its sign is not touched again here
    (trap 2 in ``AGENTS.md``).
    """
    node_tags, coords, param_coords = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
    coords = np.asarray(coords, dtype=float).reshape(-1, 3)
    by_tag = {int(t): coords[i] for i, t in enumerate(node_tags)}

    normal_by_tag: dict[int, np.ndarray] = {}
    if param_coords is not None and len(param_coords) == 2 * len(node_tags):
        uv = np.asarray(param_coords, dtype=float)
        flat_normals = np.asarray(gmsh.model.getNormal(tag, uv), dtype=float).reshape(-1, 3)
        for i, node in enumerate(node_tags):
            length = float(np.linalg.norm(flat_normals[i]))
            if length > 1e-12:
                normal_by_tag[int(node)] = flat_normals[i] / length

    elem_types, _elem_tags, elem_nodes = gmsh.model.mesh.getElements(2, tag)
    positions: list[float] = []
    normals: list[float] = []
    triangle_count = 0
    for gtype, node_list in zip(elem_types, elem_nodes, strict=True):
        # 2 = 3-node triangle, 9 = 6-node triangle (curvature can add
        # midside nodes even though this mesh is never given an element
        # order to raise -- only the corners are used for display).
        corners_per_element = {2: 3, 9: 3}.get(int(gtype))
        if corners_per_element is None:
            continue
        stride = {2: 3, 9: 6}[int(gtype)]
        flat = np.asarray(node_list, dtype=np.int64).reshape(-1, stride)
        for row in flat:
            corners = row[:corners_per_element]
            for node in corners:
                point = by_tag.get(int(node))
                if point is None:
                    continue
                positions.extend(float(c) for c in point)
                normal = normal_by_tag.get(int(node))
                if normal is not None:
                    normals.extend(float(c) for c in normal)
                else:
                    # Not seen on any real face -- kept as a safeguard rather
                    # than an assumption, since a degenerate parametric point
                    # would otherwise raise instead of just looking flat here.
                    normals.extend((0.0, 0.0, 0.0))
            triangle_count += 1
    return positions, normals, triangle_count
