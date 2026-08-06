"""Run directories, manifests and provenance.

Every evaluation gets its own directory containing the geometry, the mesh, the
solver deck, the raw solver output and a manifest.  This costs disk and buys the
only thing that matters when a number is questioned six months later: the
ability to reproduce it exactly, and to see what version of what produced it.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def tool_versions() -> dict[str, str]:
    """Versions of everything that can change a number."""
    versions: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    try:
        from .. import __version__

        versions["openoptima"] = __version__
    except Exception:  # pragma: no cover
        versions["openoptima"] = "unknown"
    for module_name, key in (("gmsh", "gmsh"), ("numpy", "numpy"), ("scipy", "scipy")):
        try:
            module = __import__(module_name)
            versions[key] = str(getattr(module, "__version__", "unknown"))
        except Exception:  # pragma: no cover - optional
            versions[key] = "not installed"
    try:
        from ..domain.model import SolverSpecification
        from ..solvers.calculix.runner import find_executable, solver_version

        executable = find_executable(SolverSpecification())
        versions["calculix"] = solver_version(executable) if executable else "not found"
    except Exception:  # pragma: no cover
        versions["calculix"] = "unknown"
    return versions


@dataclass
class RunSpace:
    """The directory layout for one evaluation."""

    root: Path
    run_id: str
    keep_artifacts: bool = True
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def directory(self) -> Path:
        return self.root / self.run_id

    @property
    def geometry_dir(self) -> Path:
        return self.directory / "geometry"

    @property
    def mesh_dir(self) -> Path:
        return self.directory / "mesh"

    @property
    def solver_dir(self) -> Path:
        return self.directory / "solver"

    @property
    def results_dir(self) -> Path:
        return self.directory / "results"

    def prepare(self) -> RunSpace:
        for path in (
            self.geometry_dir,
            self.mesh_dir,
            self.solver_dir,
            self.results_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def write_json(self, relative: str, payload: Any) -> Path:
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    def write_manifest(self) -> Path:
        return self.write_json("evaluation_manifest.json", self.manifest)

    def discard_bulk(self) -> None:
        """Delete large intermediates, keeping manifests and metrics.

        A 500-design study keeps 500 meshes otherwise, which fills a disk long
        before the study finishes.
        """
        for path in (self.geometry_dir, self.mesh_dir):
            shutil.rmtree(path, ignore_errors=True)
        if self.solver_dir.exists():
            for entry in self.solver_dir.iterdir():
                if entry.is_file() and entry.suffix in {".frd", ".inp", ".dat", ".sta", ".cvg"}:
                    entry.unlink(missing_ok=True)


class RunSpaceFactory:
    """Allocates sequential run directories under a project's ``runs/`` folder."""

    def __init__(self, root: Path, keep_artifacts: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep_artifacts = keep_artifacts
        self._counter = self._highest_existing()

    def _highest_existing(self) -> int:
        highest = 0
        for entry in self.root.iterdir() if self.root.exists() else []:
            if entry.is_dir() and entry.name.isdigit():
                highest = max(highest, int(entry.name))
        return highest

    def allocate(self, run_id: str | None = None) -> RunSpace:
        if run_id is None:
            self._counter += 1
            run_id = f"{self._counter:06d}"
        return RunSpace(root=self.root, run_id=run_id, keep_artifacts=self.keep_artifacts).prepare()

    def reserve(self, count: int) -> list[str]:
        """Pre-allocate run ids for workers running in other processes."""
        ids = [f"{self._counter + offset:06d}" for offset in range(1, count + 1)]
        self._counter += count
        return ids
