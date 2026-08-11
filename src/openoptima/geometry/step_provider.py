"""Geometry provider that reads a CAD file exported from somewhere else.

SolidWorks, Fusion 360 and every other mainstream CAD package can export
STEP (ISO 10303) -- a finished shape, with no history and no design
variables. That is the whole difference from the ``occ`` and ``cadquery``
providers: this one never rebuilds the shape, because there is nothing
parametric to rebuild it *from*. Every evaluation returns the same solid.

The import itself is not new machinery. ``cadquery_provider.py`` already
re-imports its own STEP export through this exact call --
``gmsh.model.occ.importShapes`` -- so this provider extends an already-used
path to a user-supplied file rather than adding one. What is new is trusting
a file this project did not write, which is why every failure here is
treated as a setup problem to fix, never as a bad *design*: there is no
design vector driving the shape, so nothing about the failure would change
on a different evaluation. See ``domain/failures.py`` for why that
distinction is never guessed at.

**Never write to the source file.** ``build()`` only reads
``self.source_path``; every file it writes goes into the run's own output
directory. Guarded by
``tests/integration/test_step_import.py::TestTheSourceFileIsNeverTouched``.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.project import GeometryDefinition
from ..domain.regions import BoundingBox
from ..domain.variables import DesignVector
from .base import GeometryArtifact, ValidationReport
from .gmsh_session import drain_log, gmsh_session, suppress_native_output

#: Extensions OpenCASCADE's importer recognises. STEP is the one this
#: provider is built and tested for -- a SolidWorks or Fusion 360 export.
#: IGES and BREP go through the identical call and should work the same way,
#: but nothing here has exercised them against a real file from either
#: format; treat them as unverified until something does.
_SUPPORTED_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep")


class StepGeometryProvider:
    """Reads one fixed solid from an imported CAD file. No design variables."""

    name = "step"

    def __init__(self, definition: GeometryDefinition, root: Path | None = None) -> None:
        self.definition = definition
        self.root = root or Path.cwd()

    @property
    def source_path(self) -> Path:
        if not self.definition.source:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR, "step provider requires geometry.source"
            )
        path = Path(self.definition.source)
        return path if path.is_absolute() else self.root / path

    # -- introspection ---------------------------------------------------
    def validate_definition(self) -> ValidationReport:
        try:
            path = self.source_path
        except EvaluationFailure as exc:
            return ValidationReport(ok=False, errors=(exc.message,))

        if not path.exists():
            return ValidationReport(ok=False, errors=(f"geometry source not found: {path}",))
        if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            return ValidationReport(
                ok=False,
                errors=(
                    f"{path.name}: unrecognised extension {path.suffix!r}. "
                    f"Expected one of {', '.join(_SUPPORTED_SUFFIXES)}.",
                ),
            )

        # Actually attempt the import, in a throwaway session, so a corrupt or
        # unsupported file is caught here -- in seconds, before a study starts
        # -- rather than as the first evaluation's failure. Same reasoning as
        # `openoptima doctor`: a setup mistake should surface immediately.
        try:
            solid_count = self._count_solids(path)
        except EvaluationFailure as exc:
            return ValidationReport(ok=False, errors=(exc.message,))

        if solid_count == 0:
            return ValidationReport(ok=False, errors=(f"{path.name} contains no solid body",))
        if solid_count > 1:
            return ValidationReport(
                ok=False,
                errors=(
                    f"{path.name} contains {solid_count} separate solids. "
                    f"This provider reads one part, not an assembly.",
                ),
            )
        return ValidationReport(
            ok=True,
            warnings=(
                "An imported shape has no dimensions to vary. Design variables "
                "in this project will be ignored unless they drive a feature "
                "you have added on top of the import.",
            ),
        )

    @staticmethod
    def _count_solids(path: Path) -> int:
        with gmsh_session() as gmsh:
            gmsh.model.add("openoptima_step_validate")
            try:
                with suppress_native_output():
                    gmsh.model.occ.importShapes(str(path))
                gmsh.model.occ.synchronize()
            except Exception as exc:
                messages = drain_log(gmsh)
                raise EvaluationFailure(
                    FailureCode.INTERNAL_ERROR,
                    f"could not read {path.name}: {exc}",
                    detail={"gmsh_log": messages[-20:]},
                ) from exc
            return len(gmsh.model.getEntities(3))

    # -- build -------------------------------------------------------------
    def build(self, design: DesignVector, output_directory: Path) -> GeometryArtifact:
        """Return the imported solid. *design* has no effect on the result.

        There is nothing in an imported file for a design vector to drive --
        see the module docstring. It is accepted only because every provider
        must accept one, to satisfy :class:`~openoptima.geometry.base.GeometryProvider`.
        """
        path = self.source_path
        if not path.exists():
            # validate_definition() is meant to catch this before any
            # evaluation runs. Reaching here means the file went missing
            # between validation and this call -- still a setup problem, not
            # a property of *this* design, since every design shares the one
            # fixed file.
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR, f"geometry source not found: {path}"
            )

        output_directory.mkdir(parents=True, exist_ok=True)
        brep_path = output_directory / "model.brep"
        step_path = output_directory / "model.step"

        with gmsh_session() as gmsh:
            gmsh.model.add("openoptima_step_import")
            try:
                with suppress_native_output():
                    gmsh.model.occ.importShapes(str(path))
                gmsh.model.occ.synchronize()
            except Exception as exc:
                messages = drain_log(gmsh)
                raise EvaluationFailure(
                    FailureCode.INTERNAL_ERROR,
                    f"could not read {path.name}: {exc}",
                    detail={"gmsh_log": messages[-20:]},
                ) from exc

            occ = gmsh.model.occ
            solids = gmsh.model.getEntities(3)
            if len(solids) != 1:
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"expected exactly 1 solid in {path.name}, found {len(solids)}",
                )
            volume_tag = solids[0][1]

            volume = float(occ.getMass(3, volume_tag))
            if not (volume > 0.0):
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"{path.name} has non-positive volume ({volume:g} mm^3)",
                )

            centre = tuple(float(c) for c in occ.getCenterOfMass(3, volume_tag))
            bounds = occ.getBoundingBox(3, volume_tag)
            bbox = BoundingBox(*(float(b) for b in bounds))
            surface_area = float(
                sum(occ.getMass(2, tag) for _dim, tag in gmsh.model.getEntities(2))
            )

            gmsh.write(str(brep_path))
            try:
                with suppress_native_output():
                    gmsh.write(str(step_path))
            except Exception:  # a normalised STEP copy is a convenience, not a requirement
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
            metadata={"provider": "step", "source": str(path)},
        )
