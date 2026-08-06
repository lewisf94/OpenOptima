"""CalculiX solver adapter."""

from .dat import BucklingTable, ReactionTotal, parse_buckling, parse_dat
from .deck import DeckArtifact, write_deck
from .frd import ResultBlock, blocks_named, parse_frd
from .runner import (
    WINDOWS,
    SolverRun,
    find_executable,
    installation_hint,
    run_calculix,
    solver_version,
)
from .solver import CalculiXSolver

__all__ = [
    "WINDOWS",
    "BucklingTable",
    "CalculiXSolver",
    "DeckArtifact",
    "ReactionTotal",
    "ResultBlock",
    "SolverRun",
    "blocks_named",
    "find_executable",
    "installation_hint",
    "parse_buckling",
    "parse_dat",
    "parse_frd",
    "run_calculix",
    "solver_version",
    "write_deck",
]
