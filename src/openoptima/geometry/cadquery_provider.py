"""Optional geometry provider backed by CadQuery.

CadQuery is *not* a required dependency (its OCP wheel is ~68 MB).  It exists
for users who want to author their own parts with a fluent modelling API rather
than a built-in template.  Install with ``pip install openoptima[cadquery]``.

A CadQuery source file must expose::

    def build(parameters: dict) -> cadquery.Workplane

and must raise ``ValueError`` (or return an empty result) for impossible
parameter combinations, which this adapter translates into an INFEASIBLE
design rather than an infrastructure error.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.project import GeometryDefinition
from ..domain.regions import BoundingBox
from ..domain.variables import DesignVector
from .base import GeometryArtifact, ValidationReport
from .gmsh_session import gmsh_session, suppress_native_output


class CadQueryGeometryProvider:
    name = "cadquery"

    def __init__(self, definition: GeometryDefinition, root: Path | None = None) -> None:
        self.definition = definition
        self.root = root or Path.cwd()
        self.fixed_parameters = dict(definition.parameters)
        self._build_function: Any | None = None

    @property
    def source_path(self) -> Path:
        if not self.definition.source:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR, "cadquery provider requires geometry.source"
            )
        path = Path(self.definition.source)
        return path if path.is_absolute() else self.root / path

    def validate_definition(self) -> ValidationReport:
        if importlib.util.find_spec("cadquery") is None:
            return ValidationReport(
                ok=False,
                errors=(
                    "cadquery is not installed. Install it with: "
                    "pip install 'openoptima[cadquery]'",
                ),
            )
        try:
            path = self.source_path
        except EvaluationFailure as exc:
            return ValidationReport(ok=False, errors=(exc.message,))
        if not path.exists():
            return ValidationReport(ok=False, errors=(f"geometry source not found: {path}",))
        try:
            self._load()
        except EvaluationFailure as exc:
            return ValidationReport(ok=False, errors=(exc.message,))
        return ValidationReport(
            ok=True,
            warnings=("CadQuery models are executable Python. Only open projects you trust.",),
        )

    def _load(self) -> Any:
        if self._build_function is not None:
            return self._build_function
        path = self.source_path
        spec = importlib.util.spec_from_file_location("openoptima_user_geometry", path)
        if spec is None or spec.loader is None:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR, f"cannot import geometry source {path}"
            )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR, f"error importing {path}: {exc}"
            ) from exc
        function = getattr(module, self.definition.template or "build", None)
        if function is None or not callable(function):
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR,
                f"{path} does not define a callable "
                f"{self.definition.template or 'build'}(parameters)",
            )
        self._build_function = function
        return function

    def build(self, design: DesignVector, output_directory: Path) -> GeometryArtifact:
        build_function = self._load()
        parameters = dict(self.fixed_parameters)
        parameters.update(design.as_dict())

        output_directory.mkdir(parents=True, exist_ok=True)
        step_path = output_directory / "model.step"
        brep_path = output_directory / "model.brep"

        try:
            result = build_function(parameters)
        except ValueError as exc:
            # By convention the model itself rejected the parameters.
            raise EvaluationFailure(
                FailureCode.INVALID_DESIGN_VARIABLES, str(exc), detail={"parameters": parameters}
            ) from exc
        except Exception as exc:
            raise EvaluationFailure(
                FailureCode.GEOMETRY_RECOMPUTE_FAILED,
                f"CadQuery build failed: {exc}",
                detail={"parameters": parameters},
            ) from exc

        try:
            import cadquery as cq

            cq.exporters.export(result, str(step_path))
        except Exception as exc:
            raise EvaluationFailure(
                FailureCode.GEOMETRY_RECOMPUTE_FAILED, f"STEP export failed: {exc}"
            ) from exc

        # Re-import through the shared OCC kernel so downstream stages see the
        # same measurements the mesher will.
        with gmsh_session() as gmsh:
            gmsh.model.add("openoptima_cadquery")
            with suppress_native_output():
                gmsh.model.occ.importShapes(str(step_path))
            gmsh.model.occ.synchronize()
            solids = gmsh.model.getEntities(3)
            if len(solids) != 1:
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"expected exactly 1 solid from {step_path.name}, found {len(solids)}",
                )
            tag = solids[0][1]
            occ = gmsh.model.occ
            volume = float(occ.getMass(3, tag))
            centre = tuple(float(c) for c in occ.getCenterOfMass(3, tag))
            bbox = BoundingBox(*(float(b) for b in occ.getBoundingBox(3, tag)))
            surface_area = float(sum(occ.getMass(2, t) for _d, t in gmsh.model.getEntities(2)))
            gmsh.write(str(brep_path))

        if volume <= 0:
            raise EvaluationFailure(
                FailureCode.INVALID_SOLID, f"solid has non-positive volume ({volume:g})"
            )

        return GeometryArtifact(
            brep_path=brep_path,
            step_path=step_path,
            volume=volume,
            bbox=bbox,
            solid_count=1,
            centre_of_mass=(centre[0], centre[1], centre[2]),
            surface_area=surface_area,
            metadata={"provider": "cadquery", "parameters": parameters},
        )
