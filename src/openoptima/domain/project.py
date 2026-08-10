"""The project aggregate: one complete, self-contained optimisation study definition."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .model import (
    AnalysisModel,
    BucklingSettings,
    LoadCase,
    Material,
    MeshSpecification,
    SolverSpecification,
    StressEvaluation,
)
from .objectives import Constraint, Objective, PreferenceModel
from .regions import SemanticRegion
from .units import UnitSystem, get_unit_system
from .variables import DesignSpace

#: Bumped whenever the on-disk project format changes incompatibly.
CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GeometryDefinition:
    """Where the parametric model comes from."""

    provider: str
    #: Built-in template name (``occ`` provider) or callable path (``cadquery``).
    template: str = ""
    #: Path to a user script or CAD document, relative to the project root.
    source: str | None = None
    #: Fixed parameters that are *not* design variables.
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SamplingSettings:
    method: str = "sobol"  # sobol | lhs | random | factorial
    evaluations: int = 32
    seed: int = 1


@dataclass(frozen=True)
class AlgorithmSettings:
    name: str = "nsga2"
    population_size: int = 24
    evaluation_budget: int = 240
    seed: int = 1


@dataclass(frozen=True)
class OptimisationSettings:
    initial_sampling: SamplingSettings = field(default_factory=SamplingSettings)
    algorithm: AlgorithmSettings = field(default_factory=AlgorithmSettings)
    #: Concurrent evaluations. ``0`` means choose from the machine's core count.
    parallel_jobs: int = 0
    #: Retries for infrastructure errors only. Infeasible designs are never retried.
    max_retries: int = 1


@dataclass(frozen=True)
class Project:
    """A complete study.  Everything needed to reproduce a result is reachable from here."""

    name: str
    geometry: GeometryDefinition
    design_space: DesignSpace
    regions: tuple[SemanticRegion, ...]
    material: Material
    load_cases: tuple[LoadCase, ...]
    mesh: MeshSpecification
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...] = ()
    solver: SolverSpecification = field(default_factory=SolverSpecification)
    stress_evaluation: StressEvaluation = field(default_factory=StressEvaluation)
    buckling: BucklingSettings = field(default_factory=BucklingSettings)
    #: Failure criterion for a material with directional strengths. Has no
    #: effect on an isotropic material, which uses its allowable stress.
    failure_criterion: str = "hoffman"
    preferences: PreferenceModel = field(default_factory=PreferenceModel)
    optimisation: OptimisationSettings = field(default_factory=OptimisationSettings)
    unit_system_name: str = "mm_N_MPa_t"
    schema_version: int = CURRENT_SCHEMA_VERSION
    description: str = ""

    def __post_init__(self) -> None:
        get_unit_system(self.unit_system_name)  # raises on an unknown system
        if not self.objectives:
            raise ValueError("A project needs at least one objective")
        if not self.load_cases:
            raise ValueError("A project needs at least one load case")

        known_regions = {region.name for region in self.regions}
        for load_case in self.load_cases:
            for name in load_case.regions_used:
                if name not in known_regions:
                    raise ValueError(
                        f"Load case {load_case.id!r} references unknown region {name!r}. "
                        f"Defined regions: {sorted(known_regions)}"
                    )
        for name in self.stress_evaluation.excluded_regions:
            if name not in known_regions:
                raise ValueError(f"stress_evaluation excludes unknown region {name!r}")
        for refinement in self.mesh.local_refinements:
            if refinement.region not in known_regions:
                raise ValueError(f"Mesh refinement references unknown region {refinement.region!r}")

        case_ids = [lc.id for lc in self.load_cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError(f"Duplicate load case ids: {case_ids}")

    @property
    def unit_system(self) -> UnitSystem:
        return get_unit_system(self.unit_system_name)

    def region(self, name: str) -> SemanticRegion:
        for region in self.regions:
            if region.name == name:
                return region
        raise KeyError(name)

    def analysis_model(self) -> AnalysisModel:
        return AnalysisModel(
            name=self.name,
            material=self.material,
            load_cases=self.load_cases,
            stress_evaluation=self.stress_evaluation,
            element_order=self.mesh.element_order,
            buckling=self.buckling,
            failure_criterion=self.failure_criterion,
        )

    def objective_metrics(self) -> tuple[str, ...]:
        return tuple(objective.metric for objective in self.objectives)

    def setup_digest(self) -> str:
        """Hash of everything that affects a result *except* the design vector.

        Changing a material, a load, a mesh setting or a stress measure changes
        this digest, which invalidates every cached evaluation.  That is the
        point: a cached number computed under different physics is not a
        cache hit, it is a wrong answer.
        """
        payload = {
            "schema_version": self.schema_version,
            "unit_system": self.unit_system_name,
            "geometry": {
                "provider": self.geometry.provider,
                "template": self.geometry.template,
                "source": self.geometry.source,
                "parameters": self.geometry.parameters,
            },
            "variables": [
                {
                    "id": v.id,
                    "type": v.type.value,
                    "min": v.minimum,
                    "max": v.maximum,
                    "step": v.step,
                    "choices": list(v.choices),
                    "default": v.default,
                }
                for v in self.design_space
            ],
            "regions": [{"name": r.name, "selector": r.selector.describe()} for r in self.regions],
            "material": {
                "name": self.material.name,
                "E": self.material.elastic_modulus,
                "nu": self.material.poisson_ratio,
                "rho": self.material.density,
                "allowable": self.material.allowable_stress,
            },
            "load_cases": [
                {
                    "id": lc.id,
                    "loads": [
                        {
                            "kind": load.kind.value,
                            "region": load.region,
                            "vector": list(load.vector),
                            "magnitude": load.magnitude,
                        }
                        for load in lc.loads
                    ],
                    "bcs": [
                        {"region": bc.region, "kind": bc.kind.value, "dofs": list(bc.dofs)}
                        for bc in lc.boundary_conditions
                    ],
                }
                for lc in self.load_cases
            ],
            "mesh": {
                "global_size": self.mesh.global_size,
                "minimum_size": self.mesh.minimum_size,
                "order": self.mesh.element_order,
                "algorithm": self.mesh.algorithm.value,
                "curvature": self.mesh.curvature_refinement,
                "curvature_elements": self.mesh.curvature_elements,
                "refinements": [
                    {"region": r.region, "size": r.size, "distance": r.distance}
                    for r in self.mesh.local_refinements
                ],
            },
            "stress": {
                "measure": self.stress_evaluation.measure,
                "percentile": self.stress_evaluation.percentile,
                "pnorm": self.stress_evaluation.pnorm_exponent,
                "excluded": list(self.stress_evaluation.excluded_regions),
                "exclusion_radius": self.stress_evaluation.exclusion_radius,
            },
            "buckling": {
                "enabled": self.buckling.enabled,
                "modes": self.buckling.modes,
                "slenderness_limit": self.buckling.slenderness_limit,
            },
            "solver": {"name": self.solver.name},
            # Changing the failure criterion changes the reported factor of
            # safety, so a result computed under the old one is not a cache
            # hit for the new one.
            "failure_criterion": self.failure_criterion,
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
