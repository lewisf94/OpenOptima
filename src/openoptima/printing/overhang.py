"""Measuring overhang and build-volume fit from a triangulated shape.

Two things here decide whether the numbers mean anything.

**The surface that rests on the bed is not an overhang.** It is downward
facing and perfectly flat, so every angle test calls it the worst possible
overhang, and it needs no support at all because the bed is under it. Measured
on the drone arm at a 26 x 24 mm section, printed flat: 6192 mm2 of the
surface faces downward shallowly, and 3900 mm2 of that is the underside lying
on the bed. Leaving it in does not merely inflate the number -- it **reverses
the ranking**. Raw, printing on edge looks better than printing flat (5628
against 6192). With the bed removed, printing flat is less than half the cost
of on edge (2292 against 5500). A metric that skipped this would have
recommended the worse orientation.

**Normals must point out of the solid.** Every angle here is the sign of
``n . b``, so a flipped normal turns a roof into a floor -- the same failure
as trap 2 in ``AGENTS.md``, where a face orientation was applied twice. This
does not trust the file: it checks that the mesh is closed and that its signed
volume is positive, and refuses to answer otherwise, because a number computed
from inconsistent normals is plausible and wrong rather than obviously broken.

**On mesh dependence.** The area is exact for a flat-faced part and stays
bit-identical under refinement -- measured on the drone arm at 2010, 7236,
27512 and 95814 triangles, all giving 527, 5500 and 2292 mm2 for the three
build directions. On a curved surface the facets straddling the angle
threshold flip in and out, so the answer wobbles by a few per cent while
converging: a horizontal cylinder whose exact supported strip is
``R (pi/2) L = 785.40`` mm2 measured 816.97, 768.57, 777.68, 798.43 and 788.44
from 630 to 140356 triangles, the last being +0.39%. That is a bounded
discretisation wobble, unlike a stress peak at a singularity, which grows
without bound and must never be optimised directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.orthotropic import local_axes
from ..domain.printing import PrintingSettings, build_volume_overflow

#: A face centroid within this distance of the lowest point, measured along the
#: build direction, is treated as lying on the bed. It is a hair rather than
#: zero only to survive the rounding in a triangle centroid; the faces this
#: catches are exactly flat and exactly at the minimum.
_ON_BED_TOLERANCE_MM = 1.0e-6


@dataclass(frozen=True)
class PrintabilityReport:
    """What printing this shape this way up would cost."""

    #: Downward-facing area shallower than the limit, with the bed removed.
    support_area_mm2: float
    #: The same as a share of the whole surface, so parts of different sizes
    #: are comparable.
    support_area_fraction: float
    #: The shallowest downward-facing surface, in degrees from horizontal.
    #: 90 means nothing overhangs at all. Reported, never optimised: it is
    #: decided by a single triangle and jumps about as the mesh changes.
    worst_overhang_deg: float
    #: Area resting on the bed, which needs no support. Reported so the number
    #: above can be checked rather than taken on trust.
    bed_area_mm2: float
    #: How far the part exceeds the printer, in mm. Zero when it fits.
    build_volume_overflow_mm: float
    #: The thinnest wall found, in mm. ``None`` when the project did not say
    #: how thin is too thin, because that number also sets the resolution the
    #: measurement needs.
    min_wall_thickness_mm: float | None = None

    def as_metrics(self) -> dict[str, float]:
        metrics = {
            "support_area_mm2": self.support_area_mm2,
            "support_area_fraction": self.support_area_fraction,
            "worst_overhang_deg": self.worst_overhang_deg,
            "bed_area_mm2": self.bed_area_mm2,
            "build_volume_overflow_mm": self.build_volume_overflow_mm,
        }
        if self.min_wall_thickness_mm is not None:
            metrics["min_wall_thickness_mm"] = self.min_wall_thickness_mm
        return metrics


def _trimesh() -> Any:
    """Import trimesh only when it is needed, as the topology runner does."""
    try:
        import trimesh
    except ImportError as exc:  # pragma: no cover - exercised on a bare install
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            "measuring printability needs the 'trimesh' package, which is not "
            "installed. Install it with 'pip install trimesh'.",
        ) from exc
    return trimesh


#: A ray is started this far below the surface so it does not immediately hit
#: the triangle it left from. Small enough to be lost in the answer, large
#: enough to clear floating-point noise in a triangle centroid.
_RAY_START_OFFSET_MM = 1.0e-5

#: More triangles than this and the wall check is slow enough to be worth
#: saying so on the result. Measured: 33 548 triangles took 1.96 s and 132 420
#: took 10.95 s, per design.
_SLOW_TRIANGLE_COUNT = 60_000


def measure_min_wall(mesh: Any) -> float:
    """The thinnest wall in the shape, in millimetres.

    From each triangle, fire a ray straight into the solid and measure how far
    it gets before coming out the other side. The smallest such distance is the
    thinnest wall. ``trimesh`` does the ray work.

    **Why the ray, and not the sphere.** ``trimesh.proximity.thickness`` also
    offers a largest-inscribed-sphere method, and on a plate of known thickness
    it reads a third low -- 0.5333 for a 0.8 mm plate, and the same 33% on 2 mm
    and 5 mm. The ray reads 0.8000, 2.0000 and 5.0000 exactly.

    **Why triangle centres, and not corners.** Corners sit on the true surface
    and centres sit inside a curve, so sampling at corners looks like the
    obvious improvement. Measured on a 1.000 mm tube wall it is worse: 0.8567
    against 0.9106 at the same tessellation, and 0.9650 against 0.9775 at twice
    the resolution. A corner's direction has to be averaged from the faces
    around it, and that average points off the true normal.

    **What this finds, and what it does not.** It finds a wall -- a run of
    material of roughly even thickness, which is what a hollow section, a rib
    and a boss all are, and what a printer actually fails to make. It does not
    find the thin end of a taper, because the thinnest point of anything that
    tapers is its edge, where the thickness is zero by definition; measured on
    a plate running from 6 mm down to 0.5 mm, this reads 0.5502, about 10%
    high. Every chamfer on every part would otherwise report zero.
    """
    values = _trimesh().proximity.thickness(
        mesh=mesh,
        points=np.asarray(mesh.triangles_center, dtype=float)
        - np.asarray(mesh.face_normals, dtype=float) * _RAY_START_OFFSET_MM,
        exterior=False,
        normals=np.asarray(mesh.face_normals, dtype=float),
        method="ray",
    )
    values = np.asarray(values, dtype=float)
    # A ray that escapes without hitting anything comes back as infinity, and
    # one that hits its own triangle as zero or below. Neither is a wall.
    usable = values[np.isfinite(values) & (values > 0.0)]
    if usable.size == 0:
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            "no ray fired into this shape came out the other side, so its wall "
            "thickness cannot be measured.",
        )
    # Put back the distance the ray was started below the surface. It is a
    # hundredth of a micron and changes no decision, but it is a known constant
    # bias rather than noise, and leaving it in made a 3.000 mm wall report
    # 2.99999 -- a number that reads as measurement error when it is not.
    return float(usable.min()) + _RAY_START_OFFSET_MM


def _triangulate_cad(shape_path: Path, into: Path, size_mm: float) -> Path:
    """Chop a CAD shape into triangles, the way a slicer would be given it."""
    from ..geometry.gmsh_session import gmsh_session, suppress_native_output

    stl_path = into / "printability.stl"
    with gmsh_session() as gmsh, suppress_native_output():
        gmsh.model.add("printability")
        gmsh.merge(str(shape_path))
        gmsh.option.setNumber("Mesh.MeshSizeMax", size_mm)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(stl_path))
    return stl_path


def _load_closed_surface(shape_path: Path, scratch: Path, size_mm: float) -> Any:
    if shape_path.suffix.lower() != ".stl":
        shape_path = _triangulate_cad(shape_path, scratch, size_mm)

    # `process=True` is required, not incidental. An STL stores every triangle
    # with its own three vertices, so nothing is shared and the surface is
    # never watertight until duplicate vertices are merged. Loading with
    # processing off makes the closedness check below fail on a perfectly good
    # solid.
    mesh = _trimesh().load(str(shape_path), process=True)
    if not mesh.is_watertight:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"the shape in {shape_path.name} is not closed, so there is no inside "
            f"and outside to measure an overhang against.",
        )
    # Signed volume is positive only when the winding, and therefore every face
    # normal, points out of the solid. Checked rather than assumed: with the
    # normals inverted every roof becomes a floor and the support area comes
    # back as a plausible number for the wrong shape.
    if float(mesh.volume) <= 0.0:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"the triangles in {shape_path.name} wind inwards, so the surface "
            f"normals point into the solid and every overhang would be "
            f"measured upside down.",
        )
    return mesh


def measure_printability(
    shape_path: Path,
    build_direction: tuple[float, float, float],
    settings: PrintingSettings,
    *,
    scratch: Path | None = None,
) -> PrintabilityReport:
    """Measure one shape, printed one way up, against one printer.

    ``shape_path`` may be an STL, or any CAD file gmsh can read -- a BREP from
    a parametric build, most often. A CAD shape is chopped into triangles
    first, because an overhang is a property of the surface a slicer sees.
    """
    shape_path = Path(shape_path)
    mesh = _load_closed_surface(
        shape_path,
        Path(scratch) if scratch else shape_path.parent,
        settings.tessellation_mm,
    )

    axis = np.asarray(build_direction, dtype=float)
    length = float(np.linalg.norm(axis))
    if length < 1.0e-12:
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            "the build direction has no length, so there is no up.",
        )
    axis = axis / length

    normals = np.asarray(mesh.face_normals, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)

    # How steeply each face points downward. `downward` is the cosine between
    # the outward normal and straight down, so 1 is a flat ceiling and 0 is a
    # vertical wall.
    downward = -(normals @ axis)
    faces_down = downward > 0.0
    # Angle of the *surface* from horizontal, which is what a printer setting
    # refers to: a flat underside is 0 degrees and a vertical wall is 90.
    tilt_deg = np.degrees(np.arccos(np.clip(downward, -1.0, 1.0)))

    needs_support = faces_down & (tilt_deg < settings.overhang_angle_deg)

    # Remove whatever is lying on the bed. See the module docstring: this term
    # decides which orientation the metric prefers, so it is not optional.
    heights = np.asarray(mesh.triangles_center, dtype=float) @ axis
    on_bed = needs_support & ((heights - heights.min()) <= _ON_BED_TOLERANCE_MM)
    supported = needs_support & ~on_bed

    total_area = float(areas.sum())
    support_area = float(areas[supported].sum())

    across_first, across_second = local_axes(tuple(axis))
    points = np.asarray(mesh.vertices, dtype=float)
    extents = (
        float(np.ptp(points @ axis)),
        float(np.ptp(points @ np.asarray(across_first))),
        float(np.ptp(points @ np.asarray(across_second))),
    )

    return PrintabilityReport(
        support_area_mm2=support_area,
        support_area_fraction=support_area / total_area if total_area > 0 else 0.0,
        # 90 degrees, not 0, when nothing faces downward: the reading is "the
        # shallowest thing here is a vertical wall", and 0 would read as the
        # worst possible overhang.
        worst_overhang_deg=float(tilt_deg[faces_down].min()) if faces_down.any() else 90.0,
        bed_area_mm2=float(areas[on_bed].sum()),
        build_volume_overflow_mm=build_volume_overflow(extents, settings.build_volume),
        # Only when the project said how thin is too thin: that number is what
        # decides the resolution this needs, so without it there is no
        # defensible tessellation to measure at.
        min_wall_thickness_mm=(
            measure_min_wall(mesh) if settings.min_wall_check_mm is not None else None
        ),
    )
