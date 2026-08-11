"""Geometry provider protocol.

A provider turns a design vector into a solid model plus the metadata the rest
of the pipeline needs.  Providers must be *pure*: same design in, same geometry
out, no dependence on ambient state, and never a write to the user's source
model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..domain.regions import BoundingBox
from ..domain.variables import DesignVector


@dataclass(frozen=True)
class GeometryArtifact:
    """A built solid on disk, plus its measured properties."""

    #: BREP file — the exchange format between the geometry and mesh stages.
    brep_path: Path
    #: STEP file for the user; not read back by the pipeline.
    step_path: Path | None
    volume: float  # mm^3
    bbox: BoundingBox
    #: Number of solids. More than one usually means the design fell apart.
    solid_count: int
    centre_of_mass: tuple[float, float, float]
    surface_area: float
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "brep": str(self.brep_path),
            "step": str(self.step_path) if self.step_path else None,
            "volume_mm3": self.volume,
            "surface_area_mm2": self.surface_area,
            "bbox": list(self.bbox.as_tuple()),
            "solid_count": self.solid_count,
            "centre_of_mass": list(self.centre_of_mass),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SurfaceArtifact:
    """A closed triangle mesh, as an alternative way into the mesher.

    A :class:`GeometryArtifact` carries a real CAD model, which knows that a
    face is a plane or a cylinder because it was built as one.  A surface
    artifact carries only triangles.  It exists so that a shape which never had
    CAD behind it -- the output of a topology optimisation, most of all -- can
    still be meshed, loaded and analysed like any other part.

    The faces are worked out by measuring the triangles; see
    ``regions/discrete.py``.  That works well for flat faces and for holes, and
    **it cannot find a rounded blend at all**, because a blend meets the faces
    it joins smoothly and there is no crease for the measurement to find.
    """

    #: STL file — the exchange format between a mesh producer and the mesher.
    stl_path: Path
    volume: float  # mm^3, from the divergence theorem over the triangles
    bbox: BoundingBox
    surface_area: float
    #: What produced it, for the run manifest. Never read back by the pipeline.
    source_description: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stl": str(self.stl_path),
            "volume_mm3": self.volume,
            "surface_area_mm2": self.surface_area,
            "bbox": list(self.bbox.as_tuple()),
            "source": self.source_description,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class GeometryProvider(Protocol):
    """Builds parametric geometry."""

    name: str

    def validate_definition(self) -> ValidationReport:
        """Check the template/source is usable before any evaluation runs."""
        ...

    def build(self, design: DesignVector, output_directory: Path) -> GeometryArtifact:
        """Generate one solid for one design vector.

        Must raise :class:`~openoptima.domain.failures.EvaluationFailure` with
        an ``INFEASIBLE`` code when the *design* is impossible, and let genuine
        bugs propagate as ordinary exceptions.
        """
        ...
