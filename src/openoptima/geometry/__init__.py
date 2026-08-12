"""Geometry generation."""

from __future__ import annotations

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.project import GeometryDefinition
from ..domain.regions import SemanticRegion
from .base import GeometryArtifact, GeometryProvider, ValidationReport
from .gmsh_session import gmsh_session
from .occ import OccGeometryProvider


def create_provider(
    definition: GeometryDefinition, regions: tuple[SemanticRegion, ...] = ()
) -> GeometryProvider:
    """Instantiate the provider named in a project file.

    *regions* is needed only when the project adds features -- a rounded or
    cut-back corner names the two regions it sits between, and those have to be
    resolved against the shape to find the edges. A provider given features but
    no regions refuses to build rather than quietly leaving the features off,
    because a part missing a feature it was supposed to have still meshes,
    still solves, and still returns a number.
    """
    if definition.features and not regions:
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            f"This project adds {len(definition.features)} feature(s), which are placed "
            f"where two named regions meet, but no regions were supplied to the "
            f"geometry provider. This is a wiring mistake in OpenOptima, not in the "
            f"project file.",
        )
    if definition.provider == "occ":
        return OccGeometryProvider(definition, regions)
    if definition.provider == "cadquery":
        from .cadquery_provider import CadQueryGeometryProvider

        if definition.features:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR,
                "Features are not supported on the 'cadquery' provider. A CadQuery "
                "script already builds its own shape from its own dimensions, so add "
                "the corner there and vary it as an ordinary script parameter.",
            )
        return CadQueryGeometryProvider(definition)
    if definition.provider == "step":
        from .step_provider import StepGeometryProvider

        return StepGeometryProvider(definition, regions=regions)
    raise EvaluationFailure(
        FailureCode.INTERNAL_ERROR,
        f"Unknown geometry provider {definition.provider!r}; expected 'occ', 'cadquery' or 'step'",
    )


__all__ = [
    "GeometryArtifact",
    "GeometryProvider",
    "OccGeometryProvider",
    "ValidationReport",
    "create_provider",
    "gmsh_session",
]
