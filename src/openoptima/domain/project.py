"""The project aggregate: one complete, self-contained optimisation study definition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .failures import EvaluationFailure, FailureCode
from .features import EdgeFeature
from .model import (
    AnalysisModel,
    AnyMaterial,
    BucklingSettings,
    LoadCase,
    MeshSpecification,
    ModalSettings,
    PointMass,
    SolverSpecification,
    StressEvaluation,
)
from .objectives import Constraint, Objective, PreferenceModel
from .orthotropic import BUILD_AXES, OrthotropicMaterial
from .regions import SemanticRegion
from .units import UnitSystem, get_unit_system
from .variables import DesignSpace

#: Bumped whenever the on-disk project format changes incompatibly.
CURRENT_SCHEMA_VERSION = 1


def _material_digest(
    material: AnyMaterial, direction_variable: str | None = None
) -> dict[str, Any]:
    """The part of a material that can change a number, for the cache hash.

    A printed material and an ordinary one share only a name and a density, so
    each contributes its own fields. Both carry a ``kind``: without it a
    printed material whose stiffness happened to match an ordinary one would
    hash identically, and a cached result computed with the layers running one
    way would be served for a part built the other way.
    """
    if isinstance(material, OrthotropicMaterial):
        strength = material.strength
        return {
            "kind": "printed",
            "name": material.name,
            "E": list(material.modulus),
            "nu": list(material.poisson),
            "G": list(material.shear_modulus),
            "rho": material.density,
            # When the optimiser chooses the print direction, this hash must
            # *not* pin one: the chosen axis rides on the design vector, which
            # is hashed separately, and baking the default in here would make
            # every orientation of the same section look like one cached
            # result. Record which variable decides instead.
            "build_direction": (
                f"variable:{direction_variable}"
                if direction_variable is not None
                else list(material.normalised_build_direction)
            ),
            "strength": None
            if strength is None
            else {
                "tension": list(strength.tension),
                "compression": list(strength.compression),
                "shear": list(strength.shear),
            },
        }
    return {
        "kind": "isotropic",
        "name": material.name,
        "E": material.elastic_modulus,
        "nu": material.poisson_ratio,
        "rho": material.density,
        "allowable": material.allowable_stress,
    }


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
    #: Rounded or cut-back corners OpenOptima adds on top of the shape above,
    #: in the order they are applied. This is what gives an imported CAD file
    #: something to vary: the file itself holds no dimensions. See
    #: ``domain/features.py``.
    features: tuple[EdgeFeature, ...] = ()


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
    material: AnyMaterial
    load_cases: tuple[LoadCase, ...]
    mesh: MeshSpecification
    objectives: tuple[Objective, ...]
    #: Heavy things the part carries but is not made of -- a motor, a camera,
    #: a battery. They add mass and weight, never stiffness. See ``PointMass``.
    point_masses: tuple[PointMass, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    solver: SolverSpecification = field(default_factory=SolverSpecification)
    stress_evaluation: StressEvaluation = field(default_factory=StressEvaluation)
    buckling: BucklingSettings = field(default_factory=BucklingSettings)
    modal: ModalSettings = field(default_factory=ModalSettings)
    #: Failure criterion for a material with directional strengths. Has no
    #: effect on an isotropic material, which uses its allowable stress.
    failure_criterion: str = "hoffman"
    #: Name of a categorical design variable that chooses which way the part is
    #: printed, when that is something the optimiser may decide rather than
    #: something the engineer fixed. ``None`` means the material's own
    #: ``build_direction`` stands. See :meth:`analysis_model`.
    build_direction_variable: str | None = None
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
        seen_masses: set[str] = set()
        for point_mass in self.point_masses:
            if point_mass.region not in known_regions:
                raise ValueError(
                    f"Point mass {point_mass.name!r} is attached to unknown region "
                    f"{point_mass.region!r}. Defined regions: {sorted(known_regions)}"
                )
            if point_mass.name in seen_masses:
                raise ValueError(f"Duplicate point mass name {point_mass.name!r}")
            seen_masses.add(point_mass.name)
        for refinement in self.mesh.local_refinements:
            if refinement.region not in known_regions:
                raise ValueError(f"Mesh refinement references unknown region {refinement.region!r}")

        case_ids = [lc.id for lc in self.load_cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError(f"Duplicate load case ids: {case_ids}")

        if self.build_direction_variable is not None:
            if not isinstance(self.material, OrthotropicMaterial):
                raise ValueError(
                    f"{self.build_direction_variable!r} chooses which way the part is "
                    f"printed, but this project's material is not a printed one. Which "
                    f"way up an ordinary material is made does not change its strength."
                )
            variable = next(
                (v for v in self.design_space if v.id == self.build_direction_variable), None
            )
            if variable is None:
                raise ValueError(
                    f"build_direction names {self.build_direction_variable!r}, which is "
                    f"not a design variable. Defined variables: "
                    f"{sorted(self.design_space.ids)}"
                )
            unknown = [c for c in variable.choices if str(c) not in BUILD_AXES]
            if unknown:
                raise ValueError(
                    f"Variable {variable.id!r} chooses which way the part is printed, so "
                    f"its choices must name the axis the layers stack along "
                    f"({', '.join(sorted(BUILD_AXES))}). Not recognised: "
                    f"{', '.join(repr(c) for c in unknown)}."
                )

        if self.buckling.enabled and isinstance(self.material, OrthotropicMaterial):
            # The buckling result is only reported when it agrees with a beam
            # theory cross-check, and that check needs one stiffness for the
            # whole part (see results/buckling_check.py). A printed part has a
            # different stiffness along its layers and through them, so there
            # is no single number to check against, and picking either one
            # would validate the answer against the wrong material. Refused
            # rather than guessed: an unchecked buckling number fails in the
            # optimistic direction, which is the one that selects an unsafe
            # design.
            raise ValueError(
                f"Material {self.material.name!r} is printed, so it is stiffer along "
                f"its layers than through them. OpenOptima cannot check a buckling "
                f"result for such a material yet: the check it runs to decide whether "
                f"a buckling number is trustworthy assumes one stiffness in every "
                f"direction. Set `buckling.enabled: false`. Stress, deflection and "
                f"natural frequency are unaffected."
            )

    @property
    def unit_system(self) -> UnitSystem:
        return get_unit_system(self.unit_system_name)

    def region(self, name: str) -> SemanticRegion:
        for region in self.regions:
            if region.name == name:
                return region
        raise KeyError(name)

    def material_for(self, design: Mapping[str, Any] | None = None) -> AnyMaterial:
        """The material as this design prints it.

        Which way up a part is printed is a structural decision, not a workshop
        one -- on ``examples/drone_arm`` it moves the factor of safety from
        3.07 to 1.55 while the stress does not move at all. So a project may
        hand that choice to the optimiser, and this resolves what it chose.

        Everything else about the material is fixed. Only the direction the
        layers run can vary, because that is the only part of it a printer
        setting decides.
        """
        if self.build_direction_variable is None or design is None:
            return self.material
        assert isinstance(self.material, OrthotropicMaterial)  # enforced in __post_init__
        chosen = design.get(self.build_direction_variable)
        if chosen is None:
            return self.material
        try:
            axis = BUILD_AXES[str(chosen)]
        except KeyError:
            raise EvaluationFailure(
                FailureCode.INVALID_DESIGN_VARIABLES,
                f"Variable {self.build_direction_variable!r} chooses which way the "
                f"part is printed, and {chosen!r} is not one of "
                f"{', '.join(sorted(BUILD_AXES))}.",
            ) from None
        return replace(self.material, build_direction=axis)

    def analysis_model(self, design: Mapping[str, Any] | None = None) -> AnalysisModel:
        return AnalysisModel(
            name=self.name,
            material=self.material_for(design),
            load_cases=self.load_cases,
            point_masses=self.point_masses,
            stress_evaluation=self.stress_evaluation,
            element_order=self.mesh.element_order,
            buckling=self.buckling,
            modal=self.modal,
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
                # A feature changes the shape, so it changes every number that
                # comes out of it. Adding one to a project must invalidate the
                # results computed before it existed.
                "features": [
                    {
                        "name": f.name,
                        "kind": f.kind.value,
                        "between": list(f.between),
                        "size": f.size,
                    }
                    for f in self.geometry.features
                ],
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
            "regions": [
                {
                    "name": r.name,
                    "selector": r.selector.describe(),
                    "min_area_mm2": r.min_area_mm2,
                }
                for r in self.regions
            ],
            "material": _material_digest(self.material, self.build_direction_variable),
            # A carried mass changes every natural frequency and every
            # acceleration load, so a result computed without one is not a
            # cache hit for a project that has one.
            "point_masses": [
                {"name": pm.name, "region": pm.region, "mass": pm.mass} for pm in self.point_masses
            ],
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
            # Turning modal analysis on adds a number to the result, and asking
            # for more modes can add more. A result computed without them is
            # not a cache hit for a project that wants them.
            "modal": {
                "enabled": self.modal.enabled,
                "modes": self.modal.modes,
            },
            "solver": {"name": self.solver.name},
            # Changing the failure criterion changes the reported factor of
            # safety, so a result computed under the old one is not a cache
            # hit for the new one.
            "failure_criterion": self.failure_criterion,
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
