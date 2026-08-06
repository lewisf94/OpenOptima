"""Computing geometric fingerprints of faces.

Everything here is derived from the shape itself, so it survives a rebuild.
Face *tags* do not survive, which is the entire reason this module exists.

Two subtleties are handled explicitly, because both were found by testing and
both produce silently wrong answers rather than errors:

**Normal direction.** gmsh's ``getNormal`` already accounts for a face's
orientation flag within the solid, so the value it returns is the outward
normal.  Applying the sign from ``getBoundary(oriented=True)`` on top of that
flips it back inwards.  Rather than trust either convention blindly, we verify
the whole set with the divergence theorem (see :func:`outward_normal_check`).

**Cylinder radius.** Averaging the distance of sampled points from their own
centroid only gives the radius of a *full* cylinder.  On a partial one — a
fillet spanning 90 degrees — the sampled centroid is nowhere near the axis and
the answer is badly wrong.  We fit an actual circle instead.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..domain.regions import BoundingBox, FaceSignature, SurfaceType

_TYPE_MAP = {
    "Plane": SurfaceType.PLANE,
    "Cylinder": SurfaceType.CYLINDER,
    "Sphere": SurfaceType.SPHERE,
    "Cone": SurfaceType.CONE,
    "Torus": SurfaceType.TORUS,
}


def _surface_type(gmsh: Any, tag: int) -> SurfaceType:
    try:
        raw = gmsh.model.getType(2, tag)
    except Exception:  # pragma: no cover - defensive
        return SurfaceType.OTHER
    return _TYPE_MAP.get(raw, SurfaceType.OTHER)


def _parametric_centre(gmsh: Any, tag: int) -> list[float]:
    lower, upper = gmsh.model.getParametrizationBounds(2, tag)
    return [0.5 * (lower[0] + upper[0]), 0.5 * (lower[1] + upper[1])]


def _sample_parameters(gmsh: Any, tag: int, n: int = 5) -> list[float]:
    """A small interior grid of (u, v) pairs, flattened as gmsh expects."""
    lower, upper = gmsh.model.getParametrizationBounds(2, tag)
    us = np.linspace(lower[0], upper[0], n + 2)[1:-1]
    vs = np.linspace(lower[1], upper[1], n + 2)[1:-1]
    out: list[float] = []
    for u in us:
        for v in vs:
            out.extend([float(u), float(v)])
    return out


def _orthonormal_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors spanning the plane perpendicular to ``axis``."""
    seed = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(seed, axis))) > 0.9:
        seed = np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, seed)
    e1 /= np.linalg.norm(e1) + 1e-30
    e2 = np.cross(axis, e1)
    e2 /= np.linalg.norm(e2) + 1e-30
    return e1, e2


def _fit_circle_2d(points: np.ndarray) -> tuple[float, float, float] | None:
    """Algebraic (Kasa) circle fit. Returns (cx, cy, radius).

    Exact for points lying on a circle, including a short arc, which is what a
    fillet face gives us.
    """
    if len(points) < 3:
        return None
    x, y = points[:, 0], points[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones(len(points))])
    b = x**2 + y**2
    try:
        solution, *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:  # pragma: no cover - degenerate sample
        return None
    cx, cy, k = solution
    squared = k + cx * cx + cy * cy
    if squared <= 0:
        return None
    return float(cx), float(cy), float(math.sqrt(squared))


def _cylinder_axis_and_radius(
    gmsh: Any, tag: int
) -> tuple[tuple[float, float, float] | None, float | None]:
    """Fit an axis and radius to a cylindrical face.

    The axis is the direction perpendicular to every surface normal, i.e. the
    smallest right-singular vector of the sampled normals.  The radius then
    comes from a circle fit in the plane perpendicular to that axis.
    """
    try:
        params = _sample_parameters(gmsh, tag, n=5)
        if len(params) < 6:
            return None, None
        normals = np.array(gmsh.model.getNormal(tag, params), dtype=float).reshape(-1, 3)
        points = np.array(gmsh.model.getValue(2, tag, params), dtype=float).reshape(-1, 3)
        if len(normals) < 3:
            return None, None

        _u, _s, vt = np.linalg.svd(normals)
        axis = vt[-1]
        axis = axis / (np.linalg.norm(axis) + 1e-30)

        e1, e2 = _orthonormal_basis(axis)
        projected = np.column_stack([points @ e1, points @ e2])
        fit = _fit_circle_2d(projected)
        if fit is None:
            return (float(axis[0]), float(axis[1]), float(axis[2])), None
        _cx, _cy, radius = fit
        return (float(axis[0]), float(axis[1]), float(axis[2])), radius
    except Exception:  # pragma: no cover - degenerate surfaces
        return None, None


