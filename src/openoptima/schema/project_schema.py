"""Validated, versioned on-disk project format.

Pydantic models here mirror the YAML file and exist purely to give the user a
precise error message with a path ("load_cases.0.loads.1.region: field
required") instead of a ``KeyError`` from deep inside the pipeline.  They are
converted straight into the frozen domain objects and then discarded.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.carried import CarriedShape, CarriedSize
from ..domain.failure_criteria import criterion_for
from ..domain.features import EdgeFeature, FeatureKind
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
    ModalSettings,
    PointMass,
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
from ..domain.orthotropic import (
    BUILD_AXES,
    DirectionalStrength,
    InadmissibleMaterial,
    OrthotropicMaterial,
)
from ..domain.printing import (
    DEFAULT_OVERHANG_ANGLE_DEG,
    BuildVolume,
    PrintingSettings,
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


class FeatureSchema(Strict):
    """A rounded or cut-back corner OpenOptima adds on top of the shape."""

    name: str
    kind: Literal["fillet", "chamfer"]
    #: The two region names whose shared edges the feature is applied to.
    between: tuple[str, str]
    #: Millimetres, or the id of a design variable that supplies the number.
    size: float | str
    description: str = ""

    def to_domain(self) -> EdgeFeature:
        return EdgeFeature(
            name=self.name,
            kind=FeatureKind(self.kind),
            between=self.between,
            size=self.size,
            description=self.description,
        )


class GeometrySchema(Strict):
    provider: Literal["occ", "cadquery", "step"] = "occ"
    template: str = ""
    source: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    variables: list[VariableSchema] = Field(default_factory=list)
    features: list[FeatureSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_source(self) -> GeometrySchema:
        if self.provider == "occ" and not self.template:
            raise ValueError("geometry.template is required for the 'occ' provider")
        if self.provider == "cadquery" and not self.source:
            raise ValueError("geometry.source is required for the 'cadquery' provider")
        if self.provider == "step" and not self.source:
            raise ValueError(
                "geometry.source is required for the 'step' provider -- the path "
                "to the CAD file to import, e.g. a STEP export from SolidWorks"
            )
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
    #: Smallest area this region may shrink to before the design is infeasible.
    #: See ``domain/regions.py::SemanticRegion`` for the measured failure that
    #: makes this worth setting when a feature can eat into the face.
    min_area_mm2: float | None = None

    def to_domain(self) -> SemanticRegion:
        return SemanticRegion(
            name=self.name,
            selector=self.selector.to_domain(),
            description=self.description,
            min_area_mm2=self.min_area_mm2,
        )


class PrintedStrengthSchema(Strict):
    """How strong a printed part is in each direction.

    Every one of these is a **design decision**, exactly like
    ``allowable_stress_mpa`` on an ordinary material. They are not properties
    of the plastic on the spool: they depend on the print settings, the
    infill, the temperature the part runs at and how much margin the engineer
    wants. OpenOptima will not infer any of them.

    "Along the layers" is the strong direction. "Through the layers" is the
    weak one -- the direction that pulls the fused layers apart, and usually
    the one that decides the design.
    """

    along_layers_tension_mpa: float
    through_layers_tension_mpa: float
    along_layers_compression_mpa: float
    through_layers_compression_mpa: float
    #: Shear within one layer.
    in_plane_shear_mpa: float
    #: Shear that slides one layer across the next.
    through_layers_shear_mpa: float
    basis: str = "unspecified"

    def to_domain(self) -> DirectionalStrength:
        # Axes 1 and 2 lie in the layer plane, axis 3 is through the layers.
        # DirectionalStrength orders shear on planes 23, 13, 12 -- so the two
        # through-layer planes come first and the in-plane one last. Getting
        # this order wrong would put the weak interlayer shear strength on the
        # strong plane, which no test of a pure tension case would catch.
        return DirectionalStrength(
            tension=(
                self.along_layers_tension_mpa,
                self.along_layers_tension_mpa,
                self.through_layers_tension_mpa,
            ),
            compression=(
                self.along_layers_compression_mpa,
                self.along_layers_compression_mpa,
                self.through_layers_compression_mpa,
            ),
            shear=(
                self.through_layers_shear_mpa,
                self.through_layers_shear_mpa,
                self.in_plane_shear_mpa,
            ),
            basis=self.basis,
        )


class PrintedSchema(Strict):
    """A 3D-printed material: stiffer and stronger along its layers than through them.

    Only five stiffness numbers are asked for, not the nine a fully
    directional material needs. Within one layer a print is treated as the
    same in every direction, and only the build direction differs. That is
    almost always what a printed part actually needs, and it means the two
    in-plane axes can point anywhere in the layer plane -- so the only
    direction you have to state is the one the layers stack along.
    """

    #: Which way the layers stack, in the model's own coordinates. This is the
    #: weak direction. A part printed flat on the bed stacks upward: (0, 0, 1).
    #:
    #: May instead name a categorical design variable, in which case the
    #: optimiser chooses how to print the part. That variable's choices must be
    #: axis names -- ``x``, ``y`` or ``z``, the axis the layers stack along.
    build_direction: Vector3 | str = (0.0, 0.0, 1.0)
    along_layers_modulus_mpa: float
    through_layers_modulus_mpa: float
    in_plane_poisson: float
    through_layers_poisson: float
    through_layers_shear_modulus_mpa: float
    #: Without strengths OpenOptima still computes stress and deflection, but
    #: refuses to report a factor of safety rather than computing one from von
    #: Mises stress, which assumes equal strength in every direction.
    strength: PrintedStrengthSchema | None = None

    def to_domain(
        self,
        *,
        name: str,
        density_kg_m3: float,
        design_space: DesignSpace | None = None,
    ) -> OrthotropicMaterial:
        return OrthotropicMaterial.transversely_isotropic(
            name=name,
            in_plane_modulus_mpa=self.along_layers_modulus_mpa,
            through_layer_modulus_mpa=self.through_layers_modulus_mpa,
            in_plane_poisson=self.in_plane_poisson,
            through_layer_poisson=self.through_layers_poisson,
            through_layer_shear_mpa=self.through_layers_shear_modulus_mpa,
            density_kg_m3=density_kg_m3,
            # A material must always hold a real direction: `stiffness_matrix`
            # and `local_axes` need one, and making them cope with a variable
            # name would spread the idea through arithmetic that has no
            # business knowing about design variables. Where the optimiser
            # chooses, this holds the first choice and `Project.material_for`
            # substitutes what the design actually picked.
            build_direction=self.resolved_build_direction(design_space),
            strength=self.strength.to_domain() if self.strength else None,
        )

    @property
    def direction_variable(self) -> str | None:
        """The design variable choosing the print direction, if any."""
        return self.build_direction if isinstance(self.build_direction, str) else None

    def resolved_build_direction(self, design_space: DesignSpace | None) -> Vector3:
        if not isinstance(self.build_direction, str):
            return self.build_direction
        variable = next(
            (v for v in (design_space or ()) if v.id == self.build_direction),
            None,
        )
        if variable is None or not variable.choices:
            # Project.__post_init__ reports this properly, with the list of
            # variables that do exist. Fall back to something valid so the
            # material can be built and that message is the one the user sees.
            return (0.0, 0.0, 1.0)
        first = str(variable.effective_default())
        return BUILD_AXES.get(first, (0.0, 0.0, 1.0))


class MaterialSchema(Strict):
    """Either an ordinary material or a printed one, never both.

    An ordinary material is equally strong in every direction and is described
    by one modulus, one Poisson ratio and one allowable stress. A printed part
    is not like that, so it is described under ``printed:`` instead and has no
    single allowable stress to give.
    """

    name: str
    density_kg_m3: float

    # -- an ordinary material, equally strong in every direction -------------
    elastic_modulus_mpa: float | None = None
    poisson_ratio: float | None = None
    allowable_stress_mpa: float | None = None
    allowable_stress_basis: str = "unspecified"

    # -- or a printed one ----------------------------------------------------
    printed: PrintedSchema | None = None
    #: Which failure criterion measures a printed material against its
    #: strengths. Meaningless for an ordinary material, and refused there
    #: rather than silently ignored.
    failure_criterion: Literal["hoffman", "max_stress"] = "hoffman"

    _ISOTROPIC_FIELDS = ("elastic_modulus_mpa", "poisson_ratio", "allowable_stress_mpa")

    @model_validator(mode="after")
    def _check_one_kind_of_material(self) -> MaterialSchema:
        given = [name for name in self._ISOTROPIC_FIELDS if getattr(self, name) is not None]

        if self.printed is None:
            if not given:
                raise ValueError(
                    "material needs either elastic_modulus_mpa, poisson_ratio and "
                    "allowable_stress_mpa for an ordinary material, or a `printed:` "
                    "block for a 3D-printed one."
                )
            missing = [name for name in self._ISOTROPIC_FIELDS if getattr(self, name) is None]
            if missing:
                raise ValueError(
                    f"material is missing {', '.join(missing)}. An ordinary material "
                    f"needs all three of {', '.join(self._ISOTROPIC_FIELDS)}."
                )
            if "failure_criterion" in self.model_fields_set:
                raise ValueError(
                    "failure_criterion applies only to a printed material. An "
                    "ordinary material is measured against its allowable_stress_mpa."
                )
            return self

        if given:
            raise ValueError(
                f"material gives both a `printed:` block and {', '.join(given)}. A "
                f"printed part is stronger along its layers than through them, so it "
                f"has no single modulus or allowable stress. Give one or the other."
            )

        # Refuse an impossible criterion *now*, at load time, rather than after
        # a full solve. Hoffman cannot describe a material whose weakest
        # direction is under half its strongest, and printed plastics are
        # routinely on the wrong side of that line -- so this is the common
        # case, not an exotic one. Catching it here turns a wasted optimisation
        # run into an error the moment the file is read.
        if self.printed.strength is not None and self.failure_criterion == "hoffman":
            try:
                criterion_for("hoffman", self.printed.strength.to_domain())
            except InadmissibleMaterial as exc:
                raise ValueError(
                    f"{exc} Set `failure_criterion: max_stress` on this material."
                ) from exc
        return self

    @property
    def direction_variable(self) -> str | None:
        return self.printed.direction_variable if self.printed else None

    def to_domain(self, design_space: DesignSpace | None = None) -> Material | OrthotropicMaterial:
        if self.printed is not None:
            return self.printed.to_domain(
                name=self.name,
                density_kg_m3=self.density_kg_m3,
                design_space=design_space,
            )
        assert self.elastic_modulus_mpa is not None  # narrowed by the validator
        assert self.poisson_ratio is not None
        assert self.allowable_stress_mpa is not None
        return Material.from_engineering_units(
            name=self.name,
            elastic_modulus_mpa=self.elastic_modulus_mpa,
            poisson_ratio=self.poisson_ratio,
            density_kg_m3=self.density_kg_m3,
            allowable_stress_mpa=self.allowable_stress_mpa,
            allowable_stress_basis=self.allowable_stress_basis,
        )


class CarriedSizeSchema(Strict):
    """How big a carried item is, so it can be put where it really sits.

    ``height_mm`` is measured straight up off the face it bolts to.
    ``across_mm`` and ``depth_mm`` are the two directions in the plane of that
    face; a cylinder uses ``across_mm`` as its diameter and ignores
    ``depth_mm``.

    The item is treated as a uniform solid of this size, so its middle sits at
    half the height unless ``centre_height_mm`` says otherwise. That is not
    guaranteed to be on the safe side: a motor with a propeller on top has its
    middle higher, and the real frequency is lower than the reported one.
    """

    shape: Literal["cylinder", "box"]
    across_mm: float
    depth_mm: float = 0.0
    height_mm: float
    centre_height_mm: float | None = None

    @model_validator(mode="after")
    def _a_box_needs_a_depth(self) -> CarriedSizeSchema:
        if self.shape == "box" and self.depth_mm <= 0.0:
            raise ValueError(
                "A box-shaped carried item needs a depth_mm as well as an "
                "across_mm. Use shape: cylinder if it is round."
            )
        return self

    def to_domain(self) -> CarriedSize:
        return CarriedSize(
            shape=CarriedShape(self.shape),
            across=self.across_mm,
            deep=self.depth_mm,
            height=self.height_mm,
            centre_height=self.centre_height_mm,
        )


class PointMassSchema(Strict):
    """Something heavy the part carries but is not made of.

    A motor on the end of an arm, a camera on a mount, a battery on a tray.
    It attaches to the face named by ``region``, and it adds mass and weight
    but no stiffness.

    This matters most for natural frequency, where the carried thing is
    usually most of the mass.

    **Give it a ``size`` if you know one.** Without one it is treated as flat
    in the face: no height above it, and no resistance to being turned. Both
    make the reported frequency higher than the real one. Measured on
    ``examples/drone_arm``, a 35 g motor 28 mm across and 32 mm tall reads
    169.8 Hz flat and 165.9 Hz where it really sits, across a 170 Hz limit.
    """

    name: str
    region: str
    mass_kg: float
    description: str = ""
    size: CarriedSizeSchema | None = None

    def to_domain(self) -> PointMass:
        return PointMass.from_engineering_units(
            name=self.name,
            region=self.region,
            mass_kg=self.mass_kg,
            description=self.description,
            size=None if self.size is None else self.size.to_domain(),
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


class ModalSchema(Strict):
    """Natural frequency settings: the rates the part likes to vibrate at.

    Off by default because it costs an extra eigenvalue solve for each distinct
    set of supports. Turn it on when something drives the part at a known rate
    -- a motor, a propeller, a pump -- because a static analysis cannot see
    that failure at all.

    Constrain the result like any other metric::

        constraints:
          - metric: natural_frequency_hz
            operator: greater_than_or_equal
            value: 300.0
    """

    enabled: bool = False
    modes: int = 6

    def to_domain(self) -> ModalSettings:
        return ModalSettings(enabled=self.enabled, modes=self.modes)


class BuildVolumeSchema(Strict):
    """The printer's usable space, in millimetres."""

    width_mm: float
    depth_mm: float
    height_mm: float

    def to_domain(self) -> BuildVolume:
        return BuildVolume(width=self.width_mm, depth=self.depth_mm, height=self.height_mm)


class PrintingSchema(Strict):
    """Whether the part can be printed, and what support it would need.

    Produces metrics you may constrain or trade against, exactly like mass::

        printing:
          enabled: true
          overhang_angle_deg: 45.0
          build_volume: { width_mm: 220, depth_mm: 220, height_mm: 250 }

        constraints:
          - metric: build_volume_overflow_mm
            operator: less_than_or_equal
            value: 0.0

    It is never a gate. A design needing support is more work, not wrong, and
    how much performance to give up avoiding one is the engineer's call.
    """

    enabled: bool = False
    overhang_angle_deg: float = DEFAULT_OVERHANG_ANGLE_DEG
    build_volume: BuildVolumeSchema | None = None

    def to_domain(self) -> PrintingSettings:
        return PrintingSettings(
            enabled=self.enabled,
            overhang_angle_deg=self.overhang_angle_deg,
            build_volume=self.build_volume.to_domain() if self.build_volume else None,
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
    point_masses: list[PointMassSchema] = Field(default_factory=list)
    load_cases: list[LoadCaseSchema]
    mesh: MeshSchema
    objectives: list[ObjectiveSchema]
    constraints: list[ConstraintSchema] = Field(default_factory=list)
    stress_evaluation: StressSchema = Field(default_factory=StressSchema)
    buckling: BucklingSchema = Field(default_factory=BucklingSchema)
    modal: ModalSchema = Field(default_factory=ModalSchema)
    printing: PrintingSchema = Field(default_factory=PrintingSchema)
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

    @model_validator(mode="after")
    def _check_features(self) -> ProjectSchema:
        """Catch a feature that names something that does not exist.

        A typo here is worth ten seconds at load time rather than a failure
        part way through a study. The size check matters most: ``size:
        corner_radius`` with no variable of that name is not a small mistake,
        it is a project that cannot build at all.
        """
        if not self.geometry.features:
            return self
        region_names = {region.name for region in self.regions}
        value_names = set(self.geometry.parameters) | {
            variable.id for variable in self.geometry.variables
        }
        seen: set[str] = set()
        for feature in self.geometry.features:
            if feature.name in seen:
                raise ValueError(f"two features are both named {feature.name!r}")
            seen.add(feature.name)
            for name in feature.between:
                if name not in region_names:
                    known = ", ".join(sorted(region_names)) or "<none>"
                    raise ValueError(
                        f"feature {feature.name!r} sits between {feature.between[0]!r} "
                        f"and {feature.between[1]!r}, but no region is named {name!r}. "
                        f"Regions in this project: {known}"
                    )
            if feature.between[0] == feature.between[1]:
                raise ValueError(
                    f"feature {feature.name!r} names {feature.between[0]!r} on both "
                    f"sides. A corner lies between two different faces."
                )
            if isinstance(feature.size, str):
                if feature.size not in value_names:
                    known = ", ".join(sorted(value_names)) or "<none>"
                    raise ValueError(
                        f"feature {feature.name!r} takes its size from {feature.size!r}, "
                        f"which is not a design variable or a fixed parameter in this "
                        f"project. Available: {known}"
                    )
            elif feature.size <= 0.0:
                # A fixed size of zero is never valid, at any design point, so
                # it belongs here rather than in the build. A design *variable*
                # that can reach zero is a different matter -- that depends on
                # the design and is refused per evaluation, as infeasible.
                raise ValueError(
                    f"feature {feature.name!r} has a size of {feature.size:g} mm. "
                    f"{FeatureKind(feature.kind).size_meaning.capitalize()} must be "
                    f"greater than zero."
                )
        return self

    def to_domain(self) -> Project:
        design_space = DesignSpace(
            tuple(variable.to_domain() for variable in self.geometry.variables)
        )
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
                features=tuple(feature.to_domain() for feature in self.geometry.features),
            ),
            design_space=design_space,
            regions=tuple(region.to_domain() for region in self.regions),
            material=self.material.to_domain(design_space),
            failure_criterion=self.material.failure_criterion,
            build_direction_variable=self.material.direction_variable,
            point_masses=tuple(mass.to_domain() for mass in self.point_masses),
            load_cases=tuple(case.to_domain() for case in self.load_cases),
            mesh=self.mesh.to_domain(),
            objectives=tuple(objective.to_domain() for objective in self.objectives),
            constraints=tuple(constraint.to_domain() for constraint in self.constraints),
            stress_evaluation=self.stress_evaluation.to_domain(),
            buckling=self.buckling.to_domain(),
            modal=self.modal.to_domain(),
            printing=self.printing.to_domain(),
            solver=self.solver.to_domain(),
            preferences=self.preferences.to_domain(),
            optimisation=self.optimisation.to_domain(),
        )
