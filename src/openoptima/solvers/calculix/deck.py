"""CalculiX input deck generation.

The deck is split across several ``*INCLUDE``d files rather than written as one
blob, because when something goes wrong the first question is always "did the
mesh, the material or the loads go in wrong?" and separate files answer it
immediately.

Every load case becomes an independent ``*STEP`` with ``OP=NEW`` on the loads
and boundary conditions, so cases do not accumulate on top of each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.model import AnalysisModel, ConstraintKind, LoadKind
from ...domain.orthotropic import OrthotropicMaterial, local_axes
from ...meshing.base import MeshData
from .loads import (
    build_face_lookup,
    consistent_nodal_forces,
    surface_element_faces,
)

_ITEMS_PER_LINE = 8

#: The ``*BUCKLE`` step is written with every load divided by this constant,
#: and the factors CalculiX returns are divided by it again to recover the
#: factor against the load the user applied.
#:
#: CalculiX skips the lowest buckling mode when the true factor falls below
#: about 0.52, returning the second mode instead -- about nine times too high,
#: in the unsafe direction, with nothing in its output to say so. Scaling the
#: reference load down moves every factor up by the same constant, exactly,
#: because the stress stiffness matrix is linear in the load. A part folding
#: under a thousandth of its applied load still reports 1.0 here, which is
#: comfortably clear of the threshold.
#:
#: 1000 is chosen to leave that much headroom while staying far from the range
#: where the eigenvalues themselves would lose precision.
BUCKLING_LOAD_SCALE = 1000.0


@dataclass(frozen=True)
class DeckArtifact:
    job_name: str
    directory: Path
    main_file: Path
    files: tuple[Path, ...]
    #: Sum of applied force per load case, for the equilibrium cross-check.
    applied_force: dict[str, tuple[float, float, float]]
    #: 1-based step number of the ``*FREQUENCY`` step that answers for each
    #: load case, when modal analysis is on. Several load cases share one step
    #: whenever they hold the part the same way, because a natural frequency
    #: does not depend on the load. Empty when modal analysis is off.
    frequency_step: dict[str, int] = field(default_factory=dict)

    @property
    def input_path(self) -> Path:
        return self.main_file


def _support_signature(load_case) -> tuple:
    """What holds this part still, as a comparable key.

    A natural frequency comes from stiffness and mass alone, so two load cases
    with identical supports have identical frequencies -- exactly, not
    approximately. Solving once per distinct set of supports rather than once
    per load case is therefore free of any loss, and a project with four load
    cases on one set of supports pays for one frequency solve instead of four.
    """
    return tuple(
        sorted(
            (
                condition.region,
                condition.kind.value,
                tuple(condition.dofs),
                0.0 if condition.kind is ConstraintKind.FIXED else condition.magnitude,
            )
            for condition in load_case.boundary_conditions
        )
    )


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
        _write_material(handle, material)

    # -- steps -------------------------------------------------------------
    face_lookup = build_face_lookup(mesh.element_tags, mesh.connectivity)
    applied_force: dict[str, tuple[float, float, float]] = {}
    frequency_step: dict[str, int] = {}
    step_number = 0

    with main_file.open("w", encoding="ascii") as handle:
        handle.write(f"** OpenOptima generated deck for {model.name}\n")
        handle.write("** Units: mm, N, MPa, t\n")
        handle.write(f"*INCLUDE, INPUT={mesh_file.name}\n")
        handle.write(f"*INCLUDE, INPUT={sets_file.name}\n")
        handle.write(f"*INCLUDE, INPUT={material_file.name}\n")

        for load_case in model.load_cases:
            total = np.zeros(3)
            step_number += 1
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
                # Named `axis` rather than `direction`: the same name is bound
                # to a numpy array earlier in this function, and reusing it
                # here made the type ambiguous.
                for magnitude, axis in gravity:
                    handle.write(
                        f"Eall, GRAV, {magnitude:.9g}, "
                        f"{axis[0]:.9g}, {axis[1]:.9g}, {axis[2]:.9g}\n"
                    )
            else:
                handle.write("*DLOAD, OP=NEW\n")

            handle.write("*NODE FILE\nU\n")
            handle.write("*EL FILE\nS\n")
            # ELSE is the internal energy already integrated over each element's
            # volume. ENER is the energy *density* at each integration point,
            # which TOTALS=ONLY does not sum -- it prints one row per point, and
            # turning that into a total needs the Jacobian weights the .dat does
            # not carry. ELSE gives the number directly.
            handle.write("*EL PRINT, ELSET=Eall, TOTALS=ONLY\nELSE\n")
            for condition in load_case.boundary_conditions:
                handle.write(f"*NODE PRINT, NSET={_set_name(condition.region)}, TOTALS=ONLY\nRF\n")
            handle.write("*END STEP\n")

            applied_force[load_case.id] = (float(total[0]), float(total[1]), float(total[2]))

            if model.buckling.enabled:
                step_number += 1
                _write_buckling_step(
                    handle,
                    load_case,
                    model.buckling.modes,
                    concentrated,
                    pressures,
                    gravity,
                )

        # Frequency steps go last, after every load case, so that adding modal
        # analysis to a project cannot shift the step number of any static or
        # buckling step. Results are selected by step number, so a shift would
        # silently pair a load case with another one's numbers.
        if model.modal.enabled:
            seen: dict[tuple, int] = {}
            for load_case in model.load_cases:
                signature = _support_signature(load_case)
                if signature not in seen:
                    step_number += 1
                    seen[signature] = step_number
                    _write_frequency_step(handle, load_case, model.modal.modes)
                frequency_step[load_case.id] = seen[signature]

    return DeckArtifact(
        job_name=job_name,
        directory=directory,
        main_file=main_file,
        files=(main_file, mesh_file, sets_file, material_file),
        applied_force=applied_force,
        frequency_step=frequency_step,
    )


def _write_frequency_step(handle, load_case, modes: int) -> None:
    """A ``*FREQUENCY`` step carrying this load case's supports and no loads.

    **No loads, deliberately.** A natural frequency comes from stiffness and
    mass. What is pushing on the part does not enter the eigenvalue problem at
    all, so applying the load here would be noise at best. (A load *can* shift
    a frequency by stiffening or slackening the part -- a tightened guitar
    string rises in pitch -- but that is a separate analysis, needs a
    ``PERTURBATION`` step on top of a static one, and is not what this reports.
    See ``docs/engineering-assumptions.md``.)

    **The empty output requests are not decoration; they are the whole reason
    this step is safe.** CalculiX carries a ``*NODE FILE``/``*EL FILE`` request
    forward from the step that made it, so a frequency step following a static
    one writes a full mode shape -- DISP, STRESS and ERROR -- into the FRD for
    every mode, without being asked. ``frd.py`` reads results by block order,
    on the rule that the n-th DISP block is the n-th solved step. Eighteen
    unexpected blocks would break that rule silently, and a mode shape is a
    displacement field that looks exactly like a real deflection, only scaled
    arbitrarily. The reader would not notice, and neither would anyone else.

    Measured on the cantilever probe: 21 blocks and 46 413 lines of FRD with
    the requests carried forward, 3 blocks and 10 131 lines with them cleared,
    and the frequencies identical to every digit either way. So this costs
    nothing and removes a whole class of silent misreading. ``frd.py`` also
    ignores anything marked ``MODAL`` now, which is the belt to this braces.
    """
    handle.write(f"\n** ---- frequency: supports of {load_case.id} ----\n")
    handle.write(f"*STEP\n*FREQUENCY\n{modes}\n")

    handle.write("*BOUNDARY, OP=NEW\n")
    for condition in load_case.boundary_conditions:
        set_name = _set_name(condition.region)
        magnitude = 0.0 if condition.kind is ConstraintKind.FIXED else condition.magnitude
        for dof in condition.dofs:
            handle.write(f"{set_name}, {dof}, {dof}, {magnitude:.9g}\n")

    # An output request with no entities replaces the one carried forward.
    handle.write("*NODE FILE\n\n*EL FILE\n\n")
    handle.write("*END STEP\n")


def _write_buckling_step(
    handle,
    load_case,
    modes: int,
    concentrated: dict[int, np.ndarray],
    pressures: list[tuple[int, int, float]],
    gravity: list[tuple[float, tuple[float, float, float]]],
) -> None:
    """A ``*BUCKLE`` step carrying the static step's loads, scaled down.

    The eigenvalues CalculiX returns are multiples of *this* step's load, so
    the caller must divide them by :data:`BUCKLING_LOAD_SCALE` to recover the
    factor against the load the user actually applied.

    **Why the load is scaled at all.** CalculiX silently skips the lowest
    buckling mode when the true factor against the applied load falls below
    about 0.52, and returns the second mode instead -- roughly nine times too
    high, in the unsafe direction, with nothing in the output to say so. It was
    measured at exactly the same threshold on three different columns, at
    slenderness 69 and 277 alike, so it is a property of the eigenvalue solve
    and not of the geometry. Asking for more modes does not help: at twenty
    modes the true one is still absent.

    The buckling eigenvalue scales exactly inversely with the reference load --
    the stress stiffness matrix is linear in it -- so solving against a load a
    thousand times smaller multiplies every factor by a thousand and changes
    nothing else. A design whose true factor is 0.001, folding under a
    thousandth of its load, still comes back at 1.0 here: far above the
    threshold. Dividing by the same constant afterwards is exact.

    This is why the scale is applied here rather than by re-solving. It costs
    nothing, and it makes the answer right instead of merely refusing it.

    Deliberately no ``*NODE FILE``: requesting mode shapes writes one extra
    displacement block per mode into the FRD, which would shift the block
    indices the static results are read from and silently mis-attribute results
    to the wrong load case. Mode shapes are also large, and a 500-design study
    does not want them. The deck is kept in the run directory, so re-running one
    design with mode-shape output is easy when a design is being reviewed.
    """
    handle.write(f"\n** ---- buckling: {load_case.id} ----\n")
    handle.write(f"*STEP\n*BUCKLE\n{modes}\n")

    handle.write("*BOUNDARY, OP=NEW\n")
    for condition in load_case.boundary_conditions:
        set_name = _set_name(condition.region)
        magnitude = 0.0 if condition.kind is ConstraintKind.FIXED else condition.magnitude
        for dof in condition.dofs:
            handle.write(f"{set_name}, {dof}, {dof}, {magnitude:.9g}\n")

    # Every load in this step is scaled by the same constant, so the mode
    # shapes are untouched and only the reported factors move -- by exactly
    # that constant. See this function's docstring.
    scale = 1.0 / BUCKLING_LOAD_SCALE

    handle.write("*CLOAD, OP=NEW\n")
    for tag in sorted(concentrated):
        vector = concentrated[tag]
        for dof in (1, 2, 3):
            component = float(vector[dof - 1]) * scale
            if abs(component) > 0.0:
                handle.write(f"{tag}, {dof}, {component:.9g}\n")

    handle.write("*DLOAD, OP=NEW\n")
    for element, face, magnitude in pressures:
        handle.write(f"{element}, P{face}, {magnitude * scale:.9g}\n")
    for magnitude, direction in gravity:
        handle.write(
            f"Eall, GRAV, {magnitude * scale:.9g}, "
            f"{direction[0]:.9g}, {direction[1]:.9g}, {direction[2]:.9g}\n"
        )

    handle.write("*END STEP\n")


def _write_material(handle, material) -> None:
    """Write the material block, isotropic or orthotropic.

    An isotropic material produces exactly the two-number ``*ELASTIC`` block it
    always did. That matters: every verified benchmark in this project rests on
    those decks, and this feature must not move any of them.
    """
    name = _safe(material.name)
    handle.write(f"*MATERIAL, NAME={name}\n")

    orientation = None
    if isinstance(material, OrthotropicMaterial):
        # CalculiX wants stiffness, in the order D1111, D1122, D2222, D1133,
        # D2233, D3333, D1212, D1313, D2323. Engineering constants are
        # compliance, so handing them over directly would be silently wrong --
        # both are large positive numbers and the deck would look plausible.
        constants = material.stiffness_matrix()
        handle.write("*ELASTIC, TYPE=ORTHO\n")
        handle.write(", ".join(f"{value:.9g}" for value in constants[:8]) + "\n")
        handle.write(f"{constants[8]:.9g}\n")
        orientation = f"OR_{name}"[:78]
    else:
        handle.write("*ELASTIC\n")
        handle.write(f"{material.elastic_modulus:.9g}, {material.poisson_ratio:.9g}\n")

    handle.write("*DENSITY\n")
    handle.write(f"{material.density:.9g}\n")

    if orientation is not None:
        # The nine constants above are stated along the material's own axes,
        # which are not the model's. Without this the solver would apply the
        # weak through-layer direction along global z whatever direction the
        # part was actually built in.
        first, second = local_axes(material.build_direction)
        handle.write(f"*ORIENTATION, NAME={orientation}\n")
        handle.write(", ".join(f"{value:.9g}" for value in (*first, *second)) + "\n")
        handle.write(f"*SOLID SECTION, ELSET=Eall, MATERIAL={name}, ORIENTATION={orientation}\n")
    else:
        handle.write(f"*SOLID SECTION, ELSET=Eall, MATERIAL={name}\n")


def _safe(name: str) -> str:
    """CalculiX names cannot contain spaces or commas."""
    return "".join(character if character.isalnum() else "_" for character in name)[:70]


def _set_name(region: str) -> str:
    return f"R_{_safe(region)}".upper()