def face_signature(gmsh: Any, tag: int) -> FaceSignature:
    """Measure one face.

    The stored normal is the outward normal of the solid, as returned by gmsh
    (which already accounts for the face's orientation within the shell).
    :func:`solid_face_signatures` verifies that assumption for the whole solid.
    """
    occ = gmsh.model.occ
    area = float(occ.getMass(2, tag))
    centroid = tuple(float(c) for c in occ.getCenterOfMass(2, tag))
    bbox = BoundingBox(*(float(b) for b in occ.getBoundingBox(2, tag)))
    surface_type = _surface_type(gmsh, tag)

    normal: tuple[float, float, float] | None = None
    if surface_type is SurfaceType.PLANE:
        raw = np.array(gmsh.model.getNormal(tag, _parametric_centre(gmsh, tag))[:3], dtype=float)
        length = float(np.linalg.norm(raw))
        if length > 1e-12:
            unit = raw / length
            normal = (float(unit[0]), float(unit[1]), float(unit[2]))

    radius: float | None = None
    axis: tuple[float, float, float] | None = None
    if surface_type is SurfaceType.CYLINDER:
        axis, radius = _cylinder_axis_and_radius(gmsh, tag)

    return FaceSignature(
        tag=int(tag),
        surface_type=surface_type,
        area=area,
        centroid=(centroid[0], centroid[1], centroid[2]),
        normal=normal,
        bbox=bbox,
        radius=radius,
        axis=axis,
    )


def outward_normal_check(signatures: list[FaceSignature], volume: float) -> tuple[bool, float]:
    """Verify the stored normals point out of the solid, via the divergence theorem.

    For a closed surface with outward normals, :math:`\\oint \\mathbf{n}\\cdot
    \\mathbf{r}\\, dA = 3V`.  Faces without a normal (cylinders, splines) are
    skipped, so this is approximate — but it separates ``+3V`` from ``-3V``
    decisively, which is all it needs to do.

    Returns ``(normals_point_outward, ratio)`` where ratio is the computed
    integral divided by 3V.
    """
    if volume <= 0:
        return True, float("nan")
    total = 0.0
    covered = 0.0
    for signature in signatures:
        if signature.normal is None:
            continue
        total += float(np.dot(signature.normal, signature.centroid)) * signature.area
        covered += signature.area
    if covered <= 0:
        return True, float("nan")
    ratio = total / (3.0 * volume)
    return ratio >= 0.0, ratio


def solid_face_signatures(
    gmsh: Any, volume_tag: int, *, verify: bool = True
) -> list[FaceSignature]:
    """Signatures for every face of one solid, with outward normals.

    If the divergence check says the kernel's convention has inverted (which
    would silently move every load to the wrong side of the part), all normals
    are flipped and the fact is recorded on the returned signatures' order —
    callers can re-run :func:`outward_normal_check` to confirm.
    """
    boundary = gmsh.model.getBoundary(
        [(3, volume_tag)], combined=False, oriented=False, recursive=False
    )
    signatures = [face_signature(gmsh, abs(int(tag))) for _dim, tag in boundary]

    if verify:
        volume = float(gmsh.model.occ.getMass(3, volume_tag))
        outward, _ratio = outward_normal_check(signatures, volume)
        if not outward:
            signatures = [
                FaceSignature(
                    tag=s.tag,
                    surface_type=s.surface_type,
                    area=s.area,
                    centroid=s.centroid,
                    normal=((-s.normal[0], -s.normal[1], -s.normal[2]) if s.normal else None),
                    bbox=s.bbox,
                    radius=s.radius,
                    axis=s.axis,
                )
                for s in signatures
            ]
    return signatures


def angle_between(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Angle in degrees between two vectors."""
    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na < 1e-12 or nb < 1e-12:
        return 180.0
    cosine = float(np.clip(float(np.dot(va, vb)) / (na * nb), -1.0, 1.0))
    return math.degrees(math.acos(cosine))
