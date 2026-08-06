"""Structural solver adapters."""

from __future__ import annotations

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.model import SolverSpecification
from .analytic import AnalyticSolver
from .base import AnalysisResults, LoadCaseFields, StructuralSolver, von_mises_from_tensor
from .calculix import CalculiXSolver


def create_solver(specification: SolverSpecification) -> StructuralSolver:
    if specification.name == "calculix":
        return CalculiXSolver(specification)
    if specification.name == "analytic":
        return AnalyticSolver(specification)
    raise EvaluationFailure(
        FailureCode.INTERNAL_ERROR,
        f"Unknown solver {specification.name!r}; expected 'calculix' or 'analytic'",
    )


__all__ = [
    "AnalysisResults",
    "AnalyticSolver",
    "CalculiXSolver",
    "LoadCaseFields",
    "StructuralSolver",
    "create_solver",
    "von_mises_from_tensor",
]
