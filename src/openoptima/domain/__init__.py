"""Pure domain layer.

Nothing in this package may import gmsh, pymoo, a solver, a database driver or
any other external tool.  It is plain Python data and rules, so it can be
imported and tested in milliseconds and reasoned about without a CAE stack
installed.  ``tests/unit/test_architecture.py`` enforces this.
"""

from .failures import (
    INFEASIBLE_CODES,
    RETRYABLE_CODES,
    EvaluationFailure,
    EvaluationState,
    FailureCode,
    Outcome,
    is_retryable,
    outcome_for,
)
from .model import (
    AnalysisModel,
    BoundaryCondition,
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
from .objectives import (
    Constraint,
    Direction,
    MetricPreference,
    Objective,
    Operator,
    PreferenceModel,
    TradeRule,
)
from .project import (
    CURRENT_SCHEMA_VERSION,
    AlgorithmSettings,
    GeometryDefinition,
    OptimisationSettings,
    Project,
    SamplingSettings,
)
from .regions import (
    BoundingBox,
    FaceSignature,
    RegionMap,
    RegionMatch,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from .results import EvaluationResult, LoadCaseResult, MeshSummary
from .units import MM_N_MPA_T, UnitSystem, density_kg_m3_to_internal, get_unit_system
from .variables import (
    ActivationRule,
    DesignSpace,
    DesignVariable,
    DesignVector,
    VariableType,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "INFEASIBLE_CODES",
    "MM_N_MPA_T",
    "RETRYABLE_CODES",
    "ActivationRule",
    "AlgorithmSettings",
    "AnalysisModel",
    "BoundaryCondition",
    "BoundingBox",
    "Constraint",
    "ConstraintKind",
    "DesignSpace",
    "DesignVariable",
    "DesignVector",
    "Direction",
    "EvaluationFailure",
    "EvaluationResult",
    "EvaluationState",
    "FaceSignature",
    "FailureCode",
    "GeometryDefinition",
    "Load",
    "LoadCase",
    "LoadCaseResult",
    "LoadKind",
    "LocalRefinement",
    "Material",
    "MeshAlgorithm",
    "MeshSpecification",
    "MeshSummary",
    "MetricPreference",
    "Objective",
    "Operator",
    "OptimisationSettings",
    "Outcome",
    "PreferenceModel",
    "Project",
    "RegionMap",
    "RegionMatch",
    "RegionSelector",
    "SamplingSettings",
    "SelectionMode",
    "SemanticRegion",
    "SolverSpecification",
    "StressEvaluation",
    "SurfaceType",
    "TradeRule",
    "UnitSystem",
    "VariableType",
    "density_kg_m3_to_internal",
    "get_unit_system",
    "is_retryable",
    "outcome_for",
]
