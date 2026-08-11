"""Business logic behind the 3D face-picking view.

Same shape as ``checks.py``: this holds the work, ``server.py`` holds the
HTTP plumbing. Two things happen here that ``openoptima faces`` (the
command-line version) also does, for the same reason -- see
``regions/describe.py`` for why both are load-bearing rather than
belt-and-braces:

* The part is built at its smallest and largest sizes as well as its
  current one, and a candidate description is only ever offered if it
  survives all three. A description checked against a single shape is a
  description nobody has tested.
* A face tag is only ever used within the one gmsh build that produced it.
  ``FaceView`` holds exactly one build's worth of signatures, is replaced
  wholesale on every ``build_view`` call, and a click against a stale view
  is refused rather than resolved against the wrong shape.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.failures import EvaluationFailure
from ..domain.project import Project
from ..geometry.gmsh_session import gmsh_session
from ..geometry.tessellate import tessellate_solid
from ..regions.describe import BuildSample, describe_faces, selector_to_yaml
from ..regions.signature import FaceSignature, solid_face_signatures


@dataclass
class FaceView:
    """One built shape, ready to have its faces clicked and described."""

    project_path: str
    signatures: list[FaceSignature]
    scale_length: float
    shape_can_change: bool
    alternatives: list[BuildSample] = field(default_factory=list)
    #: A small integer identifying this exact build, bumped on every
    #: ``build_view`` call. Sent to the browser and echoed back on every
    #: describe request, so a click that arrives after the user has already
    #: reopened or resized the part is refused rather than resolved against
    #: geometry that no longer exists.
    generation: int = 0

    def signature(self, tag: int) -> FaceSignature | None:
        return next((s for s in self.signatures if s.tag == tag), None)


def build_view(
    project: Project, path: Path, provider: Any, generation: int
) -> tuple[FaceView, dict[str, Any]]:
    """Build the part, tessellate it for display, and measure its faces.

    Also builds the extremes of the design range, for every description
    generated from this view to be checked against -- the same three shapes
    ``openoptima doctor`` and ``openoptima faces`` already build.
    """
    with tempfile.TemporaryDirectory(prefix="openoptima-faces-") as scratch_name:
        scratch = Path(scratch_name)
        artifact = provider.build(project.design_space.defaults(), scratch / "default")
        with gmsh_session() as gmsh:
            gmsh.model.add("faces_view")
            gmsh.model.occ.importShapes(str(artifact.brep_path))
            gmsh.model.occ.synchronize()
            volume_tag = gmsh.model.getEntities(3)[0][1]
            signatures = solid_face_signatures(gmsh, volume_tag)
            mesh = tessellate_solid(gmsh, volume_tag, scale_length=artifact.bbox.diagonal)

        alternatives: list[BuildSample] = []
        shape_can_change = len(project.design_space) > 0
        if shape_can_change:
            lower, upper = project.design_space.bounds()
            extremes = (
                ("smallest", "the smallest allowed size", list(lower)),
                ("largest", "the largest allowed size", list(upper)),
            )
            for directory_name, label, values in extremes:
                design = project.design_space.from_array(values)
                try:
                    other = provider.build(design, scratch / directory_name)
                except EvaluationFailure:
                    continue
                with gmsh_session() as gmsh:
                    gmsh.model.add("faces_alt")
                    gmsh.model.occ.importShapes(str(other.brep_path))
                    gmsh.model.occ.synchronize()
                    other_tag = gmsh.model.getEntities(3)[0][1]
                    other_signatures = solid_face_signatures(gmsh, other_tag)
                alternatives.append(BuildSample(other_signatures, other.bbox.diagonal, label))

    view = FaceView(
        project_path=str(path),
        signatures=signatures,
        scale_length=artifact.bbox.diagonal,
        shape_can_change=shape_can_change,
        alternatives=alternatives,
        generation=generation,
    )
    payload = {
        "generation": generation,
        "checked_against": len(alternatives),
        "shape_can_change": shape_can_change,
        "mesh": mesh.to_dict(),
        "faces": [
            {
                "tag": s.tag,
                "surface_type": s.surface_type.value,
                "area_mm2": s.area,
            }
            for s in signatures
        ],
    }
    return view, payload


def describe_selection(view: FaceView, tags: list[int]) -> dict[str, Any]:
    """Turn the tags a click (or shift-click) selected into a description.

    Returns a JSON-shaped dict either way -- a refusal is not an HTTP error,
    it is the correct outcome for two faces that cannot be told apart, and the
    caller needs to show the user why, not a generic failure page.
    """
    targets = [view.signature(tag) for tag in tags]
    missing = [tag for tag, sig in zip(tags, targets, strict=True) if sig is None]
    if missing:
        return {"ok": False, "error": f"face(s) {missing} are not part of the current view"}

    try:
        described = describe_faces(
            [t for t in targets if t is not None],
            view.signatures,
            scale_length=view.scale_length,
            name="picked_region",
            alternatives=view.alternatives,
            shape_can_change=view.shape_can_change,
        )
    except EvaluationFailure as exc:
        return {"ok": False, "error": exc.message}

    return {
        "ok": True,
        "explanation": described.explanation,
        "filters_used": list(described.filters_used),
        "checked_against": described.checked_against,
        "warnings": list(described.warnings),
        "yaml": selector_to_yaml(described.selector),
    }
