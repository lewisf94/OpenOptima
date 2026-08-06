"""CalculiX input deck generation.

The deck is split across several ``*INCLUDE``d files rather than written as one
blob, because when something goes wrong the first question is always "did the
mesh, the material or the loads go in wrong?" and separate files answer it
immediately.

Every load case becomes an independent ``*STEP`` with ``OP=NEW`` on the loads
and boundary conditions, so cases do not accumulate on top of each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.model import AnalysisModel, ConstraintKind, LoadKind
from ...meshing.base import MeshData
from .loads import (
    build_face_lookup,
    consistent_nodal_forces,
    surface_element_faces,
)

_ITEMS_PER_LINE = 8


@dataclass(frozen=True)
class DeckArtifact:
    job_name: str
    directory: Path
    main_file: Path
    files: tuple[Path, ...]
    #: Sum of applied force per load case, for the equilibrium cross-check.
    applied_force: dict[str, tuple[float, float, float]]

    @property
    def input_path(self) -> Path:
        return self.main_file


def _write_set(handle, name: str, tags: np.ndarray, keyword: str = "NSET") -> None:
    handle.write(f"*{keyword}, {keyword}={name}\n")
    values = [str(int(t)) for t in tags]
    for start in range(0, len(values), _ITEMS_PER_LINE):
        handle.write(", ".join(values[start : start + _ITEMS_PER_LINE]) + "\n")


def write_deck(
    model: AnalysisModel,
    mesh: MeshData,
    directory: Path,
    *,
    job_name: str = "job",
) -> DeckArtifact:
    directory.mkdir(parents=True, exist_ok=True)

    mesh_file = directory / "mesh.inp"
    sets_file = directory / "sets.inp"
    material_file = directory / "material.inp"
    main_file = directory / f"{job_name}.inp"

    coordinates_by_tag = {
        int(tag): mesh.coordinates[index] for index, tag in enumerate(mesh.node_tags)
    }

    # -- mesh --------------------------------------------------------------
    with mesh_file.open("w", encoding="ascii") as handle:
        handle.write("*NODE, NSET=Nall\n")
        for tag, point in zip(mesh.node_tags, mesh.coordinates, strict=True):
            handle.write(f"{int(tag)}, {point[0]:.9g}, {point[1]:.9g}, {point[2]:.9g}\n")
        handle.write(f"*ELEMENT, TYPE={mesh.element_type}, ELSET=Eall\n")
        for tag, row in zip(mesh.element_tags, mesh.connectivity, strict=True):
            nodes = ", ".join(str(int(n)) for n in row)
            handle.write(f"{int(tag)}, {nodes}\n")

    # -- node sets ---------------------------------------------------------
    with sets_file.open("w", encoding="ascii") as handle:
        for name, tags in sorted(mesh.surface_nodes.items()):
            _write_set(handle, _set_name(name), tags)

    # -- material ----------------------------------------------------------
    material = model.material
    with material_file.open("w", encoding="ascii") as handle:
        handle.write(f"*MATERIAL, NAME={_safe(material.name)}\n")
        handle.write("*ELASTIC\n")
        handle.write(f"{material.elastic_modulus:.9g}, {material.poisson_ratio:.9g}\n")
        handle.write("*DENSITY\n")
        handle.write(f"{material.density:.9g}\n")
        handle.write(f"*SOLID SECTION, ELSET=Eall, MATERIAL={_safe(material.name)}\n")

    # -- steps -------------------------------------------------------------
    face_lookup = build_face_lookup(mesh.element_tags, mesh.connectivity)
    applied_force: dict[str, tuple[float, float, float]] = {}

    with main_file.open("w", encoding="ascii") as handle:
        handle.write(f"** OpenOptima generated deck for {model.name}\n")
        handle.write("** Units: mm, N, MPa, t\n")
        handle.write(f"*INCLUDE, INPUT={mesh_file.name}\n")
        handle.write(f"*INCLUDE, INPUT={sets_file.name}\n")
        handle.write(f"*INCLUDE, INPUT={material_file.name}\n")

        for load_case in model.load_cases:
            total = np.zeros(3)
            handle.write(f"\n** ---- load case: {load_case.id} ----\n")
            handle.write("*STEP\n*STATIC\n")

            handle.write("*BOUNDARY, OP=NEW\n")
            for condition in load_case.boundary_conditions:
                set_name = _set_name(condition.region)
                if condition.region not in mesh.surface_nodes:
                    raise EvaluationFailure(
                        FailureCode.INTERNAL_ERROR,
                        f"boundary condition references region {condition.region!r} "
                        f"which has no mesh nodes",
                    )
                magnitude = 0.0 if condition.kind is ConstraintKind.FIXED else condition.magnitude
                for dof in condition.dofs:
                    handle.write(f"{set_name}, {dof}, {dof}, {magnitude:.9g}\n")

            concentrated: dict[int, np.ndarray] = {}
            pressures: list[tuple[int, int, float]] = []
            gravity: list[tuple[float, tuple[float, float, float]]] = []

            for load in load_case.loads:
                if load.kind is LoadKind.FORCE:
                    triangles = mesh.surface_triangles.get(load.region or "")
                    if triangles is None:
                        raise EvaluationFailure(
                            FailureCode.INTERNAL_ERROR,
                            f"force load references region {load.region!r} "
                            f"which has no surface elements",
                        )
                    try:
                        contributions = consistent_nodal_forces(
                            triangles, coordinates_by_tag, load.vector
                        )
                    except ValueError as exc:
                        raise EvaluationFailure(FailureCode.INTERNAL_ERROR, str(exc)) from exc
                    for tag, vector in contributions.items():
                        concentrated[tag] = concentrated.get(tag, np.zeros(3)) + vector
                    total += np.array(load.vector, dtype=float)

                elif load.kind is LoadKind.PRESSURE:
                    triangles = mesh.surface_triangles.get(load.region or "")
                    if triangles is None:
                        raise EvaluationFailure(
                            FailureCode.INTERNAL_ERROR,
                            f"pressure load references region {load.region!r} "
                            f"which has no surface elements",
                        )
                    try:
                        faces = surface_element_faces(triangles, face_lookup)
                    except ValueError as exc:
                        raise EvaluationFailure(FailureCode.INTERNAL_ERROR, str(exc)) from exc
                    pressures.extend((element, face, load.magnitude) for element, face in faces)

                elif load.kind is LoadKind.ACCELERATION:
                    vector = np.array(load.vector, dtype=float)
                    magnitude = float(np.linalg.norm(vector))
                    if magnitude > 0:
                        direction = vector / magnitude
                        gravity.append((magnitude, (direction[0], direction[1], direction[2])))

            if concentrated:
                handle.write("*CLOAD, OP=NEW\n")
                for tag in sorted(concentrated):
                    vector = concentrated[tag]
                    for dof in (1, 2, 3):
                        component = float(vector[dof - 1])
                        if abs(component) > 0.0:
                            handle.write(f"{tag}, {dof}, {component:.9g}\n")
            else:
                handle.write("*CLOAD, OP=NEW\n")

            if pressures or gravity:
                handle.write("*DLOAD, OP=NEW\n")
                for element, face, magnitude in pressures:
                    handle.write(f"{element}, P{face}, {magnitude:.9g}\n")
                for magnitude, direction in gravity:
                    handle.write(
                        f"Eall, GRAV, {magnitude:.9g}, "
                        f"{direction[0]:.9g}, {direction[1]:.9g}, {direction[2]:.9g}\n"
                    )
            else:
                handle.write("*DLOAD, OP=NEW\n")

            handle.write("*NODE FILE\nU\n")
            handle.write("*EL FILE\nS\n")
            for condition in load_case.boundary_conditions:
                handle.write(f"*NODE PRINT, NSET={_set_name(condition.region)}, TOTALS=ONLY\nRF\n")
            handle.write("*END STEP\n")

            applied_force[load_case.id] = (float(total[0]), float(total[1]), float(total[2]))

    return DeckArtifact(
        job_name=job_name,
        directory=directory,
        main_file=main_file,
        files=(main_file, mesh_file, sets_file, material_file),
        applied_force=applied_force,
    )


def _safe(name: str) -> str:
    """CalculiX names cannot contain spaces or commas."""
    return "".join(character if character.isalnum() else "_" for character in name)[:70]


def _set_name(region: str) -> str:
    return f"R_{_safe(region)}".upper()
