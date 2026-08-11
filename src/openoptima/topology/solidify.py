"""Turning a topology result into a shape that can be analysed again.

What ``beso`` produces is not a part. It is the subset of the starting mesh
whose elements survived, which means a **blocky, staircase surface** following
element boundaries. Nobody would manufacture that, and analysing it would
report stress concentrations at every step that exist only because of how the
mesh was drawn.

This module extracts the outside surface of that block, smooths the staircase
away, and checks the result is a single sealed solid. It is the part of
topology optimisation worth building here: the optimisation itself is
borrowed, and this is where most open-source topology tools stop being useful.

**Smoothing removes material, and material is strength.** That is not a detail
to hide. The volume before and after is measured and reported, and a large loss
is said out loud. The smoothed shape is a *proposal* -- it has to go back
through the ordinary evaluation pipeline on a body-fitted mesh before any
number about it means anything. Nothing here may be reported as a result.

Two measurements decided how this is done, both on a real 600-element
cantilever result:

- **Laplacian smoothing is unusable.** Unconstrained it lost 52 per cent of the
  volume in five passes and 92 per cent in twenty -- it dissolves the part.
  Volume-constrained, it moved vertices by 16 mm and then 72 mm on a part 20 mm
  deep, and the surface area *grew*: diverging, not smoothing.
- **Taubin smoothing works, but only with an even number of passes.** It
  alternates a shrinking pass with an inflating one, so an odd count stops half
  way through the cycle. Measured: one pass loses 14.0 per cent of the volume,
  two passes lose 0.8 per cent, three lose 14.9 per cent, four lose 1.8 per
  cent. Anybody choosing five would quietly lose a sixth of the material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..domain.failures import EvaluationFailure, FailureCode

#: Corner nodes of each face, per element type. Only corners are used: a curved
#: element's midside nodes describe the same face, and the surface is
#: triangulated flat before it is smoothed anyway.
#:
#: Second-order types map onto their first-order parents because CalculiX
#: numbers the corners first in both.
_TET_FACES = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3))
_HEX_FACES = (
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)

ELEMENT_FACES: dict[str, tuple[tuple[int, ...], ...]] = {
    "C3D4": _TET_FACES,
    "C3D10": _TET_FACES,
    "C3D8": _HEX_FACES,
    "C3D8R": _HEX_FACES,
    "C3D8I": _HEX_FACES,
    "C3D20": _HEX_FACES,
    "C3D20R": _HEX_FACES,
}

#: Passes of Taubin smoothing. **Must be even** -- see the module docstring.
#: Six is enough to take the staircase off while costing about 3 per cent of
#: the volume; more passes smooth further and cost more material.
DEFAULT_SMOOTHING_PASSES = 6

#: How much volume may be lost to smoothing before it is called out. Not an
#: error: the shape is re-analysed afterwards, so a loss shows up honestly as a
#: worse result rather than a hidden one. But it must not pass in silence.
VOLUME_LOSS_WARNING = 0.05


@dataclass(frozen=True)
class SolidResult:
    """A smoothed surface, and everything about it worth knowing.

    **This is a proposal, not a result.** It has not been analysed. Anything
    reported about it has to come from putting it back through the ordinary
    evaluation pipeline on a body-fitted mesh.
    """

    vertices: np.ndarray  # (N, 3) mm
    faces: np.ndarray  # (M, 3) triangle indices
    volume_before_smoothing_mm3: float
    volume_mm3: float
    watertight: bool
    body_count: int
    smoothing_passes: int
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def volume_change(self) -> float:
        """Share of the volume smoothing removed. Negative means material lost."""
        if self.volume_before_smoothing_mm3 <= 0:
            return 0.0
        return (
            self.volume_mm3 - self.volume_before_smoothing_mm3
        ) / self.volume_before_smoothing_mm3

    def write_stl(self, path: Path) -> Path:
        """Write the surface out so it can be re-meshed and re-analysed."""
        mesh = _trimesh_from(self.vertices, self.faces)
        path.parent.mkdir(parents=True, exist_ok=True)
        mesh.export(path)
        return path


def _trimesh():
    """Import trimesh only when it is needed.

    Topology optimisation is optional, and a user who never runs it should not
    have to install its dependencies to start the application.
    """
    try:
        import trimesh
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise EvaluationFailure(
            FailureCode.SOLVER_NOT_FOUND,
            "turning a topology result into a solid needs the 'trimesh' "
            "package, and it is not installed. Install it with "
            "'pip install trimesh'.",
        ) from exc
    return trimesh


def _trimesh_from(vertices: np.ndarray, faces: np.ndarray):
    return _trimesh().Trimesh(vertices=vertices, faces=faces, process=False)


def read_element_mesh(path: Path) -> tuple[dict[int, tuple[float, float, float]], list, str]:
    """Read the nodes and elements out of a CalculiX ``.inp``.

    Deliberately narrow: this reads what beso writes, which is one node block
    and one element block. It is not a general reader for the format.
    """
    nodes: dict[int, tuple[float, float, float]] = {}
    elements: list[tuple[int, ...]] = []
    element_type = ""
    section = ""

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper = line.upper()
            if upper.startswith("*NODE"):
                section = "node"
            elif upper.startswith("*ELEMENT"):
                section = "element"
                match = re.search(r"TYPE\s*=\s*([A-Z0-9]+)", upper)
                element_type = match.group(1) if match else ""
            else:
                section = ""
            continue

        parts = [p for p in re.split(r"[,\s]+", line) if p]
        if section == "node" and len(parts) >= 4:
            nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif section == "element" and len(parts) >= 2:
            elements.append(tuple(int(p) for p in parts[1:]))

    if not nodes or not elements:
        raise EvaluationFailure(
            FailureCode.RESULT_PARSE_FAILED,
            f"{path.name} does not contain both nodes and elements, so no shape "
            f"can be built from it.",
        )
    if element_type not in ELEMENT_FACES:
        raise EvaluationFailure(
            FailureCode.RESULT_PARSE_FAILED,
            f"{path.name} uses element type {element_type!r}, which this "
            f"conversion does not handle. Supported: "
            f"{', '.join(sorted(ELEMENT_FACES))}.",
        )
    return nodes, elements, element_type


def boundary_surface(
    nodes: dict[int, tuple[float, float, float]],
    elements: list,
    element_type: str,
) -> tuple[np.ndarray, np.ndarray]:
    """The outside skin of a block of elements, as triangles.

    A face on the outside belongs to exactly one element; a face inside belongs
    to two, and cancels. Counting them is all it takes, and it works whatever
    shape the block is -- including one with holes through it, which a topology
    result usually has.
    """
    face_pattern = ELEMENT_FACES[element_type]
    seen: dict[tuple[int, ...], list[tuple[int, ...]]] = {}

    for element in elements:
        for pattern in face_pattern:
            try:
                face = tuple(element[i] for i in pattern)
            except IndexError as exc:
                raise EvaluationFailure(
                    FailureCode.RESULT_PARSE_FAILED,
                    f"an element has fewer nodes than {element_type} requires",
                ) from exc
            seen.setdefault(tuple(sorted(face)), []).append(face)

    outside = [faces[0] for faces in seen.values() if len(faces) == 1]
    if not outside:
        raise EvaluationFailure(
            FailureCode.RESULT_PARSE_FAILED,
            "the topology result has no outside surface, which should be "
            "impossible. Nothing can be built from it.",
        )

    used = sorted({tag for face in outside for tag in face})
    position = {tag: index for index, tag in enumerate(used)}
    vertices = np.array([nodes[tag] for tag in used], dtype=np.float64)

    triangles: list[list[int]] = []
    for face in outside:
        indices = [position[tag] for tag in face]
        if len(indices) == 3:
            triangles.append(indices)
        else:  # a quad, split along one diagonal
            a, b, c, d = indices
            triangles.append([a, b, c])
            triangles.append([a, c, d])

    return vertices, np.array(triangles, dtype=np.int64)


def smooth(vertices: np.ndarray, faces: np.ndarray, passes: int) -> tuple[np.ndarray, int]:
    """Take the staircase off, using Taubin smoothing.

    ``passes`` is **rounded up to an even number**, because Taubin alternates a
    shrinking pass with an inflating one and stopping on an odd pass leaves the
    shape shrunk. Measured on a real result: one pass costs 14.0 per cent of the
    volume, two cost 0.8 per cent. That is not a subtlety anyone should have to
    know about, so it is enforced rather than documented.
    """
    if passes < 0:
        raise ValueError("passes cannot be negative")
    even_passes = passes + (passes % 2)
    if even_passes == 0:
        return vertices.copy(), 0

    trimesh = _trimesh()
    mesh = trimesh.Trimesh(vertices=vertices.copy(), faces=faces, process=False)
    trimesh.smoothing.filter_taubin(mesh, iterations=even_passes)
    return np.asarray(mesh.vertices, dtype=np.float64), even_passes


def to_solid(
    mesh_path: Path,
    *,
    smoothing_passes: int = DEFAULT_SMOOTHING_PASSES,
) -> SolidResult:
    """Turn one of beso's result meshes into a smoothed, sealed surface.

    Raises rather than returns when the shape cannot be used at all -- a
    surface with holes in it, or one that came out as separate pieces. Both are
    real outcomes of a topology run that removed too much, and neither can be
    analysed or made.

    **The minimum feature size is not checked here, and this does not pretend
    to.** It is enforced during the optimisation, by the smoothing filter:
    material is blurred over the filter radius, so a feature narrower than
    twice that radius cannot survive. Checking it again afterwards would need
    the thinnest width anywhere inside the shape, which is real work and not
    something a bounding box can answer. An earlier attempt using the smallest
    side of the bounding box was removed because it fires on every thin plate,
    where that number is the plate thickness the user chose -- and a warning
    that cries wolf is worse than none.
    """
    trimesh = _trimesh()
    nodes, elements, element_type = read_element_mesh(mesh_path)
    vertices, faces = boundary_surface(nodes, elements, element_type)

    blocky = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    blocky.fix_normals()
    volume_before = float(blocky.volume)

    if not blocky.is_watertight:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            "the shape the optimiser produced is not sealed, so it is not a "
            "solid and cannot be analysed or manufactured. This usually means "
            "too much material was removed.",
        )

    smoothed, used_passes = smooth(
        np.asarray(blocky.vertices), np.asarray(blocky.faces), smoothing_passes
    )
    final = trimesh.Trimesh(vertices=smoothed, faces=np.asarray(blocky.faces), process=False)
    final.fix_normals()

    warnings: list[str] = []
    if used_passes != smoothing_passes:
        warnings.append(
            f"Smoothing used {used_passes} passes rather than {smoothing_passes}. "
            f"An odd number leaves the shape shrunk, which would have thrown "
            f"away material and therefore strength."
        )

    body_count = int(final.body_count)
    if body_count > 1:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            f"the optimiser produced {body_count} separate pieces rather than "
            f"one part. Material was removed until the load path came apart. "
            f"Keep more material, or lower the removal rate.",
        )

    volume = float(final.volume)
    change = (volume - volume_before) / volume_before if volume_before > 0 else 0.0
    if change < -VOLUME_LOSS_WARNING:
        warnings.append(
            f"Smoothing removed {abs(change):.1%} of the material. The smoothed "
            f"shape is what must be analysed, not the blocky one -- it is that "
            f"much weaker than the optimiser's own result suggests."
        )

    if not final.is_watertight:
        raise EvaluationFailure(
            FailureCode.INVALID_SOLID,
            "smoothing left the shape unsealed, so it is not a solid. Try fewer smoothing passes.",
        )

    return SolidResult(
        vertices=np.asarray(final.vertices, dtype=np.float64),
        faces=np.asarray(final.faces, dtype=np.int64),
        volume_before_smoothing_mm3=volume_before,
        volume_mm3=volume,
        watertight=True,
        body_count=body_count,
        smoothing_passes=used_passes,
        warnings=tuple(warnings),
    )
