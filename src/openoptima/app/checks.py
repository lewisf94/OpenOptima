"""The setup check, shaped for the app rather than the terminal.

Same work as ``openoptima doctor``: build the part at the extremes of its
design range and confirm every region still resolves to exactly one surface.
The difference is the output, which is structured so the page can show a green
tick or a specific complaint instead of a wall of text.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..domain.model import SolverSpecification
from ..domain.project import Project
from ..geometry import create_provider
from ..geometry.gmsh_session import gmsh_session
from ..regions.matcher import resolve_regions
from ..regions.signature import outward_normal_check, solid_face_signatures
from ..solvers import create_solver


def run_doctor(project: Project, path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    solver = create_solver(SolverSpecification(name=project.solver.name))
    available, message = solver.available()
    checks.append(
        {
            "name": "Stress solver",
            "ok": available,
            "detail": message,
            "fix": None if available else "Install CalculiX, then reopen this project.",
        }
    )

    provider = create_provider(project.geometry)
    report = provider.validate_definition()
    checks.append(
        {
            "name": "Part definition",
            "ok": report.ok,
            "detail": project.geometry.template or project.geometry.source or "",
            "fix": "; ".join(report.errors) if report.errors else None,
        }
    )

    probes: list[dict[str, Any]] = []
    if report.ok:
        lower, upper = project.design_space.bounds()
        cases = {
            "smallest": project.design_space.from_array(list(lower)),
            "default": project.design_space.defaults(),
            "largest": project.design_space.from_array(list(upper)),
        }
        with tempfile.TemporaryDirectory(prefix="openoptima-check-") as scratch:
            for label, design in cases.items():
                probes.append(_probe(project, provider, design, label, Path(scratch)))

    regions_ok = all(p["ok"] for p in probes) if probes else False
    checks.append(
        {
            "name": "Faces stay findable across the whole size range",
            "ok": regions_ok,
            "detail": (
                "Every load and support surface was found exactly once at the "
                "smallest, default and largest sizes."
                if regions_ok
                else "At least one surface could not be found, or matched more than once."
            ),
            "fix": None
            if regions_ok
            else "Make the description more specific — add a position box or a size filter.",
        }
    )

    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "probes": probes,
        "project": project.name,
        "path": str(path),
    }


def _probe(
    project: Project, provider: Any, design: Any, label: str, scratch: Path
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "label": label,
        "design": design.as_dict(),
        "ok": False,
        "regions": [],
        "error": "",
    }
    try:
        geometry = provider.build(design, scratch / label)
    except Exception as exc:
        entry["error"] = f"the part could not be built: {exc}"
        return entry

    entry["volume_mm3"] = geometry.volume
    entry["mass_kg"] = geometry.volume * project.material.density * 1e3

    try:
        with gmsh_session() as gmsh:
            gmsh.model.add(f"check_{label}")
            gmsh.model.occ.importShapes(str(geometry.brep_path))
            gmsh.model.occ.synchronize()
            volume_tag = gmsh.model.getEntities(3)[0][1]
            signatures = solid_face_signatures(gmsh, volume_tag)
            outward, _ratio = outward_normal_check(signatures, geometry.volume)
            matches = resolve_regions(
                project.regions, signatures, scale_length=geometry.bbox.diagonal
            )
        entry["normals_outward"] = outward
        entry["regions"] = [
            {
                "name": name,
                "faces": len(match.face_tags),
                "area_mm2": match.total_area,
                "unique": match.margin == float("inf") or match.margin > 0,
            }
            for name, match in matches.matches.items()
        ]
        entry["ok"] = outward and bool(entry["regions"])
    except Exception as exc:
        entry["error"] = str(exc)
    return entry
