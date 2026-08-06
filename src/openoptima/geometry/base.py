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
