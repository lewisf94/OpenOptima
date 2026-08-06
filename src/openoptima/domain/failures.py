"""Evaluation states and the failure taxonomy.

The single most important distinction in this codebase:

``INFEASIBLE``
    The design itself is bad.  A wall went to zero thickness, a rib collided
    with a hole, a stress limit was exceeded.  This is *information* for the
    optimiser and must be fed back to it as a constraint violation.

``ERROR``
    We could not find out whether the design is good.  The solver crashed, the
    disk filled, a worker was killed.  This says nothing about the design and
    must **never** be presented to the optimiser as a poor result — otherwise
    the search learns to avoid whatever region of the design space happened to
    coincide with an infrastructure problem.

Everything downstream keys off this split, so it lives in the domain layer with
no dependencies.
"""

from __future__ import annotations

from enum import Enum


class EvaluationState(str, Enum):
    """Ordered stages every design passes through."""

    CREATED = "created"
    GEOMETRY_GENERATED = "geometry_generated"
    REGIONS_RESOLVED = "regions_resolved"
    GEOMETRY_VALIDATED = "geometry_validated"
    MESH_GENERATED = "mesh_generated"
    MESH_VALIDATED = "mesh_validated"
    SOLVER_INPUT_WRITTEN = "solver_input_written"
    SOLVED = "solved"
    RESULTS_PARSED = "results_parsed"
    CHECKS_COMPLETE = "checks_complete"
    ACCEPTED = "accepted"

    @property
    def order(self) -> int:
        return _STATE_ORDER[self]


_STATE_ORDER: dict[EvaluationState, int] = {
    state: index for index, state in enumerate(EvaluationState)
}


class Outcome(str, Enum):
    """How an evaluation finished."""

    OK = "ok"
    #: The design violates a genuine geometric or engineering constraint.
    INFEASIBLE = "infeasible"
    #: We could not determine the result.  Retryable; not a statement about the design.
    ERROR = "error"


class FailureCode(str, Enum):
    """Specific reason an evaluation did not reach ``ACCEPTED``."""

    # --- Infeasible designs -------------------------------------------------
    INVALID_DESIGN_VARIABLES = "invalid_design_variables"
    GEOMETRY_RECOMPUTE_FAILED = "geometry_recompute_failed"
    INVALID_SOLID = "invalid_solid"
    MANUFACTURING_RULE_VIOLATED = "manufacturing_rule_violated"
    PACKAGING_VIOLATED = "packaging_violated"
    ENGINEERING_CONSTRAINT_FAILED = "engineering_constraint_failed"

    # --- Infrastructure errors ---------------------------------------------
    REGION_NOT_FOUND = "region_not_found"
    REGION_AMBIGUOUS = "region_ambiguous"
    MESH_GENERATION_FAILED = "mesh_generation_failed"
    MESH_QUALITY_FAILED = "mesh_quality_failed"
    SOLVER_NOT_FOUND = "solver_not_found"
    SOLVER_TIMEOUT = "solver_timeout"
    SOLVER_NONCONVERGENCE = "solver_nonconvergence"
    SOLVER_CRASH = "solver_crash"
    RESULT_FILE_MISSING = "result_file_missing"
    RESULT_PARSE_FAILED = "result_parse_failed"
    WORKER_CRASH = "worker_crash"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


#: Codes that describe the *design*.  Everything else is an infrastructure error.
INFEASIBLE_CODES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.INVALID_DESIGN_VARIABLES,
        FailureCode.GEOMETRY_RECOMPUTE_FAILED,
        FailureCode.INVALID_SOLID,
        FailureCode.MANUFACTURING_RULE_VIOLATED,
        FailureCode.PACKAGING_VIOLATED,
        FailureCode.ENGINEERING_CONSTRAINT_FAILED,
    }
)

#: Errors worth retrying: transient, resource-related, or environment-related.
RETRYABLE_CODES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.SOLVER_TIMEOUT,
        FailureCode.SOLVER_CRASH,
        FailureCode.WORKER_CRASH,
        FailureCode.RESULT_FILE_MISSING,
    }
)


def outcome_for(code: FailureCode | None) -> Outcome:
    if code is None:
        return Outcome.OK
    return Outcome.INFEASIBLE if code in INFEASIBLE_CODES else Outcome.ERROR


def is_retryable(code: FailureCode | None) -> bool:
    return code is not None and code in RETRYABLE_CODES


class EvaluationFailure(Exception):
    """Raised inside the pipeline to abort an evaluation with a classified reason."""

    def __init__(
        self,
        code: FailureCode,
        message: str,
        *,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    @property
    def outcome(self) -> Outcome:
        return outcome_for(self.code)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code.value}] {self.message}"
