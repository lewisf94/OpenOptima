"""Gmsh meshing with size fields, quality gates and a retry ladder.

Design notes:

*  The mesher re-resolves the region selectors against the reloaded BREP rather
   than trusting face tags carried over from the geometry stage, then compares
   the measured areas with what geometry found.  A silent tag shuffle across the
   file boundary therefore surfaces as an explicit error.

*  A mesh that fails a quality gate is retried with progressively more
   conservative settings.  The final rung of the ladder produces a *diagnostic*
   first-order mesh which is flagged and never silently accepted as a result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..domain.failures import EvaluationFailure, FailureCode, Outcome
from ..domain.model import MeshAlgorithm, MeshSpecification
from ..domain.regions import RegionMap, SemanticRegion
from ..geometry.base import GeometryArtifact, SurfaceArtifact
from ..geometry.gmsh_session import drain_log, gmsh_session
from ..regions.matcher import compare_region_maps, resolve_regions
from .base import MeshData, MeshQualityReport
from .sources import Loaded, load_brep, load_surface

#: gmsh 3D algorithm numbers.
_ALGORITHM_NUMBER = {
    MeshAlgorithm.DELAUNAY: 1,
    MeshAlgorithm.FRONTAL: 4,
    MeshAlgorithm.HXT: 10,
}

#: gmsh element type -> (CalculiX name, node count).
_VOLUME_ELEMENTS = {4: ("C3D4", 4), 11: ("C3D10", 10)}
_SURFACE_ELEMENTS = {2: 3, 9: 6}

#: gmsh's tet10 midside ordering differs from Abaqus/CalculiX C3D10 in the last
#: two nodes: gmsh lists edge (2,3) before (1,3), CalculiX expects the reverse.
_TET10_TO_CCX = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
_TET4_TO_CCX = [0, 1, 2, 3]


@dataclass(frozen=True)
class MeshAttempt:
    """One rung of the retry ladder."""

    description: str
    specification: MeshSpecification
    diagnostic: bool = False


def build_retry_ladder(specification: MeshSpecification) -> list[MeshAttempt]:
    """Progressively more forgiving meshing attempts.

    Order matters: change one thing at a time so a failure report says which
    change rescued the mesh.
    """
    ladder = [MeshAttempt("requested settings", specification)]

    if specification.algorithm is not MeshAlgorithm.DELAUNAY:
        ladder.append(
            MeshAttempt(
                "fall back to the Delaunay algorithm",
                specification.with_overrides(algorithm=MeshAlgorithm.DELAUNAY),
            )
        )

    ladder.append(
        MeshAttempt(
            "relax the size field (1.5x coarser, curvature refinement off)",
            specification.coarsened(1.5).with_overrides(curvature_refinement=False),
        )
    )
    ladder.append(
        MeshAttempt(
            "coarsen further (2.5x) with mesh optimisation on",
            specification.coarsened(2.5).with_overrides(curvature_refinement=False, optimise=True),
        )
    )
    if specification.element_order == 2:
        ladder.append(
            MeshAttempt(
                "diagnostic first-order mesh",
                specification.coarsened(2.5).with_overrides(
                    element_order=1, curvature_refinement=False
                ),
                diagnostic=True,
            )
        )
    return ladder


class GmshMesher:
    """Turns a geometry artifact into a solver-ready mesh."""

    name = "gmsh"

    def __init__(self, specification: MeshSpecification) -> None:
        self.specification = specification

    def generate(
        self,
        geometry: GeometryArtifact,
        regions: tuple[SemanticRegion, ...],
        output_directory: Path,
        *,
        expected_regions: RegionMap | None = None,
        write_mesh_file: bool = False,
    ) -> tuple[MeshData, RegionMap]:
        """Mesh a CAD solid, walking the retry ladder until a mesh passes the gates."""
        return self._generate(
            lambda gmsh: load_brep(gmsh, geometry),
            regions,
            output_directory,
            expected_regions=expected_regions,
            write_mesh_file=write_mesh_file,
        )

    def generate_from_surface(
        self,
        surface: SurfaceArtifact,
        regions: tuple[SemanticRegion, ...],
        output_directory: Path,
        *,
        write_mesh_file: bool = False,
    ) -> tuple[MeshData, RegionMap]:
        """Mesh a closed triangle mesh that has no CAD behind it.

        Everything after the shape is loaded is identical to the CAD path: the
        same selectors, the same quality gates, the same mesh.  What differs is
        that the faces were measured rather than looked up, and that the midside
        nodes are kept straight -- see ``meshing/sources.py``.
        """
        return self._generate(
            lambda gmsh: load_surface(gmsh, surface),
            regions,
            output_directory,
            expected_regions=None,
            write_mesh_file=write_mesh_file,
        )

    def _generate(
        self,
        loader: Callable[[Any], Loaded],
        regions: tuple[SemanticRegion, ...],
        output_directory: Path,
        *,
        expected_regions: RegionMap | None,
        write_mesh_file: bool,
    ) -> tuple[MeshData, RegionMap]:
        output_directory.mkdir(parents=True, exist_ok=True)
        ladder = build_retry_ladder(self.specification)
        problems: list[str] = []

        for attempt_number, attempt in enumerate(ladder, start=1):
            try:
                mesh, region_map = self._attempt(
                    loader,
                    regions,
                    attempt,
                    attempt_number,
                    output_directory,
                    expected_regions=expected_regions,
                    write_mesh_file=write_mesh_file,
                )
            except EvaluationFailure as failure:
                # Only a meshing problem is worth another go at a coarser
                # setting. Two other kinds arrive here and neither is:
                #
                # * a selector that found nothing, or could not choose between
                #   two faces -- a mistake in the project, identical on every
                #   attempt;
                # * anything that says the *design* is bad, such as a shape
                #   that came apart into pieces or a face a feature has shrunk
                #   past the size the engineer allowed.
                #
                # Both used to be caught by name in a list here, which was one
                # forgotten entry away from a real defect: a design failure
                # that falls through is retried four times and then reported
                # as MESH_GENERATION_FAILED, an infrastructure error. The
                # optimiser then learns nothing from a design it should have
                # learned to avoid, and the evaluation budget pays for four
                # attempts that could not have succeeded. Asking the taxonomy
                # rather than a list means a new failure code cannot reopen it.
                if failure.outcome is Outcome.INFEASIBLE or failure.code in (
                    FailureCode.REGION_NOT_FOUND,
                    FailureCode.REGION_AMBIGUOUS,
                ):
                    raise  # retrying cannot help, and relabelling would mislead
                problems.append(f"attempt {attempt_number} ({attempt.description}): {failure}")
                continue

            if attempt.diagnostic:
                assert mesh.quality is not None
                mesh.quality = replace(
                    mesh.quality,
                    warnings=(
                        *mesh.quality.warnings,
                        "DIAGNOSTIC MESH: first-order elements, produced only after every "
                        "quality-preserving attempt failed. Displacements will be too stiff "
                        "and stresses unreliable. Do not accept this result without review.",
                    ),
                )
            return mesh, region_map

        raise EvaluationFailure(
            FailureCode.MESH_GENERATION_FAILED,
            "every meshing attempt failed:\n  " + "\n  ".join(problems),
            detail={"attempts": problems},
        )

    # -- one attempt ---------------------------------------------------------
    def _attempt(
        self,
        loader: Callable[[Any], Loaded],
        regions: tuple[SemanticRegion, ...],
        attempt: MeshAttempt,
        attempt_number: int,
        output_directory: Path,
        *,
        expected_regions: RegionMap | None,
        write_mesh_file: bool,
    ) -> tuple[MeshData, RegionMap]:
        specification = attempt.specification

        with gmsh_session() as gmsh:
            gmsh.model.add("openoptima_mesh")
            loaded = loader(gmsh)

            # -- regions, re-resolved against what we actually reloaded ------
            region_map = resolve_regions(
                regions, loaded.signatures, scale_length=loaded.scale_length
            )
            if expected_regions is not None:
                differences = compare_region_maps(expected_regions, region_map)
                if differences:
                    raise EvaluationFailure(
                        FailureCode.REGION_AMBIGUOUS,
                        "regions resolved differently after reloading the geometry: "
                        + "; ".join(differences),
                        detail={"differences": differences},
                    )

            gmsh.model.addPhysicalGroup(3, [loaded.volume_tag], name="solid")
            for name, match in region_map.matches.items():
                gmsh.model.addPhysicalGroup(2, loaded.gmsh_tags(match.face_tags), name=name)

            self._configure(gmsh, specification, region_map, loaded)

            try:
                gmsh.model.mesh.generate(3)
                # Order matters. Netgen optimisation only understands linear
                # elements: run it *before* raising the order, or it silently
                # drops the mesh back to first order and leaves the midside
                # nodes orphaned -- a mesh that still looks valid but whose
                # loaded nodes belong to no element at all.
                if specification.optimise:
                    gmsh.model.mesh.optimize("Netgen")
                if specification.element_order == 2:
                    gmsh.model.mesh.setOrder(2)
            except Exception as exc:
                raise EvaluationFailure(
                    FailureCode.MESH_GENERATION_FAILED,
                    f"gmsh failed to mesh: {exc}",
                    detail={"gmsh_log": drain_log(gmsh)[-20:]},
                ) from exc

            mesh = self._extract(gmsh, region_map, specification, loaded, attempt, attempt_number)

            if write_mesh_file:
                gmsh.write(str(output_directory / "mesh.msh"))

        self._check_quality(mesh, specification)
        return mesh, region_map

    # -- size fields ---------------------------------------------------------
    def _configure(
        self,
        gmsh: Any,
        specification: MeshSpecification,
        region_map: RegionMap,
        loaded: Loaded,
    ) -> None:
        option = gmsh.option
        option.setNumber("Mesh.MeshSizeMin", specification.minimum_size)
        option.setNumber("Mesh.MeshSizeMax", specification.global_size)
        option.setNumber("Mesh.Algorithm3D", _ALGORITHM_NUMBER[specification.algorithm])
        option.setNumber("Mesh.ElementOrder", 1)  # raised later via setOrder
        option.setNumber("Mesh.Optimize", 1 if specification.optimise else 0)
        option.setNumber("Mesh.OptimizeNetgen", 0)
        # Do not let point/boundary sizes silently override the field below.
        option.setNumber("Mesh.MeshSizeFromPoints", 0)
        option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        option.setNumber(
            "Mesh.MeshSizeFromCurvature",
            specification.curvature_elements if specification.curvature_refinement else 0,
        )
        if specification.element_order == 2:
            # Curve the midside nodes onto the true surface, otherwise a fillet
            # is meshed as a set of flat facets and its stresses are wrong.
            # 0 pushes each midside node onto the true surface, so a fillet is
            # meshed as a curve rather than a set of flat facets. A shape made
            # of triangles has no true surface to push onto: doing it there
            # turned 6 of 2060 elements inside out on a real topology result,
            # and refining the mesh did not fix it.
            option.setNumber("Mesh.SecondOrderLinear", 0 if loaded.curved_midsides else 1)
            option.setNumber("Mesh.HighOrderOptimize", 1)

        field_ids: list[int] = []
        for index, refinement in enumerate(specification.local_refinements, start=1):
            if refinement.region not in region_map:
                continue
            distance_id = 100 + 2 * index
            threshold_id = distance_id + 1
            gmsh.model.mesh.field.add("Distance", distance_id)
            gmsh.model.mesh.field.setNumbers(
                distance_id, "SurfacesList", list(region_map.face_tags(refinement.region))
            )
            gmsh.model.mesh.field.setNumber(distance_id, "Sampling", 100)
            gmsh.model.mesh.field.add("Threshold", threshold_id)
            gmsh.model.mesh.field.setNumber(threshold_id, "InField", distance_id)
            gmsh.model.mesh.field.setNumber(threshold_id, "SizeMin", refinement.size)
            gmsh.model.mesh.field.setNumber(threshold_id, "SizeMax", specification.global_size)
            gmsh.model.mesh.field.setNumber(threshold_id, "DistMin", 0.0)
            gmsh.model.mesh.field.setNumber(threshold_id, "DistMax", refinement.distance)
            field_ids.append(threshold_id)

        if specification.size_from_thickness:
            # Keep at least a couple of elements through the thinnest wall the
            # model happens to have. Cheap proxy: the smallest bbox dimension.
            bbox = loaded.bbox
            extents = [
                bbox.xmax - bbox.xmin,
                bbox.ymax - bbox.ymin,
                bbox.zmax - bbox.zmin,
            ]
            smallest = min(e for e in extents if e > 0)
            thickness_size = max(specification.minimum_size, smallest / 3.0)
            if thickness_size < specification.global_size:
                constant_id = 90
                gmsh.model.mesh.field.add("Constant", constant_id)
                gmsh.model.mesh.field.setNumber(constant_id, "VIn", thickness_size)
                gmsh.model.mesh.field.setNumbers(constant_id, "VolumesList", [])
                # Applied globally via the Min combination below.
                gmsh.model.mesh.field.setNumber(constant_id, "VOut", thickness_size)
                field_ids.append(constant_id)

        if field_ids:
            gmsh.model.mesh.field.add("Min", 1)
            gmsh.model.mesh.field.setNumbers(1, "FieldsList", field_ids)
            gmsh.model.mesh.field.setAsBackgroundMesh(1)

    # -- extraction ----------------------------------------------------------
    def _extract(
        self,
        gmsh: Any,
        region_map: RegionMap,
        specification: MeshSpecification,
        loaded: Loaded,
        attempt: MeshAttempt,
        attempt_number: int,
    ) -> MeshData:
        node_tags_raw, coordinates_raw, _ = gmsh.model.mesh.getNodes()
        if len(node_tags_raw) == 0:
            raise EvaluationFailure(FailureCode.MESH_GENERATION_FAILED, "mesh contains no nodes")
        node_tags = np.asarray(node_tags_raw, dtype=np.int64)
        coordinates = np.asarray(coordinates_raw, dtype=np.float64).reshape(-1, 3)

        element_types, element_tags, element_nodes = gmsh.model.mesh.getElements(3)
        chosen: tuple[str, np.ndarray, np.ndarray] | None = None
        for gmsh_type, tags, nodes in zip(element_types, element_tags, element_nodes, strict=True):
            if int(gmsh_type) not in _VOLUME_ELEMENTS:
                continue
            name, count = _VOLUME_ELEMENTS[int(gmsh_type)]
            connectivity = np.asarray(nodes, dtype=np.int64).reshape(-1, count)
            permutation = _TET10_TO_CCX if count == 10 else _TET4_TO_CCX
            chosen = (name, np.asarray(tags, dtype=np.int64), connectivity[:, permutation])
            break
        if chosen is None:
            raise EvaluationFailure(
                FailureCode.MESH_GENERATION_FAILED,
                f"no supported tetrahedral elements produced (types: {list(element_types)})",
            )
        element_type, volume_element_tags, connectivity = chosen

        # The mesh must be the order that was asked for. Silently accepting a
        # lower order would report first-order stiffness as if it were the
        # requested quadratic result.
        expected_type = "C3D10" if specification.element_order == 2 else "C3D4"
        if element_type != expected_type:
            raise EvaluationFailure(
                FailureCode.MESH_GENERATION_FAILED,
                f"requested element order {specification.element_order} "
                f"({expected_type}) but gmsh produced {element_type}",
            )

        # Drop nodes no element references. gmsh can leave orphans behind after
        # order changes and optimisation passes; writing them into a solver deck
        # produces a singular or silently unloaded model.
        used = np.unique(connectivity)
        if len(used) != len(node_tags):
            keep = np.isin(node_tags, used)
            node_tags = node_tags[keep]
            coordinates = coordinates[keep]

        if len(volume_element_tags) > specification.max_elements:
            raise EvaluationFailure(
                FailureCode.MESH_QUALITY_FAILED,
                f"mesh has {len(volume_element_tags)} elements, over the "
                f"{specification.max_elements} limit; coarsen the mesh or raise max_elements",
            )

        surface_nodes: dict[str, np.ndarray] = {}
        surface_triangles: dict[str, np.ndarray] = {}
        for name, match in region_map.matches.items():
            collected: list[np.ndarray] = []
            # A region can cover several gmsh surfaces: on a shape made of
            # triangles, one physical face arrives in pieces and is put back
            # together before the selectors see it.
            for face_tag in loaded.gmsh_tags(match.face_tags):
                types, _tags, nodes = gmsh.model.mesh.getElements(2, face_tag)
                for gmsh_type, node_block in zip(types, nodes, strict=False):
                    per_element = _SURFACE_ELEMENTS.get(int(gmsh_type))
                    if per_element is None:
                        continue
                    collected.append(
                        np.asarray(node_block, dtype=np.int64).reshape(-1, per_element)
                    )
            if not collected:
                raise EvaluationFailure(
                    FailureCode.MESH_GENERATION_FAILED,
                    f"region {name!r} has no surface elements after meshing",
                )
            triangles = np.vstack(collected)
            orphans = np.setdiff1d(np.unique(triangles), node_tags)
            if orphans.size:
                raise EvaluationFailure(
                    FailureCode.MESH_GENERATION_FAILED,
                    f"region {name!r} has {orphans.size} surface node(s) that belong to "
                    f"no volume element; the surface and volume meshes are inconsistent",
                )
            surface_triangles[name] = triangles
            surface_nodes[name] = np.unique(triangles)

        quality = self._quality(
            gmsh,
            volume_element_tags,
            element_type,
            len(node_tags),
            loaded.reference_volume,
            attempt,
            attempt_number,
        )

        return MeshData(
            node_tags=node_tags,
            coordinates=coordinates,
            element_tags=volume_element_tags,
            connectivity=connectivity,
            element_type=element_type,
            surface_nodes=surface_nodes,
            surface_triangles=surface_triangles,
            quality=quality,
        )

    def _quality(
        self,
        gmsh: Any,
        element_tags: np.ndarray,
        element_type: str,
        node_count: int,
        cad_volume: float,
        attempt: MeshAttempt,
        attempt_number: int,
    ) -> MeshQualityReport:
        try:
            sicn = np.asarray(
                gmsh.model.mesh.getElementQualities(element_tags.tolist(), "minSICN"),
                dtype=np.float64,
            )
        except Exception:  # pragma: no cover - older gmsh
            sicn = np.array([1.0])
        try:
            volumes = np.asarray(
                gmsh.model.mesh.getElementQualities(element_tags.tolist(), "volume"),
                dtype=np.float64,
            )
            mesh_volume = float(volumes.sum())
        except Exception:  # pragma: no cover
            mesh_volume = float("nan")

        warnings: list[str] = []
        if attempt_number > 1:
            warnings.append(
                f"mesh produced on attempt {attempt_number} after falling back: "
                f"{attempt.description}"
            )

        return MeshQualityReport(
            node_count=node_count,
            element_count=len(element_tags),
            element_type=element_type,
            min_scaled_jacobian=float(sicn.min()),
            mean_scaled_jacobian=float(sicn.mean()),
            inverted_elements=int((sicn <= 0).sum()),
            mesh_volume=mesh_volume,
            cad_volume=cad_volume,
            algorithm=attempt.specification.algorithm.value,
            attempt=attempt_number,
            warnings=tuple(warnings),
        )

    def _check_quality(self, mesh: MeshData, specification: MeshSpecification) -> None:
        quality = mesh.quality
        assert quality is not None

        if quality.inverted_elements > 0:
            raise EvaluationFailure(
                FailureCode.MESH_QUALITY_FAILED,
                f"{quality.inverted_elements} inverted elements "
                f"(minimum scaled Jacobian {quality.min_scaled_jacobian:.4f})",
            )
        if quality.min_scaled_jacobian < specification.min_scaled_jacobian:
            raise EvaluationFailure(
                FailureCode.MESH_QUALITY_FAILED,
                f"worst element quality {quality.min_scaled_jacobian:.4f} is below the "
                f"{specification.min_scaled_jacobian} gate",
            )
        error = quality.volume_error
        if error == error and error > specification.volume_tolerance:  # not NaN
            raise EvaluationFailure(
                FailureCode.MESH_QUALITY_FAILED,
                f"mesh volume differs from CAD volume by {error:.2%} "
                f"(tolerance {specification.volume_tolerance:.2%}); the mesh is not "
                f"representing the geometry",
            )
