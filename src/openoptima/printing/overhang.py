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

    def as_metrics(self) -> dict[str, float]:
        return {
            "support_area_mm2": self.support_area_mm2,
            "support_area_fraction": self.support_area_fraction,
            "worst_overhang_deg": self.worst_overhang_deg,
            "bed_area_mm2": self.bed_area_mm2,
            "build_volume_overflow_mm": self.build_volume_overflow_mm,
        }


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


#: How finely the shape is chopped into triangles when it arrives as CAD. The
#: measurement is exact for flat faces at any setting -- verified bit-identical
#: from 2010 to 95814 triangles -- so this only affects curved surfaces, where
#: it trades a few per cent of wobble against time.
_TESSELLATION_SIZE_MM = 3.0


def _triangulate_cad(shape_path: Path, into: Path) -> Path:
    """Chop a CAD shape into triangles, the way a slicer would be given it."""
    from ..geometry.gmsh_session import gmsh_session, suppress_native_output

    stl_path = into / "printability.stl"
    with gmsh_session() as gmsh, suppress_native_output():
        gmsh.model.add("printability")
        gmsh.merge(str(shape_path))
        gmsh.option.setNumber("Mesh.MeshSizeMax", _TESSELLATION_SIZE_MM)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(stl_path))
    return stl_path


def _load_closed_surface(shape_path: Path, scratch: Path) -> Any:
    if shape_path.suffix.lower() != ".stl":
        shape_path = _triangulate_cad(shape_path, scratch)

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
    mesh = _load_closed_surface(shape_path, Path(scratch) if scratch else shape_path.parent)

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
    )
