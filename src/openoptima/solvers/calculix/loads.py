"""Converting region loads into something CalculiX understands correctly.

Two mechanisms, chosen deliberately:

**Pressure** becomes ``*DLOAD`` with an element face id.  CalculiX then does the
surface integration itself, in the element's own curved geometry and with the
correct outward direction.  This requires knowing which face of which tet each
surface triangle is, which :func:`build_face_lookup` provides.

**A directional force** cannot be expressed as a CalculiX pressure, so it
becomes ``*CLOAD`` with a *consistent* nodal load vector — the surface integral
of each shape function, computed here.

Consistency matters more than it looks.  Spreading a force equally over the
nodes of a quadratic surface is wrong: the exact integral of a corner shape
function over a flat 6-node triangle is **zero**, and all the load belongs on
the midside nodes.  Lumping it evenly puts spurious load at the corners and
pollutes exactly the region where peak stress is read.
"""

from __future__ import annotations

import numpy as np

#: Abaqus/CalculiX face numbering for C3D4 and C3D10, as 0-based corner indices.
_TET_FACES = {
    1: (0, 1, 2),
    2: (0, 3, 1),
    3: (1, 3, 2),
    4: (2, 3, 0),
}

#: Degree-2 Gauss rule on a triangle: exact for the quadratic shape functions
#: of a 6-node triangle, and therefore for the 3-node one too.
_GAUSS_POINTS = ((2 / 3, 1 / 6, 1 / 6), (1 / 6, 2 / 3, 1 / 6), (1 / 6, 1 / 6, 2 / 3))
_GAUSS_WEIGHT = 1.0 / 3.0


def _shape_functions(l1: float, l2: float, l3: float, nodes: int) -> np.ndarray:
    if nodes == 3:
        return np.array([l1, l2, l3])
    if nodes == 6:
        return np.array(
            [
                l1 * (2 * l1 - 1),
                l2 * (2 * l2 - 1),
                l3 * (2 * l3 - 1),
                4 * l1 * l2,
                4 * l2 * l3,
                4 * l3 * l1,
            ]
        )
    raise ValueError(f"unsupported surface element with {nodes} nodes")


def build_face_lookup(
    element_tags: np.ndarray, connectivity: np.ndarray
) -> dict[tuple[int, ...], tuple[int, int]]:
    """Map a sorted corner-node triple to ``(element_tag, face_number)``.

    Only the four corner nodes of each tet participate, so this works
    identically for C3D4 and C3D10.
    """
    lookup: dict[tuple[int, ...], tuple[int, int]] = {}
    corners = connectivity[:, :4]
    for element_tag, tet in zip(element_tags, corners, strict=True):
        for face_number, indices in _TET_FACES.items():
            key = tuple(sorted(int(tet[i]) for i in indices))
            # A face shared by two tets is interior and will be overwritten;
            # boundary faces appear exactly once, which is all we look up.
            lookup[key] = (int(element_tag), face_number)
    return lookup


def surface_element_faces(
    triangles: np.ndarray,
    lookup: dict[tuple[int, ...], tuple[int, int]],
) -> list[tuple[int, int]]:
    """Resolve each surface triangle to the volume element face it sits on."""
    faces: list[tuple[int, int]] = []
    missing = 0
    for triangle in triangles:
        key = tuple(sorted(int(n) for n in triangle[:3]))
        entry = lookup.get(key)
        if entry is None:
            missing += 1
            continue
        faces.append(entry)
    if missing:
        raise ValueError(
            f"{missing} surface triangles could not be matched to a volume element face"
        )
    return faces


def consistent_nodal_forces(
    triangles: np.ndarray,
    coordinates_by_tag: dict[int, np.ndarray],
    total_force: tuple[float, float, float],
) -> dict[int, np.ndarray]:
    """Distribute a total force over a surface as a consistent nodal load vector.

    Returns ``{node_tag: force_vector}``.  The sum of the returned vectors
    equals ``total_force`` to machine precision, which
    ``tests/unit/test_loads.py`` asserts and the cantilever benchmark confirms
    against CalculiX's own reaction forces.
    """
    nodes_per_element = triangles.shape[1]
    weights: dict[int, float] = {}
    total_area = 0.0

    for triangle in triangles:
        points = np.array([coordinates_by_tag[int(n)] for n in triangle[:3]])
        # Straight-sided area is the correct Jacobian for a flat facet and a
        # very close approximation for a mildly curved quadratic one.
        area = 0.5 * float(np.linalg.norm(np.cross(points[1] - points[0], points[2] - points[0])))
        if area <= 0:
            continue
        total_area += area
        for l1, l2, l3 in _GAUSS_POINTS:
            shape = _shape_functions(l1, l2, l3, nodes_per_element)
            for local_index, node_tag in enumerate(triangle):
                weights[int(node_tag)] = (
                    weights.get(int(node_tag), 0.0)
                    + float(shape[local_index]) * area * _GAUSS_WEIGHT
                )

    if total_area <= 0:
        raise ValueError("load surface has zero area")

    force = np.array(total_force, dtype=float)
    return {tag: force * (weight / total_area) for tag, weight in weights.items()}


def surface_area(triangles: np.ndarray, coordinates_by_tag: dict[int, np.ndarray]) -> float:
    total = 0.0
    for triangle in triangles:
        points = np.array([coordinates_by_tag[int(n)] for n in triangle[:3]])
        total += 0.5 * float(np.linalg.norm(np.cross(points[1] - points[0], points[2] - points[0])))
    return total
