"""Geometry provider that reads a CAD file exported from somewhere else.

SolidWorks, Fusion 360 and every other mainstream CAD package can export
STEP (ISO 10303) -- a finished shape, with no history and no design
variables. That is the whole difference from the ``occ`` and ``cadquery``
providers: this one never rebuilds the imported shape, because there is
nothing parametric to rebuild it *from*.

The import itself is not new machinery. ``cadquery_provider.py`` already
re-imports its own STEP export through this exact call --
``gmsh.model.occ.importShapes`` -- so this provider extends an already-used
path to a user-supplied file rather than adding one. What is new is trusting
a file this project did not write, which is why a failure to *read* it is
treated as a setup problem to fix rather than a bad design: the same fixed
file fails identically on every evaluation, so nothing about that failure is
a property of any one design. See ``domain/failures.py`` for why that
distinction is never guessed at.

**Where a design variable does reach the shape.** The imported solid never
changes, but a project can ask OpenOptima to add features on top of it -- a
rounded or cut-back corner -- and vary *those*. That is the only way a design
vector affects anything here, and without features every evaluation returns
the identical solid. Failures from a feature are classified the other way
round from failures reading the file, because they genuinely do depend on the
design: a round bigger than the material around the corner is a fact about
that design and nothing else. See ``geometry/features.py``.

**Never write to the source file.** ``build()`` only reads
``self.source_path``; every file it writes goes into the run's own output
directory. Guarded by
``tests/integration/test_step_import.py::TestTheSourceFileIsNeverTouched``.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.project import GeometryDefinition
from ..domain.regions import BoundingBox, SemanticRegion
from ..domain.variables import DesignVector
from .base import GeometryArtifact, ValidationReport
from .features import FeatureRecord, apply_features
from .gmsh_session import drain_log, gmsh_session, suppress_native_output

#: Extensions OpenCASCADE's importer recognises. STEP is the one this
#: provider is built and tested for -- a SolidWorks or Fusion 360 export.
#: IGES and BREP go through the identical call and should work the same way,
#: but nothing here has exercised them against a real file from either
#: format; treat them as unverified until something does.
_SUPPORTED_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep")


class StepGeometryProvider:
    """One fixed solid from an imported CAD file, plus any features added on top."""

    name = "step"

    def __init__(
        self,
        definition: GeometryDefinition,
        root: Path | None = None,
        *,
        regions: tuple[SemanticRegion, ...] = (),
    ) -> None:
        self.definition = definition
        self.root = root or Path.cwd()
        self.regions = regions

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
            solid_count, size = self._inspect(path)
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

        # Report the size the part came out as. A STEP file states its own
        # units and OpenCASCADE converts them, so a part drawn in inches
        # arrives correctly as 25.4x its drawn numbers in millimetres --
        # measured exactly, see tests. That conversion being right is
        # precisely why the size is worth printing: the numbers here will not
        # match what the user typed into their CAD package, and every load,
        # region box and limit they write in the project file has to be in
        # millimetres to match. Seeing the size here is the ten-second check
        # that the part is the one they meant.
        measured = ""
        if size is not None:
            measured = (
                f" The part measures {size[0]:.4g} x {size[1]:.4g} x {size[2]:.4g} mm; "
                f"if that is not the size you expect, check the units your CAD "
                f"package exported in. Everything you write in this project file "
                f"is in millimetres whatever the file was drawn in."
            )
        if self.definition.features:
            # The shape can change now, so the standing warning would be wrong.
            # Say what is varying instead, naming each feature, because a
            # feature that silently failed to be applied is the one thing a
            # user cannot see from the numbers alone.
            listed = "; ".join(feature.describe() for feature in self.definition.features)
            return ValidationReport(
                ok=True,
                warnings=(
                    f"The imported shape itself never changes. What changes is "
                    f"{len(self.definition.features)} feature(s) OpenOptima adds on "
                    f"top of it: {listed}." + measured,
                ),
            )
        return ValidationReport(
            ok=True,
            warnings=(
                "An imported shape has no dimensions to vary. Design variables "
                "in this project will be ignored unless they drive a feature "
                "you have added on top of the import." + measured,
            ),
        )

    @staticmethod
    def _inspect(path: Path) -> tuple[int, tuple[float, float, float] | None]:
        """Solid count, and the overall size in mm, without building anything."""
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

            solids = gmsh.model.getEntities(3)
            if len(solids) != 1:
                return len(solids), None
            bounds = gmsh.model.occ.getBoundingBox(3, solids[0][1])
            size = (
                float(bounds[3] - bounds[0]),
                float(bounds[4] - bounds[1]),
                float(bounds[5] - bounds[2]),
            )
            return 1, size

    # -- build -------------------------------------------------------------
    def build(self, design: DesignVector, output_directory: Path) -> GeometryArtifact:
        """Return the imported solid, with any features applied on top of it.

        There is nothing *in an imported file* for a design vector to drive --
        see the module docstring. What a design vector can drive is a feature
        OpenOptima adds on top: a rounded or cut-back corner whose size is a
        design variable. Without features the design vector has no effect and
        every evaluation returns the same solid.
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

            # The design vector reaches the shape here and nowhere else.
            # Fixed parameters come first so a design variable of the same
            # name overrides one, matching how the `occ` provider merges them.
            feature_records: list[FeatureRecord] = []
            if self.definition.features:
                values = {**self.definition.parameters, **design.as_dict()}
                volume_tag, feature_records = apply_features(
                    gmsh, volume_tag, self.definition.features, self.regions, values
                )

            volume = float(occ.getMass(3, volume_tag))
            if not (volume > 0.0):
                raise EvaluationFailure(
                    FailureCode.INVALID_SOLID,
                    f"{path.name} has non-positive volume ({volume:g} mm^3)",
                )

            centre = tuple(float(c) for c in occ.getCenterOfMass(3, volume_tag))
            bounds = occ.getBoundingBox(3, volume_tag)
            bbox = BoundingBox(*(float(b) for b in bounds))
            # Faces of *this solid*, not every surface in the model. Applying a
            # feature replaces the solid, and asking the model at large would
            # add up any surface the kernel has not yet swept away.
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
            metadata={
                "provider": "step",
                "source": str(path),
                "features": [record.to_dict() for record in feature_records],
            },
        )
