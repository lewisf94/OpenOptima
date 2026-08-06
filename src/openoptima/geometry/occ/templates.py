"""Built-in parametric templates, modelled with gmsh's OpenCASCADE kernel.

A template is a pure function ``(gmsh, parameters) -> volume_tag``.  It must:

* validate its own parameters and raise ``EvaluationFailure`` with an
  **INFEASIBLE** code when the design is impossible — a zero wall, a hole that
  has burst through a face, a fillet larger than the material around it.  These
  are facts about the design and the optimiser should learn them;
* leave a single solid behind, so the mesher has something unambiguous to work
  with;
* never depend on face or edge tags surviving from one build to the next.

Templates deliberately fail *early*, before meshing, because rejecting a bad
design analytically costs microseconds and meshing it costs seconds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...domain.failures import EvaluationFailure, FailureCode

BuildFunction = Callable[[Any, dict[str, Any]], int]


@dataclass(frozen=True)
class Template:
    name: str
    build: BuildFunction
    defaults: dict[str, float] = field(default_factory=dict)
    description: str = ""
    #: Regions this template is designed to expose, for documentation and `doctor`.
    suggested_regions: tuple[str, ...] = ()


_REGISTRY: dict[str, Template] = {}


def register(template: Template) -> Template:
    _REGISTRY[template.name] = template
    return template


def get_template(name: str) -> Template:
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            f"Unknown geometry template {name!r}. Available templates: {available}",
        ) from None


def available_templates() -> list[Template]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def _infeasible(message: str, code: FailureCode = FailureCode.INVALID_SOLID) -> None:
    raise EvaluationFailure(code, message)


def _require_positive(parameters: dict[str, Any], *names: str) -> None:
    for name in names:
        value = float(parameters[name])
        if value <= 0.0:
            _infeasible(
                f"{name} must be positive, received {value:g}",
                FailureCode.INVALID_DESIGN_VARIABLES,
            )


def _merged(defaults: dict[str, float], parameters: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(parameters)
    return merged


# ---------------------------------------------------------------------------
# Cantilever box — the verification workhorse
# ---------------------------------------------------------------------------

_CANTILEVER_DEFAULTS = {"length": 200.0, "width": 20.0, "height": 20.0}


def _build_cantilever(gmsh: Any, parameters: dict[str, Any]) -> int:
    p = _merged(_CANTILEVER_DEFAULTS, parameters)
    _require_positive(p, "length", "width", "height")
    tag = gmsh.model.occ.addBox(0.0, 0.0, 0.0, p["length"], p["width"], p["height"])
    gmsh.model.occ.synchronize()
    return int(tag)


register(
    Template(
        name="cantilever_box",
        build=_build_cantilever,
        defaults=_CANTILEVER_DEFAULTS,
        description=(
            "Rectangular prism fixed at x=0. Used for the beam-theory verification "
            "benchmark; also the simplest possible smoke test."
        ),
        suggested_regions=("fixed_face", "load_face"),
    )
)


# ---------------------------------------------------------------------------
# L-bracket — the flagship example
# ---------------------------------------------------------------------------

_L_BRACKET_DEFAULTS = {
    "length": 120.0,
    "height": 90.0,
    "width": 60.0,
    "thickness_h": 10.0,
    "thickness_v": 10.0,
    "fillet_radius": 8.0,
    "bolt_diameter": 9.0,
    "bolt_inset": 15.0,
}


def _build_l_bracket(gmsh: Any, parameters: dict[str, Any]) -> int:
    p = _merged(_L_BRACKET_DEFAULTS, parameters)
    _require_positive(p, "length", "height", "width", "thickness_h", "thickness_v", "bolt_diameter")
    occ = gmsh.model.occ

    length = float(p["length"])
    height = float(p["height"])
    width = float(p["width"])
    th = float(p["thickness_h"])
    tv = float(p["thickness_v"])
    radius = float(p["fillet_radius"])
    bolt_r = 0.5 * float(p["bolt_diameter"])
    inset = float(p["bolt_inset"])

    # --- analytic feasibility, before touching the CAD kernel --------------
    if tv >= length:
        _infeasible(f"thickness_v ({tv:g}) leaves no horizontal arm (length {length:g})")
    if th >= height:
        _infeasible(f"thickness_h ({th:g}) leaves no vertical arm (height {height:g})")
    if radius < 0:
        _infeasible("fillet_radius cannot be negative", FailureCode.INVALID_DESIGN_VARIABLES)
    clearance = min(length - tv, height - th)
    if radius > 0.8 * clearance:
        _infeasible(
            f"fillet_radius {radius:g} does not fit the {clearance:g} mm of material "
            f"at the internal corner (limit {0.8 * clearance:g})",
            FailureCode.MANUFACTURING_RULE_VIOLATED,
        )
    bolt_z = height - inset
    if bolt_z - bolt_r <= th:
        _infeasible(
            f"bolt holes at z={bolt_z:g} would break into the horizontal arm "
            f"(thickness_h {th:g}, bolt radius {bolt_r:g})",
            FailureCode.MANUFACTURING_RULE_VIOLATED,
        )
    if bolt_z + bolt_r >= height:
        _infeasible(
            f"bolt holes at z={bolt_z:g} would break through the top face",
            FailureCode.MANUFACTURING_RULE_VIOLATED,
        )
    for y in (0.25 * width, 0.75 * width):
        if y - bolt_r <= 0 or y + bolt_r >= width:
            _infeasible(
                f"bolt hole at y={y:g} breaks through the side face (width {width:g})",
                FailureCode.MANUFACTURING_RULE_VIOLATED,
            )

    # --- build -------------------------------------------------------------
    horizontal = occ.addBox(0.0, 0.0, 0.0, length, width, th)
    vertical = occ.addBox(0.0, 0.0, 0.0, tv, width, height)
    fused, _ = occ.fuse([(3, horizontal)], [(3, vertical)])
    occ.synchronize()
    if len(fused) != 1:
        _infeasible(f"fuse produced {len(fused)} solids; expected 1")
    volume = int(fused[0][1])

    if radius > 0:
        corner_edges = _edges_at_line(occ, gmsh, x=tv, z=th)
        if not corner_edges:
            _infeasible("could not locate the internal corner edge to fillet")
        try:
            filleted = occ.fillet([volume], corner_edges, [radius])
            occ.synchronize()
        except Exception as exc:
            _infeasible(
                f"fillet of radius {radius:g} failed on this geometry: {exc}",
                FailureCode.GEOMETRY_RECOMPUTE_FAILED,
            )
        if not filleted:
            _infeasible(
                f"fillet of radius {radius:g} consumed the solid",
                FailureCode.GEOMETRY_RECOMPUTE_FAILED,
            )
        volume = int(filleted[0][1])

    holes = []
    for y in (0.25 * width, 0.75 * width):
        holes.append((3, occ.addCylinder(-1.0, y, bolt_z, tv + 2.0, 0.0, 0.0, bolt_r)))
    cut, _ = occ.cut([(3, volume)], holes)
    occ.synchronize()
    if len(cut) != 1:
        _infeasible(f"bolt holes split the bracket into {len(cut)} solids")
    return int(cut[0][1])


def _edges_at_line(occ: Any, gmsh: Any, *, x: float, z: float, tol: float = 1e-6) -> list[int]:
    """Edges lying on the line (x, *, z) — the internal corner of the bracket."""
    found: list[int] = []
    for _dim, tag in gmsh.model.getEntities(1):
        xmin, _ymin, zmin, xmax, _ymax, zmax = occ.getBoundingBox(1, tag)
        if (
            abs(xmin - x) < tol
            and abs(xmax - x) < tol
            and abs(zmin - z) < tol
            and abs(zmax - z) < tol
        ):
            found.append(int(tag))
    return found


register(
    Template(
        name="l_bracket",
        build=_build_l_bracket,
        defaults=_L_BRACKET_DEFAULTS,
        description=(
            "Two-plate L bracket with a filleted internal corner and two bolt holes "
            "through the vertical arm. Bolted at x=0, loaded on the far end face."
        ),
        suggested_regions=("mounting_face", "bolt_holes", "load_face"),
    )
)


# ---------------------------------------------------------------------------
# Plate with a central hole — stress concentration verification
# ---------------------------------------------------------------------------

_PLATE_DEFAULTS = {
    "length": 200.0,
    "width": 100.0,
    "thickness": 10.0,
    "hole_diameter": 20.0,
}


def _build_plate_with_hole(gmsh: Any, parameters: dict[str, Any]) -> int:
    p = _merged(_PLATE_DEFAULTS, parameters)
    _require_positive(p, "length", "width", "thickness", "hole_diameter")
    occ = gmsh.model.occ

    length, width = float(p["length"]), float(p["width"])
    thickness, diameter = float(p["thickness"]), float(p["hole_diameter"])
    radius = 0.5 * diameter
    if diameter >= 0.9 * width:
        _infeasible(
            f"hole diameter {diameter:g} leaves too little ligament in a {width:g} wide plate",
            FailureCode.MANUFACTURING_RULE_VIOLATED,
        )

    plate = occ.addBox(0.0, 0.0, 0.0, length, width, thickness)
    hole = occ.addCylinder(0.5 * length, 0.5 * width, -1.0, 0.0, 0.0, thickness + 2.0, radius)
    cut, _ = occ.cut([(3, plate)], [(3, hole)])
    occ.synchronize()
    if len(cut) != 1:
        _infeasible(f"cut produced {len(cut)} solids; expected 1")
    return int(cut[0][1])


register(
    Template(
        name="plate_with_hole",
        build=_build_plate_with_hole,
        defaults=_PLATE_DEFAULTS,
        description=(
            "Flat plate with a central circular hole under tension. Verification case "
            "for stress concentration factor against the Howland/Peterson solution."
        ),
        suggested_regions=("fixed_face", "load_face", "hole_surface"),
    )
)
