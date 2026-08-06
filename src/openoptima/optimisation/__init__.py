"""Design of experiments, optimisation and decision support."""

from .pareto import (
    MarginalRate,
    apply_trade_rules,
    distance_to_targets,
    knee_point,
    marginal_rates,
    non_dominated_mask,
    pareto_front,
    rank_by_preference,
    summarise_front,
)
from .study import StudyResult, run_doe, run_optimisation, write_study_json

__all__ = [
    "MarginalRate",
    "StudyResult",
    "apply_trade_rules",
    "distance_to_targets",
    "knee_point",
    "marginal_rates",
    "non_dominated_mask",
    "pareto_front",
    "rank_by_preference",
    "run_doe",
    "run_optimisation",
    "summarise_front",
    "write_study_json",
]
