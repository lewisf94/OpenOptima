"""Validated, versioned on-disk project format.

Pydantic models here mirror the YAML file and exist purely to give the user a
precise error message with a path ("load_cases.0.loads.1.region: field
required") instead of a ``KeyError`` from deep inside the pipeline.  They are
converted straight into the frozen domain objects and then discarded.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.model import (
    BoundaryCondition,
    BucklingSettings,
    ConstraintKind,
    Load,
    LoadCase,
    LoadKind,
    LocalRefinement,
    Material,
    MeshAlgorithm,
    MeshSpecification,
    SolverSpecification,
    StressEvaluation,
)
from ..domain.objectives import (
    Constraint,
    Direction,
    MetricPreference,
    Objective,
    Operator,
    PreferenceModel,
    TradeRule,
)
from ..domain.project import (
    CURRENT_SCHEMA_VERSION,
    AlgorithmSettings,
    GeometryDefinition,
    OptimisationSettings,
    Project,
    SamplingSettings,
)
from ..domain.regions import (
    BoundingBox,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from ..domain.variables import ActivationRule, DesignSpace, DesignVariable, VariableType

Vector3 = tuple[float, float, float]


class Strict(BaseModel):
    """Reject unknown keys.

    A typo like ``allowable_stres_mpa`` must fail loudly, not silently fall
    back to a default and change the physics.
    """

    model_config = ConfigDict(extra="forbid")


class ActivationSchema(Strict):
    variable: str
    equals: Any


class VariableSchema(Strict):
    id: str
    type: Literal["continuous", "integer", "categorical", "boolean"] = "continuous"
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None
    step: float | None = None
    choices: list[Any] = Field(default_factory=list)
    unit: str = ""
    label: str = ""
    description: str = ""
    active_when: ActivationSchema | None = None

    def to_domain(self) -> DesignVariable:
        return DesignVariable(
            id=self.id,
            type=VariableType(self.type),
            minimum=self.minimum,
            maximum=self.maximum,
            default=self.default,
            step=self.step,
            choices=tuple(self.choices),
            unit=self.unit,
            label=self.label,
            description=self.description,
            active_when=(
                ActivationRule(variable=self.active_when.variable, equals=self.active_when.equals)
                if self.active_when
                else None
            ),
        )


class GeometrySchema(Strict):
    provider: Literal["occ", "cadquery"] = "occ"
    template: str = ""
    source: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    variables: list[VariableSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_source(self) -> GeometrySchema:
        if self.provider == "occ" and not self.template:
            raise ValueError("geometry.template is required for the 'occ' provider")
        if self.provider == "cadquery" and not self.source:
            raise ValueError("geometry.source is required for the 'cadquery' provider")
        return self


class BoxSchema(Strict):
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    def to_domain(self) -> BoundingBox:
        return BoundingBox(self.xmin, self.ymin, self.zmin, self.xmax, self.ymax, self.zmax)


class SelectorSchema(Strict):
    surface_type: Literal["any", "plane", "cylinder", "sphere", "cone", "torus"] = "any"
    normal: Vector3 | None = None
    normal_tolerance_deg: float = 5.0
    within_box: BoxSchema | None = None
    min_area: float | None = None
    max_area: float | None = None
    min_radius: float | None = None
    max_radius: float | None = None
    centroid_near: Vector3 | None = None
    centroid_weight: float = 1.0
    area_near: float | None = None
    area_weight: float = 1.0
    prefer_largest: bool = False
    mode: Literal["single", "all"] = "single"
    ambiguity_margin: float = 0.05

    def to_domain(self) -> RegionSelector:
        return RegionSelector(
            surface_type=SurfaceType(self.surface_type),
            normal=self.normal,
            normal_tolerance_deg=self.normal_tolerance_deg,
            within_box=self.within_box.to_domain() if self.within_box else None,
            min_area=self.min_area,
            max_area=self.max_area,
            min_radius=self.min_radius,
            max_radius=self.max_radius,
            centroid_near=self.centroid_near,
            centroid_weight=self.centroid_weight,
            area_near=self.area_near,
            area_weight=self.area_weight,
            prefer_largest=self.prefer_largest,
            mode=SelectionMode(self.mode),
            ambiguity_margin=self.ambiguity_margin,
        )


class RegionSchema(Strict):
    name: str
    selector: SelectorSchema
    description: str = ""

    def to_domain(self) -> SemanticRegion:
        return SemanticRegion(
            name=self.name, selector=self.selector.to_domain(), description=self.description
        )


class MaterialSchema(Strict):
    name: str
    elastic_modulus_mpa: float
    poisson_ratio: float
    density_kg_m3: float
    allowable_stress_mpa: float
    allowable_stress_basis: str = "unspecified"

    def to_domain(self) -> Material:
        return Material.from_engineering_units(
            name=self.name,
            elastic_modulus_mpa=self.elastic_modulus_mpa,
            poisson_ratio=self.poisson_ratio,
            density_kg_m3=self.density_kg_m3,
            allowable_stress_mpa=self.allowable_stress_mpa,
            allowable_stress_basis=self.allowable_stress_basis,
        )


class LoadSchema(Strict):
    kind: Literal["force", "pressure", "acceleration"] = "force"
    region: str | None = None
    vector: Vector3 = (0.0, 0.0, 0.0)
    magnitude: float = 0.0

    def to_domain(self) -> Load:
        return Load(
            kind=LoadKind(self.kind),
            region=self.region,
            vector=self.vector,
            magnitude=self.magnitude,
        )


class BoundarySchema(Strict):
    region: str
    kind: Literal["fixed", "prescribed_displacement"] = "fixed"
    dofs: list[int] = Field(default_factory=lambda: [1, 2, 3])
    magnitude: float = 0.0

    def to_domain(self) -> BoundaryCondition:
        return BoundaryCondition(
            region=self.region,
            kind=ConstraintKind(self.kind),
            dofs=tuple(self.dofs),
            magnitude=self.magnitude,
        )


class LoadCaseSchema(Strict):
    id: str
    loads: list[LoadSchema]
    boundary_conditions: list[BoundarySchema]
    description: str = ""

    def to_domain(self) -> LoadCase:
        return LoadCase(
            id=self.id,
            loads=tuple(load.to_domain() for load in self.loads),
            boundary_conditions=tuple(bc.to_domain() for bc in self.boundary_conditions),
            description=self.description,
        )


class RefinementSchema(Strict):
    region: str
    size: float
    distance: float


class MeshSchema(Strict):
    global_size: float
    minimum_size: float
    element_order: Literal[1, 2] = 2
    algorithm: Literal["delaunay", "hxt", "frontal"] = "delaunay"
    curvature_refinement: bool = True
    curvature_elements: float = 12.0
    size_from_thickness: bool = True
    optimise: bool = True
    local_refinements: list[RefinementSchema] = Field(default_factory=list)
    min_scaled_jacobian: float = 0.05
    volume_tolerance: float = 0.02
    max_elements: int = 2_000_000

    def to_domain(self) -> MeshSpecification:
        return MeshSpecification(
            global_size=self.global_size,
            minimum_size=self.minimum_size,
            element_order=self.element_order,
            algorithm=MeshAlgorithm(self.algorithm),
            curvature_refinement=self.curvature_refinement,
            curvature_elements=self.curvature_elements,
            size_from_thickness=self.size_from_thickness,
            optimise=self.optimise,
            local_refinements=tuple(
                LocalRefinement(region=r.region, size=r.size, distance=r.distance)
                for r in self.local_refinements
            ),
            min_scaled_jacobian=self.min_scaled_jacobian,
            volume_tolerance=self.volume_tolerance,
            max_elements=self.max_elements,
        )


class StressSchema(Strict):
    measure: Literal["raw_max", "percentile", "pnorm", "region_max"] = "percentile"
    percentile: float = 99.0
    pnorm_exponent: float = 8.0
    excluded_regions: list[str] = Field(default_factory=list)
    exclusion_radius: float = 0.0

    def to_domain(self) -> StressEvaluation:
        return StressEvaluation(
            measure=self.measure,
            percentile=self.percentile,
            pnorm_exponent=self.pnorm_exponent,
            excluded_regions=tuple(self.excluded_regions),
            exclusion_radius=self.exclusion_radius,
        )


class BucklingSchema(Strict):
    """Linear buckling analysis settings.

    Off by default because it costs an extra eigenvalue solve per load case.
    Turn it on for anything with slender sections — which is most things an
    optimiser produces when told to minimise mass.
    """

    enabled: bool = False
    modes: int = 3
    slenderness_limit: float = 150.0

    def to_domain(self) -> BucklingSettings:
        return BucklingSettings(
            enabled=self.enabled,
            modes=self.modes,
            slenderness_limit=self.slenderness_limit,
        )


class SolverSchema(Strict):
    name: Literal["calculix", "analytic"] = "calculix"
    executable: str | None = None
    timeout_seconds: float = 900.0
    threads: int = 1
    extra_options: list[str] = Field(default_factory=list)

    def to_domain(self) -> SolverSpecification:
        return SolverSpecification(
            name=self.name,
            executable=self.executable,
            timeout_seconds=self.timeout_seconds,
            threads=self.threads,
            extra_options=tuple(self.extra_options),
        )


class ObjectiveSchema(Strict):
    metric: str
    direction: Literal["minimise", "maximise"] = "minimise"
    label: str = ""

    def to_domain(self) -> Objective:
        return Objective(metric=self.metric, direction=Direction(self.direction), label=self.label)


class ConstraintSchema(Strict):
    metric: str
    operator: Literal["less_than_or_equal", "greater_than_or_equal"]
    value: float
    label: str = ""
    scale: float | None = None

    def to_domain(self) -> Constraint:
        return Constraint(
            metric=self.metric,
            operator=Operator(self.operator),
            value=self.value,
            label=self.label,
            scale=self.scale,
        )


class DesirabilitySchema(Strict):
    metric: str
    direction: Literal["minimise", "maximise"] = "minimise"
    ideal: float
    acceptable: float
    weight: float = 1.0
    exponent: float = 1.0

    def to_domain(self) -> MetricPreference:
        return MetricPreference(
            metric=self.metric,
            direction=Direction(self.direction),
            ideal=self.ideal,
            acceptable=self.acceptable,
            weight=self.weight,
            exponent=self.exponent,
        )


class TradeRuleSchema(Strict):
    give_metric: str
    give_amount: float
    gain_metric: str
    gain_amount: float

    def to_domain(self) -> TradeRule:
        return TradeRule(
            give_metric=self.give_metric,
            give_amount=self.give_amount,
            gain_metric=self.gain_metric,
            gain_amount=self.gain_amount,
        )


class PreferenceSchema(Strict):
    hard_limits: list[ConstraintSchema] = Field(default_factory=list)
    targets: list[ConstraintSchema] = Field(default_factory=list)
    desirability: list[DesirabilitySchema] = Field(default_factory=list)
    trade_rules: list[TradeRuleSchema] = Field(default_factory=list)

    def to_domain(self) -> PreferenceModel:
        return PreferenceModel(
            hard_limits=tuple(c.to_domain() for c in self.hard_limits),
            targets=tuple(c.to_domain() for c in self.targets),
            desirability=tuple(d.to_domain() for d in self.desirability),
            trade_rules=tuple(t.to_domain() for t in self.trade_rules),
        )


class SamplingSchema(Strict):
    method: Literal["sobol", "lhs", "random", "factorial"] = "sobol"
    evaluations: int = 32
    seed: int = 1


class AlgorithmSchema(Strict):
    name: Literal["nsga2"] = "nsga2"
    population_size: int = 24
    evaluation_budget: int = 240
    seed: int = 1


class OptimisationSchema(Strict):
    initial_sampling: SamplingSchema = Field(default_factory=SamplingSchema)
    algorithm: AlgorithmSchema = Field(default_factory=AlgorithmSchema)
    parallel_jobs: int = 0
    max_retries: int = 1

    def to_domain(self) -> OptimisationSettings:
        return OptimisationSettings(
            initial_sampling=SamplingSettings(
                method=self.initial_sampling.method,
                evaluations=self.initial_sampling.evaluations,
                seed=self.initial_sampling.seed,
            ),
            algorithm=AlgorithmSettings(
                name=self.algorithm.name,
                population_size=self.algorithm.population_size,
                evaluation_budget=self.algorithm.evaluation_budget,
                seed=self.algorithm.seed,
            ),
            parallel_jobs=self.parallel_jobs,
            max_retries=self.max_retries,
        )


class ProjectSchema(Strict):
    schema_version: int = CURRENT_SCHEMA_VERSION
    name: str
    description: str = ""
    unit_system: Literal["mm_N_MPa_t"] = "mm_N_MPa_t"
    geometry: GeometrySchema
    regions: list[RegionSchema]
    material: MaterialSchema
    load_cases: list[LoadCaseSchema]
    mesh: MeshSchema
    objectives: list[ObjectiveSchema]
    constraints: list[ConstraintSchema] = Field(default_factory=list)
    stress_evaluation: StressSchema = Field(default_factory=StressSchema)
    buckling: BucklingSchema = Field(default_factory=BucklingSchema)
    solver: SolverSchema = Field(default_factory=SolverSchema)
    preferences: PreferenceSchema = Field(default_factory=PreferenceSchema)
    optimisation: OptimisationSchema = Field(default_factory=OptimisationSchema)

    @field_validator("schema_version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Project schema version {value} is newer than this build supports "
                f"({CURRENT_SCHEMA_VERSION}). Upgrade OpenOptima."
            )
        return value

    def to_domain(self) -> Project:
        return Project(
            name=self.name,
            description=self.description,
            unit_system_name=self.unit_system,
            schema_version=self.schema_version,
            geometry=GeometryDefinition(
                provider=self.geometry.provider,
                template=self.geometry.template,
                source=self.geometry.source,
                parameters=dict(self.geometry.parameters),
            ),
            design_space=DesignSpace(
                tuple(variable.to_domain() for variable in self.geometry.variables)
            ),
            regions=tuple(region.to_domain() for region in self.regions),
            material=self.material.to_domain(),
            load_cases=tuple(case.to_domain() for case in self.load_cases),
            mesh=self.mesh.to_domain(),
            objectives=tuple(objective.to_domain() for objective in self.objectives),
            constraints=tuple(constraint.to_domain() for constraint in self.constraints),
            stress_evaluation=self.stress_evaluation.to_domain(),
            buckling=self.buckling.to_domain(),
            solver=self.solver.to_domain(),
            preferences=self.preferences.to_domain(),
            optimisation=self.optimisation.to_domain(),
        )
