"""Geometry provider backed by gmsh's OpenCASCADE kernel.

Chosen as the default because it needs nothing beyond the mesher that is
already a hard dependency, and because it keeps geometry and meshing on the
same CAD kernel — no STEP round-trip in the middle of the loop where tolerance
differences can quietly change a model.  See ``docs/adr/0004-geometry-kernel.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.project import GeometryDefinition
from ...domain.regions import BoundingBox, SemanticRegion
from ...domain.variables import DesignVector
from ..base import GeometryArtifact, ValidationReport
from ..features import FeatureRecord, apply_features
from ..gmsh_session import drain_log, gmsh_session, suppress_native_output
from .templates import available_templates, get_template


class OccGeometryProvider:
    """Builds one of the built-in parametric templates."""

    name = "occ"

    def __init__(
        self, definition: GeometryDefinition, regions: tuple[SemanticRegion, ...] = ()
    ) -> None:
        self.definition = definition
        self.template_name = definition.template
        self.fixed_parameters = dict(definition.parameters)
        #: Needed only to place features; see ``geometry/features.py``.
        self.regions = regions

    # -- introspection -------------------------------------------------------
    def validate_definition(self) -> ValidationReport:
        try:
            template = get_template(self.template_name)
        except EvaluationFailure as exc:
            return ValidationReport(ok=False, errors=(exc.message,))
        warnings: list[str] = []
        unknown = set(self.fixed_parameters) - set(template.defaults)
        if unknown:
            warnings.append(
                f"template {template.name!r} does not use parameter(s): "
                f"{', '.join(sorted(unknown))}"
            )
        return ValidationReport(ok=True, warnings=tuple(warnings))

    def describe(self) -> str:
        template = get_template(self.template_name)
        return f"{template.name}: {template.description}"

    @staticmethod
    def list_templates() -> list[str]:
        return [t.name for t in available_templates()]

    # -- build ---------------------------------------------------------------
    def build(self, design: DesignVector, output_directory: Path) -> GeometryArtifact:
        template = get_template(self.template_name)
        parameters: dict[str, Any] = dict(self.fixed_parameters)
        parameters.update(design.as_dict())

        output_directory.mkdir(parents=True, exist_ok=True)
        brep_path = output_directory / "model.brep"
        step_path = output_directory / "model.step"

        with gmsh_session() as gmsh:
            gmsh.model.add(f"openoptima_{template.name}")
            try:
                volume_tag = template.build(gmsh, parameters)
            except EvaluationFailure:
                raise
            except Exception as exc:  # kernel blew up on a legal-looking design
                messages = drain_log(gmsh)
                raise EvaluationFailure(
                    FailureCode.GEOMETRY_RECOMPUTE_FAILED,
                    f"template {template.name!r} failed to build: {exc}",
                    detail={"gmsh_log": messages[-20:], "parameters": parameters},
                ) from exc

            occ = gmsh.model.occ
            occ.synchronize()

            solids = gmsh.model.getEntities(3)
            if len(solids) != 1:
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"expected exactly 1 solid, model has {len(solids)}",
                    detail={"parameters": parameters},
                )

            feature_records: list[FeatureRecord] = []
            if self.definition.features:
                volume_tag, feature_records = apply_features(
                    gmsh, volume_tag, self.definition.features, self.regions, parameters
                )

            volume = float(occ.getMass(3, volume_tag))
            if not (volume > 0.0):
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"solid has non-positive volume ({volume:g} mm^3)",
                    detail={"parameters": parameters},
                )

            centre = tuple(float(c) for c in occ.getCenterOfMass(3, volume_tag))
            bounds = occ.getBoundingBox(3, volume_tag)
            bbox = BoundingBox(*(float(b) for b in bounds))
            # Faces of *this solid*. Applying a feature replaces the solid, so
            # asking the model at large would add up surfaces that are no
            # longer part of the part.
            surface_area = float(
                sum(
                    occ.getMass(2, abs(int(tag)))
                    for _dim, tag in gmsh.model.getBoundary(
                        [(3, volume_tag)], combined=False, oriented=False, recursive=False
                    )
                )
            )

            gmsh.write(str(brep_path))
            try:
                with suppress_native_output():
                    gmsh.write(str(step_path))
            except Exception:  # STEP is a convenience, not a requirement
                step_path = None  # type: ignore[assignment]

            warnings = [m for m in drain_log(gmsh) if m.startswith("Warning")]

        return GeometryArtifact(
            brep_path=brep_path,
            step_path=step_path,
            volume=volume,
            bbox=bbox,
            solid_count=1,
            centre_of_mass=(centre[0], centre[1], centre[2]),
            surface_area=surface_area,
            warnings=warnings[:20],
            metadata={
                "template": template.name,
                "parameters": parameters,
                "features": [record.to_dict() for record in feature_records],
            },
        )
