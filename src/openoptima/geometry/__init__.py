"""Geometry generation."""

from __future__ import annotations

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.project import GeometryDefinition
from .base import GeometryArtifact, GeometryProvider, ValidationReport
from .gmsh_session import gmsh_session
from .occ import OccGeometryProvider


def create_provider(definition: GeometryDefinition) -> GeometryProvider:
    """Instantiate the provider named in a project file."""
    if definition.provider == "occ":
        return OccGeometryProvider(definition)
    if definition.provider == "cadquery":
        from .cadquery_provider import CadQueryGeometryProvider

        return CadQueryGeometryProvider(definition)
    raise EvaluationFailure(
        FailureCode.INTERNAL_ERROR,
        f"Unknown geometry provider {definition.provider!r}; expected 'occ' or 'cadquery'",
    )


__all__ = [
    "GeometryArtifact",
    "GeometryProvider",
    "OccGeometryProvider",
    "ValidationReport",
    "create_provider",
    "gmsh_session",
]
