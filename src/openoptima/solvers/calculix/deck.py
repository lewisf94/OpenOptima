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

from ...domain.carried import mass_group
from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.model import AnalysisModel, ConstraintKind, LoadKind, PointMass
from ...domain.orthotropic import OrthotropicMaterial, local_axes
from ...meshing.base import MeshData
from .loads import (
    FACE_FLATNESS_LIMIT_DEG,
    build_element_centroids,
    build_face_lookup,
    consistent_nodal_forces,
    face_frame,
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

    # -- carried items with a size -----------------------------------------
    # Worked out before anything is written, because their lumps need nodes of
    # their own and those have to go into the mesh file with the rest.
    sized = [m for m in model.point_masses if m.has_size]
    attachments: list[CarriedAttachment] = []
    carried_lumps: dict[str, list[tuple[int, float]]] = {}
    if sized:
        element_centroids = build_element_centroids(mesh.connectivity, coordinates_by_tag)
        next_node = int(max(mesh.node_tags, default=0)) + 1
        for point_mass in sized:
            attachment, lumps = _carried_geometry(
                point_mass, mesh, coordinates_by_tag, element_centroids, next_node
            )
            attachments.append(attachment)
            carried_lumps[point_mass.name] = lumps
            next_node = attachment.rotation_node + 1

    # -- mesh --------------------------------------------------------------
    with mesh_file.open("w", encoding="ascii") as handle:
        handle.write("*NODE, NSET=Nall\n")
        for tag, point in zip(mesh.node_tags, mesh.coordinates, strict=True):
            handle.write(f"{int(tag)}, {point[0]:.9g}, {point[1]:.9g}, {point[2]:.9g}\n")
        handle.write(f"*ELEMENT, TYPE={mesh.element_type}, ELSET=Eall\n")
        for tag, row in zip(mesh.element_tags, mesh.connectivity, strict=True):
            nodes = ", ".join(str(int(n)) for n in row)
            handle.write(f"{int(tag)}, {nodes}\n")
        for attachment in attachments:
            handle.write(f"*NODE, NSET={_carried_set_name(attachment.name)}\n")
            for tag, x, y, z in attachment.nodes:
                handle.write(f"{tag}, {x:.9g}, {y:.9g}, {z:.9g}\n")
            # The rotation node is placed at the item's middle. Where it sits
            # does not change the physics -- it only carries the rigid body's
            # rotations -- but putting it anywhere else makes the deck read as
            # though it did.
            handle.write(
                f"{attachment.rotation_node}, {attachment.centre[0]:.9g}, "
                f"{attachment.centre[1]:.9g}, {attachment.centre[2]:.9g}\n"
            )

    # -- node sets ---------------------------------------------------------
    with sets_file.open("w", encoding="ascii") as handle:
        for name, tags in sorted(mesh.surface_nodes.items()):
            _write_set(handle, _set_name(name), tags)

    # -- material ----------------------------------------------------------
    material = model.material
    with material_file.open("w", encoding="ascii") as handle:
        _write_material(handle, material)
        # Element tags for the mass elements start above every real element,
        # so they cannot collide with the mesh whatever the mesher produced.
        mass_sets = _write_point_masses(
            handle,
            model.point_masses,
            mesh,
            first_element_tag=int(max(mesh.element_tags, default=0)) + 1,
            carried_lumps=carried_lumps,
        )

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
        _write_rigid_bodies(handle, tuple(attachments))

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
                    _write_gravity(handle, magnitude, axis, mass_sets)
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
                    mass_sets,
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
    mass_sets: list[str],
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
        # Scaled like every other load in this step, so the eigenvalues stay
        # exactly proportional and dividing back afterwards remains exact.
        _write_gravity(handle, magnitude * scale, direction, mass_sets)

    handle.write("*END STEP\n")


def _mass_set_name(name: str) -> str:
    return f"EM_{_safe(name)}".upper()


@dataclass(frozen=True)
class CarriedAttachment:
    """A sized carried item, placed where it really sits.

    Its lumps live on nodes of their own, off the surface of the part, so they
    have to be tied to the face before they mean anything. That tie is written
    into the main deck file rather than here, because ``*RIGID BODY`` is a
    model-level card.
    """

    name: str
    region: str
    #: New nodes to append to the mesh: ``(tag, x, y, z)``.
    nodes: tuple[tuple[int, float, float, float], ...]
    centre: tuple[float, float, float]
    outward: tuple[float, float, float]
    #: Node carrying the rigid body's rotations. Massless by construction: its
    #: rotational freedoms get their inertia from the lumps, which is the only
    #: way CalculiX 2.21 can supply one.
    rotation_node: int


def _carried_geometry(
    point_mass: PointMass,
    mesh: MeshData,
    coordinates_by_tag: dict[int, np.ndarray],
    element_centroids: dict[tuple[int, ...], np.ndarray],
    first_node_tag: int,
) -> tuple[CarriedAttachment, list[tuple[int, float]]]:
    """Work out where a sized item's lumps go, in world coordinates.

    Returns the attachment and ``[(node_tag, mass)]`` for the lumps.
    """
    size = point_mass.size
    assert size is not None  # only called for sized items

    triangles = mesh.surface_triangles.get(point_mass.region)
    if triangles is None or len(triangles) == 0:
        raise EvaluationFailure(
            FailureCode.REGION_NOT_FOUND,
            f"Carried item {point_mass.name!r} is attached to region "
            f"{point_mass.region!r}, which has no surface elements, so there is "
            f"nothing to stand it off from.",
            detail={"point_mass": point_mass.name, "region": point_mass.region},
        )

    try:
        centre, outward, worst_deg = face_frame(triangles, coordinates_by_tag, element_centroids)
    except ValueError as exc:
        raise EvaluationFailure(FailureCode.INTERNAL_ERROR, str(exc)) from exc

    if worst_deg > FACE_FLATNESS_LIMIT_DEG:
        raise EvaluationFailure(
            FailureCode.CARRIED_MASS_UNPLACEABLE,
            f"Carried item {point_mass.name!r} has a size, so it has to stand off "
            f"region {point_mass.region!r} in some direction -- but that region is "
            f"not flat. Parts of it point up to {worst_deg:.1f} degrees apart, and "
            f"there is no single direction that is 'up' off a curved or folded "
            f"face. Either attach it to a flat face, or remove its size and accept "
            f"a natural frequency that reads high.",
            detail={
                "point_mass": point_mass.name,
                "region": point_mass.region,
                "spread_deg": round(worst_deg, 3),
                "limit_deg": FACE_FLATNESS_LIMIT_DEG,
            },
        )

    # Two directions in the plane of the face. Which two does not matter for a
    # cylinder and matters only by naming for a box, so they are chosen the
    # same deterministic way the print axes are: a rebuilt project has to
    # produce a byte-identical deck or its cached results stop being valid.
    across, deep = local_axes((float(outward[0]), float(outward[1]), float(outward[2])))
    axes = (np.asarray(across), np.asarray(deep), np.asarray(outward))
    origin = np.asarray(centre) + np.asarray(outward) * size.effective_centre_height

    nodes: list[tuple[int, float, float, float]] = []
    lumps: list[tuple[int, float]] = []
    tag = first_node_tag
    for lump in mass_group(point_mass.mass, size):
        position = origin + axes[0] * lump.across + axes[1] * lump.deep + axes[2] * lump.out
        nodes.append((tag, float(position[0]), float(position[1]), float(position[2])))
        lumps.append((tag, lump.mass))
        tag += 1

    attachment = CarriedAttachment(
        name=point_mass.name,
        region=point_mass.region,
        nodes=tuple(nodes),
        centre=(float(origin[0]), float(origin[1]), float(origin[2])),
        outward=(float(outward[0]), float(outward[1]), float(outward[2])),
        rotation_node=tag,
    )
    return attachment, lumps


def _write_point_masses(
    handle,
    point_masses: tuple[PointMass, ...],
    mesh: MeshData,
    first_element_tag: int,
    carried_lumps: dict[str, list[tuple[int, float]]],
) -> list[str]:
    """``MASS`` elements for everything the part carries.

    Returns the element set names written, so the gravity loads can name them.

    **An item with no size is spread evenly between the face's nodes, and the
    even split is deliberate rather than lazy.** The obvious alternative is
    the consistent split used for a surface *load*, which integrates the
    element shape functions -- see the deck's ``*CLOAD`` block and trap 5 in
    ``AGENTS.md``. That is right for a load and wrong here: the exact integral
    of a corner shape function over a flat 6-node triangle is **zero**, and it
    is negative for some other element types. Applied to a load that is
    correct and measurable. Applied to a mass it would put zero mass on every
    corner node and a negative mass on some, and a negative mass is not a
    conservative approximation of anything -- it is a term that makes an
    eigenvalue solve return numbers with no physical meaning at all.

    So the split there is by node count. The total is exact, which is what
    sets the frequency; only its distribution across one small face is
    approximated.

    **An item with a size gets lumps on nodes of its own instead**, already
    positioned by :func:`_carried_geometry`, so its middle stands off the face
    and it resists being turned.
    """
    written: list[str] = []
    tag = first_element_tag
    for point_mass in point_masses:
        set_name = _mass_set_name(point_mass.name)
        placed = carried_lumps.get(point_mass.name)

        if placed is None:
            nodes = sorted(int(n) for n in mesh.surface_nodes.get(point_mass.region, ()))
            if not nodes:
                raise EvaluationFailure(
                    FailureCode.REGION_NOT_FOUND,
                    f"Point mass {point_mass.name!r} is attached to region "
                    f"{point_mass.region!r}, and no mesh nodes were found on it, so "
                    f"there is nothing to attach {point_mass.mass_kg:g} kg to.",
                    detail={"point_mass": point_mass.name, "region": point_mass.region},
                )
            handle.write(f"\n** point mass: {point_mass.name} ({point_mass.mass_kg:g} kg)\n")
            handle.write(f"*ELEMENT, TYPE=MASS, ELSET={set_name}\n")
            for node in nodes:
                handle.write(f"{tag}, {node}\n")
                tag += 1
            handle.write(f"*MASS, ELSET={set_name}\n{point_mass.mass / len(nodes):.9g}\n")
            written.append(set_name)
            continue

        # A sized item. Each lump weighs something different, so each needs its
        # own element set -- one *MASS card carries one value.
        size = point_mass.size
        assert size is not None
        handle.write(
            f"\n** carried: {point_mass.name} ({point_mass.mass_kg:g} kg, "
            f"{size.shape.value}, middle {size.effective_centre_height:g} mm off "
            f"{point_mass.region})\n"
        )
        for index, (node, mass) in enumerate(placed):
            lump_set = f"{set_name}_{index}"
            handle.write(f"*ELEMENT, TYPE=MASS, ELSET={lump_set}\n{tag}, {node}\n")
            handle.write(f"*MASS, ELSET={lump_set}\n{mass:.9g}\n")
            written.append(lump_set)
            tag += 1
    return written


def _write_rigid_bodies(handle, attachments: tuple[CarriedAttachment, ...]) -> None:
    """Tie each sized item to the face it bolts to.

    **A rigid tie, and not a distributing coupling.** The gentler-looking
    option turns out to be silently wrong for this: measured on
    ``examples/drone_arm`` with the identical mass at the identical place, a
    ``*DISTRIBUTING COUPLING`` gave 170.293 Hz against 166.572 Hz for the rigid
    tie -- within 0.02 Hz of the answer for an item with **no height at all**.
    It carries the force and not the moment arm, with exit code 0 and nothing
    in the log.

    The tie does make the mounting face rigid, which is a real modelling
    change. Measured on the same part it moves the first mode by +0.29% and
    the stress not at all: 4.4517 against 4.4512 MPa at the 99th percentile
    under the landing case, on a face that carries the load as well as the
    motor.
    """
    for attachment in attachments:
        handle.write(f"\n** {attachment.name} is bolted to {attachment.region}\n")
        handle.write(f"*NSET, NSET=NRB_{_safe(attachment.name).upper()}\n")
        handle.write(f"{_set_name(attachment.region)},\n")
        handle.write(f"{_carried_set_name(attachment.name)},\n")
        handle.write(
            f"*RIGID BODY, NSET=NRB_{_safe(attachment.name).upper()}, "
            f"REF NODE={attachment.nodes[0][0]}, ROT NODE={attachment.rotation_node}\n"
        )


def _carried_set_name(name: str) -> str:
    return f"NC_{_safe(name)}".upper()


def _write_gravity(handle, magnitude: float, axis, mass_sets: list[str]) -> None:
    """Gravity on the part, and on everything it carries.

    **A ``MASS`` element is not in ``Eall``, so it has no weight unless it is
    named.** Measured on a 100 mm steel cantilever carrying 0.2 kg: gravity on
    ``Eall`` alone gives a reaction of 0.3843 N, the beam's own weight, with
    the carried mass contributing nothing and nothing in the solver output to
    say so. Naming both sets gives 2.3463 N against 2.3470 N by hand.

    That silence is the whole reason this function exists rather than a bare
    ``Eall`` line: a part sized for an acceleration case would have been sized
    without the thing it is carrying.
    """
    for element_set in ("Eall", *mass_sets):
        handle.write(
            f"{element_set}, GRAV, {magnitude:.9g}, {axis[0]:.9g}, {axis[1]:.9g}, {axis[2]:.9g}\n"
        )


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
