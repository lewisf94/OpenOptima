"""CalculiX solver adapter."""

from .dat import ReactionTotal, parse_dat
from .deck import DeckArtifact, write_deck
from .frd import ResultBlock, blocks_named, parse_frd
from .runner import SolverRun, find_executable, run_calculix, solver_version
from .solver import CalculiXSolver

__all__ = [
    "CalculiXSolver",
    "DeckArtifact",
    "ReactionTotal",
    "ResultBlock",
    "SolverRun",
    "blocks_named",
    "find_executable",
    "parse_dat",
    "parse_frd",
    "run_calculix",
    "solver_version",
    "write_deck",
]
