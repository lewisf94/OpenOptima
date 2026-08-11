"""Getting a meshable solid into gmsh, from CAD or from triangles.

There are two ways a shape reaches the mesher, and they need different handling
right up to the point where a volume exists and its faces have been named.
After that everything is identical -- the same size fields, the same quality
gates, the same extraction -- so the difference is isolated here rather than
spread through the mesher.

**From CAD.** A BREP file carries real surfaces: this face *is* a plane, that
one *is* a cylinder of 6 mm radius. OpenCASCADE is asked, and it answers.

**From triangles.** An STL carries no such thing. The faces have to be measured
from the triangles (``regions/discrete.py``), which is how a topology result
gets its loads and supports back. Two differences from CAD matter enough to
name:

*  One physical face arrives as several pieces, so the pieces are put back
   together before any selector sees them. A region therefore covers several
   gmsh surfaces, and :class:`Loaded` carries the mapping.
*  **The midside nodes must stay straight.** Measured on a real topology
   result: curving them onto the surface turned 6 of 2060 elements inside out,
   and a finer mesh did not fix it. Straight midsides gave the identical volume
   with no inverted elements at all. This is not a quality setting to tune -- a
   triangle mesh *is* flat facets, so there is no true curved surface for a
   midside node to sit on, and pushing it onto the faceted approximation only
   distorts the element.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.regions import BoundingBox, FaceSignature
from ..geometry.base import GeometryArtifact, SurfaceArtifact
from ..geometry.gmsh_session import suppress_native_output
from ..regions.discrete import measure_discrete_surface
from ..regions.signature import solid_face_signatures

#: Angle between neighbouring triangles above which gmsh treats the join as an
#: edge of the shape rather than the inside of a face.  Thirty degrees is small
#: enough to keep genuinely different faces apart; the over-splitting it causes
#: is undone by putting flat pieces back together afterwards.  Measured on a
#: real topology result: 57 pieces became 46 faces, and every flat face of the
#: original design space came back whole.
FEATURE_ANGLE_DEG = 30.0


@dataclass(frozen=True)
class Loaded:
    """A solid inside a live gmsh session, with its faces measured."""

    volume_tag: int
    signatures: list[FaceSignature]
    #: Face tag as the selectors see it -> the gmsh surface tags underneath it.
    #: One-to-one for CAD; one-to-many for a surface put back together.
    patches: dict[int, tuple[int, ...]]
    #: Volume the mesh is checked against, in mm3.
    reference_volume: float
    bbox: BoundingBox
    #: May the midside nodes be pushed onto the true surface?  Only for CAD.
    curved_midsides: bool
    warnings: tuple[str, ...] = ()

    @property
    def scale_length(self) -> float:
        """Model size, used to keep the region tolerances scale-free."""
        return self.bbox.diagonal

    def gmsh_tags(self, face_tags: tuple[int, ...]) -> list[int]:
        expanded: list[int] = []
        for tag in face_tags:
            expanded.extend(self.patches[int(tag)])
        return expanded


def load_brep(gmsh: Any, geometry: GeometryArtifact) -> Loaded:
    """Import a CAD solid and ask OpenCASCADE what its faces are."""
    with suppress_native_output():
        gmsh.model.occ.importShapes(str(geometry.brep_path))
    gmsh.model.occ.synchronize()

    solids = gmsh.model.getEntities(3)
    if len(solids) != 1:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"reloaded geometry has {len(solids)} solids, expected 1",
        )
    volume_tag = int(solids[0][1])
    signatures = solid_face_signatures(gmsh, volume_tag)
    return Loaded(
        volume_tag=volume_tag,
        signatures=signatures,
        patches={signature.tag: (signature.tag,) for signature in signatures},
        reference_volume=geometry.volume,
        bbox=geometry.bbox,
        curved_midsides=True,
    )


def load_surface(
    gmsh: Any, surface: SurfaceArtifact, *, feature_angle_deg: float = FEATURE_ANGLE_DEG
) -> Loaded:
    """Load a triangle mesh, work out its faces, and build a solid from it."""
    with suppress_native_output():
        gmsh.merge(str(surface.stl_path))
    if not gmsh.model.getEntities(2):
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"{surface.stl_path.name} contains no triangles, so there is no shape to mesh",
        )

    # Cut the triangle soup into faces at its creases, and give each face a
    # description gmsh can mesh against.
    with suppress_native_output():
        gmsh.model.mesh.classifySurfaces(
            math.radians(feature_angle_deg), True, True, math.radians(180.0)
        )
    measured = measure_discrete_surface(gmsh)
    if not measured.signatures:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"nothing measurable was found on {surface.stl_path.name}",
        )
    with suppress_native_output():
        gmsh.model.mesh.createGeometry()

    # One shell is an ordinary solid. A second shell is a sealed bubble inside
    # the part, and it is passed to addVolume as a hole so the mesher leaves it
    # empty instead of filling it with material that is not there.
    loops = [gmsh.model.geo.addSurfaceLoop(list(shell)) for shell in measured.shells]
    if not loops:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"{surface.stl_path.name} does not close up into a solid",
        )
    volume_tag = int(gmsh.model.geo.addVolume(loops))
    gmsh.model.geo.synchronize()

    warnings = list(measured.warnings)
    if len(loops) > 1:
        warnings.append(
            f"The part has {len(loops) - 1} sealed void(s) inside it. They have "
            f"been left empty, which is right, but a sealed void cannot be made "
            f"by machining or casting and traps powder in a printed part."
        )

    return Loaded(
        volume_tag=volume_tag,
        signatures=list(measured.signatures),
        patches=dict(measured.patches),
        reference_volume=measured.volume,
        bbox=measured.bbox,
        # Straight midsides. See the module docstring: curving them onto a
        # faceted surface turned elements inside out on a real result.
        curved_midsides=False,
        warnings=tuple(warnings),
    )
