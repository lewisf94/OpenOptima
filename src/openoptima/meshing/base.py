"""Mesh data structures shared by the mesher and the solver adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MeshQualityReport:
    node_count: int
    element_count: int
    element_type: str
    min_scaled_jacobian: float
    mean_scaled_jacobian: float
    inverted_elements: int
    mesh_volume: float
    cad_volume: float
    algorithm: str
    attempt: int
    warnings: tuple[str, ...] = ()

    @property
    def volume_error(self) -> float:
        if self.cad_volume <= 0:
            return float("nan")
        return abs(self.mesh_volume - self.cad_volume) / self.cad_volume

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "element_count": self.element_count,
            "element_type": self.element_type,
            "min_scaled_jacobian": self.min_scaled_jacobian,
            "mean_scaled_jacobian": self.mean_scaled_jacobian,
            "inverted_elements": self.inverted_elements,
            "mesh_volume_mm3": self.mesh_volume,
            "cad_volume_mm3": self.cad_volume,
            "volume_error": self.volume_error,
            "algorithm": self.algorithm,
            "attempt": self.attempt,
            "warnings": list(self.warnings),
        }


@dataclass
class MeshData:
    """A volume mesh plus the surface sets the boundary conditions attach to.

    Held in memory rather than round-tripped through a file: the mesher and the
    deck writer run in the same worker process, and a file round-trip is both
    slower and an opportunity for the node numbering to drift.
    """

    node_tags: np.ndarray  # (N,) int64
    coordinates: np.ndarray  # (N, 3) float64
    element_tags: np.ndarray  # (E,) int64
    connectivity: np.ndarray  # (E, 4 | 10) int64, already in CalculiX order
    element_type: str  # "C3D4" | "C3D10"
    #: region name -> sorted node tags on that region
    surface_nodes: dict[str, np.ndarray] = field(default_factory=dict)
    #: region name -> (T, 3 | 6) node tags of the surface triangles
    surface_triangles: dict[str, np.ndarray] = field(default_factory=dict)
    quality: MeshQualityReport | None = None

    def __post_init__(self) -> None:
        self._index: dict[int, int] = {int(tag): index for index, tag in enumerate(self.node_tags)}

    @property
    def node_count(self) -> int:
        return len(self.node_tags)

    @property
    def element_count(self) -> int:
        return len(self.element_tags)

    @property
    def nodes_per_element(self) -> int:
        return int(self.connectivity.shape[1])

    def index_of(self, node_tag: int) -> int:
        return self._index[int(node_tag)]

    def coordinates_of(self, node_tags: np.ndarray) -> np.ndarray:
        return self.coordinates[[self._index[int(t)] for t in node_tags]]
