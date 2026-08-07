"""Result records passed from the evaluation pipeline to the optimiser.

The optimiser sees only :class:`EvaluationResult`.  It never touches an FRD
file, a mesh, or a solver log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .failures import EvaluationState, FailureCode, Outcome, outcome_for
from .variables import DesignVector


@dataclass(frozen=True)
class LoadCaseResult:
    """Metrics from one load case."""

    load_case_id: str
    displacement_max: float  # mm
    displacement_node: int | None
    stress_measure: float  # MPa, per the configured StressEvaluation
    stress_raw_max: float  # MPa, always reported even when not used
    stress_measure_name: str
    reaction_force: tuple[float, float, float]  # N
    strain_energy: float | None = None  # mJ
    #: Lowest positive buckling factor. None when buckling was not analysed, or
    #: when nothing buckles under this load (a purely tensile case).
    buckling_factor: float | None = None
    buckling_modes: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "load_case_id": self.load_case_id,
            "displacement_max_mm": self.displacement_max,
            "displacement_node": self.displacement_node,
            "stress_measure_mpa": self.stress_measure,
            "stress_raw_max_mpa": self.stress_raw_max,
            "stress_measure_name": self.stress_measure_name,
            "reaction_force_n": list(self.reaction_force),
            "strain_energy_mj": self.strain_energy,
            "buckling_factor": self.buckling_factor,
            "buckling_modes": list(self.buckling_modes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LoadCaseResult:
        """Rebuild from :meth:`to_dict`, for reading a stored result back."""
        reaction = tuple(payload.get("reaction_force_n") or (0.0, 0.0, 0.0))
        return cls(
            load_case_id=payload["load_case_id"],
            displacement_max=payload["displacement_max_mm"],
            displacement_node=payload.get("displacement_node"),
            stress_measure=payload["stress_measure_mpa"],
            stress_raw_max=payload["stress_raw_max_mpa"],
            stress_measure_name=payload.get("stress_measure_name", ""),
            reaction_force=(reaction[0], reaction[1], reaction[2]),
            strain_energy=payload.get("strain_energy_mj"),
            buckling_factor=payload.get("buckling_factor"),
            buckling_modes=tuple(payload.get("buckling_modes") or ()),
        )


@dataclass(frozen=True)
class MeshSummary:
    node_count: int
    element_count: int
    element_type: str
    min_scaled_jacobian: float
    mesh_volume: float
    cad_volume: float
    volume_error: float
    algorithm: str
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "element_count": self.element_count,
            "element_type": self.element_type,
            "min_scaled_jacobian": self.min_scaled_jacobian,
            "mesh_volume_mm3": self.mesh_volume,
            "cad_volume_mm3": self.cad_volume,
            "volume_error": self.volume_error,
            "algorithm": self.algorithm,
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MeshSummary:
        """Rebuild from :meth:`to_dict`, for reading a stored result back."""
        return cls(
            node_count=payload["node_count"],
            element_count=payload["element_count"],
            element_type=payload["element_type"],
            min_scaled_jacobian=payload["min_scaled_jacobian"],
            mesh_volume=payload["mesh_volume_mm3"],
            cad_volume=payload["cad_volume_mm3"],
            volume_error=payload["volume_error"],
            algorithm=payload.get("algorithm", ""),
            attempt=payload.get("attempt", 1),
        )


@dataclass
class EvaluationResult:
    """Everything known about one design after evaluation."""

    design: DesignVector
    outcome: Outcome
    state: EvaluationState
    metrics: dict[str, float] = field(default_factory=dict)
    load_cases: tuple[LoadCaseResult, ...] = ()
    mesh: MeshSummary | None = None
    failure_code: FailureCode | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    #: Populated for INFEASIBLE results; the optimiser uses these directly.
    constraint_violations: dict[str, float] = field(default_factory=dict)
    run_id: str = ""
    run_directory: str = ""
    evaluation_hash: str = ""
    wall_time: float = 0.0
    from_cache: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def feasible(self) -> bool:
        return self.outcome is Outcome.OK and not any(
            value > 0 for value in self.constraint_violations.values()
        )

    @property
    def total_violation(self) -> float:
        return sum(max(0.0, v) for v in self.constraint_violations.values())

    def metric(self, name: str, default: float = float("nan")) -> float:
        return self.metrics.get(name, default)

    def governing_load_case(self) -> LoadCaseResult | None:
        """The load case with the highest stress — never an average."""
        if not self.load_cases:
            return None
        return max(self.load_cases, key=lambda lc: lc.stress_measure)

    @classmethod
    def failed(
        cls,
        design: DesignVector,
        code: FailureCode,
        message: str,
        *,
        state: EvaluationState = EvaluationState.CREATED,
        **kwargs: Any,
    ) -> EvaluationResult:
        return cls(
            design=design,
            outcome=outcome_for(code),
            state=state,
            failure_code=code,
            message=message,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "design": self.design.as_dict(),
            "design_digest": self.design.digest(),
            "outcome": self.outcome.value,
            "state": self.state.value,
            "failure_code": self.failure_code.value if self.failure_code else None,
            "message": self.message,
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
            "constraint_violations": dict(self.constraint_violations),
            "load_cases": [lc.to_dict() for lc in self.load_cases],
            "mesh": self.mesh.to_dict() if self.mesh else None,
            "evaluation_hash": self.evaluation_hash,
            "run_directory": self.run_directory,
            "wall_time_s": self.wall_time,
            "from_cache": self.from_cache,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }
