"""Materials, loads, mesh settings and the analysis model handed to a solver."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .units import density_kg_m3_to_internal


@dataclass(frozen=True)
class Material:
    """Isotropic linear-elastic material, stored in internal units.

    ``allowable_stress`` is deliberately mandatory and deliberately *not*
    derived from a material name.  It depends on yield or ultimate strength,
    load uncertainty, temperature, process and the design code in force — none
    of which the software can infer.
    """

    name: str
    elastic_modulus: float  # MPa
    poisson_ratio: float
    density: float  # t/mm^3
    allowable_stress: float  # MPa
    allowable_stress_basis: str = "unspecified"

    def __post_init__(self) -> None:
        if self.elastic_modulus <= 0:
            raise ValueError(f"Material {self.name!r}: elastic modulus must be positive")
        if not -1.0 < self.poisson_ratio < 0.5:
            raise ValueError(
                f"Material {self.name!r}: Poisson ratio {self.poisson_ratio} is outside (-1, 0.5)"
            )
        if self.density <= 0:
            raise ValueError(f"Material {self.name!r}: density must be positive")
        if self.allowable_stress <= 0:
            raise ValueError(f"Material {self.name!r}: allowable stress must be positive")

    @classmethod
    def from_engineering_units(
        cls,
        *,
        name: str,
        elastic_modulus_mpa: float,
        poisson_ratio: float,
        density_kg_m3: float,
        allowable_stress_mpa: float,
        allowable_stress_basis: str = "unspecified",
    ) -> Material:
        return cls(
            name=name,
            elastic_modulus=elastic_modulus_mpa,
            poisson_ratio=poisson_ratio,
            density=density_kg_m3_to_internal(density_kg_m3),
            allowable_stress=allowable_stress_mpa,
            allowable_stress_basis=allowable_stress_basis,
        )

    @property
    def density_kg_m3(self) -> float:
        return self.density / 1.0e-12


class ConstraintKind(str, Enum):
    FIXED = "fixed"
    PRESCRIBED_DISPLACEMENT = "prescribed_displacement"


@dataclass(frozen=True)
class BoundaryCondition:
    """Displacement constraint applied to a named region."""

    region: str
    kind: ConstraintKind = ConstraintKind.FIXED
    #: Degrees of freedom to restrain, 1-based as CalculiX expects (1=x, 2=y, 3=z).
    dofs: tuple[int, ...] = (1, 2, 3)
    magnitude: float = 0.0

    def __post_init__(self) -> None:
        for dof in self.dofs:
            if dof not in (1, 2, 3):
                raise ValueError(f"Unsupported degree of freedom {dof}; expected 1, 2 or 3")


class LoadKind(str, Enum):
    #: Total force vector spread over the region as a consistent nodal load.
    FORCE = "force"
    #: Pressure acting along the inward surface normal.
    PRESSURE = "pressure"
    #: Uniform acceleration field over the whole body (self-weight etc.).
    ACCELERATION = "acceleration"


@dataclass(frozen=True)
class Load:
    kind: LoadKind
    region: str | None = None
    #: Total force in N for FORCE, acceleration in mm/s^2 for ACCELERATION.
    vector: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Pressure in MPa; positive presses onto the surface.
    magnitude: float = 0.0

    def __post_init__(self) -> None:
        if self.kind in (LoadKind.FORCE, LoadKind.PRESSURE) and not self.region:
            raise ValueError(f"{self.kind.value} load needs a region")


@dataclass(frozen=True)
class LoadCase:
    """One independent static analysis.

    Load cases are never averaged.  Metrics are reported per case and the
    governing (worst) case drives constraints.
    """

    id: str
    loads: tuple[Load, ...]
    boundary_conditions: tuple[BoundaryCondition, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.boundary_conditions:
            raise ValueError(f"Load case {self.id!r} has no boundary conditions")
        if not self.loads:
            raise ValueError(f"Load case {self.id!r} has no loads")

    @property
    def regions_used(self) -> tuple[str, ...]:
        names = [bc.region for bc in self.boundary_conditions]
        names += [load.region for load in self.loads if load.region]
        return tuple(dict.fromkeys(names))


class MeshAlgorithm(str, Enum):
    #: Delaunay — gmsh's most robust 3D algorithm. The default.
    DELAUNAY = "delaunay"
    #: HXT — parallel, faster, occasionally less forgiving on poor geometry.
    HXT = "hxt"
    FRONTAL = "frontal"


@dataclass(frozen=True)
class LocalRefinement:
    """Extra refinement near a named region."""

    region: str
    size: float
    distance: float


@dataclass(frozen=True)
class MeshSpecification:
    global_size: float
    minimum_size: float
    element_order: int = 2
    algorithm: MeshAlgorithm = MeshAlgorithm.DELAUNAY
    curvature_refinement: bool = True
    #: Elements per 2*pi of curvature when curvature refinement is on.
    curvature_elements: float = 12.0
    size_from_thickness: bool = True
    optimise: bool = True
    local_refinements: tuple[LocalRefinement, ...] = ()
    #: Quality gates. minSICN below this rejects the mesh.
    min_scaled_jacobian: float = 0.05
    #: Mesh volume must agree with CAD volume to this relative tolerance.
    volume_tolerance: float = 0.02
    max_elements: int = 2_000_000

    def __post_init__(self) -> None:
        if self.element_order not in (1, 2):
            raise ValueError("element_order must be 1 or 2")
        if self.global_size <= 0 or self.minimum_size <= 0:
            raise ValueError("mesh sizes must be positive")
        if self.minimum_size > self.global_size:
            raise ValueError("minimum_size cannot exceed global_size")

    def coarsened(self, factor: float) -> MeshSpecification:
        return MeshSpecification(
            global_size=self.global_size * factor,
            minimum_size=self.minimum_size * factor,
            element_order=self.element_order,
            algorithm=self.algorithm,
            curvature_refinement=self.curvature_refinement,
            curvature_elements=self.curvature_elements,
            size_from_thickness=self.size_from_thickness,
            optimise=self.optimise,
            local_refinements=self.local_refinements,
            min_scaled_jacobian=self.min_scaled_jacobian,
            volume_tolerance=self.volume_tolerance,
            max_elements=self.max_elements,
        )

    def with_overrides(self, **changes: object) -> MeshSpecification:
        current = {
            "global_size": self.global_size,
            "minimum_size": self.minimum_size,
            "element_order": self.element_order,
            "algorithm": self.algorithm,
            "curvature_refinement": self.curvature_refinement,
            "curvature_elements": self.curvature_elements,
            "size_from_thickness": self.size_from_thickness,
            "optimise": self.optimise,
            "local_refinements": self.local_refinements,
            "min_scaled_jacobian": self.min_scaled_jacobian,
            "volume_tolerance": self.volume_tolerance,
            "max_elements": self.max_elements,
        }
        current.update(changes)
        return MeshSpecification(**current)  # type: ignore[arg-type]


@dataclass(frozen=True)
class SolverSpecification:
    name: str = "calculix"
    executable: str | None = None
    timeout_seconds: float = 900.0
    threads: int = 1
    extra_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class StressEvaluation:
    """How a scalar stress number is extracted from a field.

    Raw peak von Mises stress is a poor optimisation target: re-entrant corners
    and point supports are singular, so the "peak" grows without bound as the
    mesh is refined and the optimiser chases mesh artefacts.  The default here
    is a high percentile with user-nominated singular regions excluded, and the
    raw peak is always reported alongside as a warning.
    """

    measure: str = "percentile"  # raw_max | percentile | pnorm | region_max
    percentile: float = 99.0
    pnorm_exponent: float = 8.0
    #: Regions whose faces are excluded from the stress measure (not from the analysis).
    excluded_regions: tuple[str, ...] = ()
    #: Nodes within this distance of an excluded region are dropped.
    exclusion_radius: float = 0.0

    def __post_init__(self) -> None:
        allowed = {"raw_max", "percentile", "pnorm", "region_max"}
        if self.measure not in allowed:
            raise ValueError(f"Unknown stress measure {self.measure!r}; expected one of {allowed}")
        if not 0.0 < self.percentile <= 100.0:
            raise ValueError("percentile must be in (0, 100]")


@dataclass(frozen=True)
class BucklingSettings:
    """Linear (eigenvalue) buckling analysis.

    Why this matters more than it looks: minimising mass under stress and
    displacement limits alone pushes a design towards thin, slender sections —
    which is precisely the geometry that buckles.  A static analysis cannot see
    it at all, so an optimiser will happily hand back a part that passes every
    stress check and folds up under load.

    The result is a *buckling factor*: the multiple of the applied load at which
    the structure becomes unstable.  A factor of 3 means it buckles at three
    times the load you applied.  Constrain it like any other metric::

        constraints:
          - metric: buckling_factor
            operator: greater_than_or_equal
            value: 3.0

    Buckling factors are conventionally required to be well above the stress
    factor of safety, because real parts have imperfections and this analysis
    assumes a perfect one.  How far above is an engineering judgement and the
    software will not make it for you.
    """

    enabled: bool = False
    #: Eigenvalues to extract. The lowest drives the constraint; the others are
    #: reported because a closely spaced pair signals a symmetric structure with
    #: two equally likely buckling directions.
    modes: int = 3
    #: Above this slenderness ratio a solid tetrahedral model cannot be trusted
    #: for buckling, and the evaluation fails rather than returning a number.
    #:
    #: Measured behaviour behind the default: a 20 mm square column (ratio 139)
    #: matched Euler to better than 1% at three different lengths, while a 22 mm
    #: column at ratio 195 and an 8 mm one at ratio 444 were both wrong by a
    #: factor of nine -- in the *unsafe* direction. Raising this limit does not
    #: make the analysis more accurate, it only silences the check.
    slenderness_limit: float = 150.0

    def __post_init__(self) -> None:
        if self.modes < 1:
            raise ValueError("buckling.modes must be at least 1")
        if self.modes > 20:
            raise ValueError("buckling.modes above 20 is rarely useful and is slow")
        if self.slenderness_limit <= 0:
            raise ValueError("buckling.slenderness_limit must be positive")


@dataclass(frozen=True)
class AnalysisModel:
    """Everything a structural solver needs for one design.

    Deliberately free of geometry and mesh *generation* concerns: it references
    a mesh that already exists.
    """

    name: str
    material: Material
    load_cases: tuple[LoadCase, ...]
    stress_evaluation: StressEvaluation = field(default_factory=StressEvaluation)
    element_order: int = 2
    buckling: BucklingSettings = field(default_factory=BucklingSettings)
