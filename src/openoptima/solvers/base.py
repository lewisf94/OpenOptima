"""Structural solver protocol and result containers.

Keeping this interface narrow is what makes a second solver (Code_Aster,
Kratos, a CFD backend) an addition rather than a rewrite: the pipeline above
only ever sees :class:`AnalysisResults`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..domain.model import AnalysisModel
from ..meshing.base import MeshData


@dataclass(frozen=True)
class LoadCaseFields:
    """Raw nodal fields from one load case."""

    load_case_id: str
    node_tags: np.ndarray  # (N,)
    displacement: np.ndarray  # (N, 3) mm
    von_mises: np.ndarray  # (N,) MPa
    reaction_force: tuple[float, float, float]  # N
    strain_energy: float | None = None  # mJ

    @property
    def displacement_magnitude(self) -> np.ndarray:
        return np.linalg.norm(self.displacement, axis=1)


@dataclass(frozen=True)
class AnalysisResults:
    load_cases: tuple[LoadCaseFields, ...]
    solver_name: str
    solver_version: str = ""
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def by_id(self, load_case_id: str) -> LoadCaseFields:
        for case in self.load_cases:
            if case.load_case_id == load_case_id:
                return case
        raise KeyError(load_case_id)


@runtime_checkable
class StructuralSolver(Protocol):
    name: str

    def available(self) -> tuple[bool, str]:
        """Whether the solver can run here, and a message if not."""
        ...

    def solve(
        self,
        model: AnalysisModel,
        mesh: MeshData,
        working_directory: Path,
    ) -> AnalysisResults:
        """Run every load case and return nodal fields."""
        ...


def von_mises_from_tensor(components: np.ndarray) -> np.ndarray:
    """Von Mises stress from (N, 6) components ordered sxx, syy, szz, sxy, syz, szx."""
    sxx, syy, szz = components[:, 0], components[:, 1], components[:, 2]
    sxy, syz, szx = components[:, 3], components[:, 4], components[:, 5]
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )
