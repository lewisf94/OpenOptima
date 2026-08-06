"""Study drivers: DOE and multi-objective optimisation runs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..doe.sampling import include_corners, sample_design_space
from ..doe.sensitivity import SensitivityReport, analyse, failure_summary
from ..domain.project import Project
from ..domain.results import EvaluationResult
from ..evaluation.evaluator import Evaluator
from .pareto import pareto_front, summarise_front


@dataclass
class StudyResult:
    name: str
    kind: str
    results: list[EvaluationResult] = field(default_factory=list)
    front: list[EvaluationResult] = field(default_factory=list)
    sensitivity: dict[str, SensitivityReport] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    wall_time: float = 0.0
    error_count: int = 0

    @property
    def feasible(self) -> list[EvaluationResult]:
        return [r for r in self.results if r.ok and r.feasible]

    @property
    def infeasible(self) -> list[EvaluationResult]:
        return [r for r in self.results if r.outcome.value == "infeasible"]

    @property
    def errors(self) -> list[EvaluationResult]:
        return [r for r in self.results if r.outcome.value == "error"]

    def summary(self) -> dict[str, object]:
        return {
            "study": self.name,
            "kind": self.kind,
            "evaluated": len(self.results),
            "feasible": len(self.feasible),
            "infeasible": len(self.infeasible),
            "errors": len(self.errors),
            "pareto_size": len(self.front),
            "wall_time_s": round(self.wall_time, 2),
            "failure_breakdown": self.failures,
        }


def run_doe(
    project: Project,
    evaluator: Evaluator,
    *,
    evaluations: int | None = None,
    method: str | None = None,
    seed: int | None = None,
    with_corners: bool = True,
    progress=None,
) -> StudyResult:
    """Space-filling exploration of the design space."""
    settings = project.optimisation.initial_sampling
    count = evaluations if evaluations is not None else settings.evaluations
    sampling_method = method or settings.method
    sampling_seed = seed if seed is not None else settings.seed

    designs = sample_design_space(project.design_space, sampling_method, count, seed=sampling_seed)
    if with_corners:
        # Extremes first: if a region selector is going to fail anywhere it is
        # usually at the edge of the design range, and finding that out after
        # 200 evaluations is a waste of an afternoon.
        designs = include_corners(project.design_space) + designs

    started = time.monotonic()
    evaluator.store.start_study(
        evaluator.study,
        "doe",
        {"method": sampling_method, "evaluations": count, "seed": sampling_seed},
    )
    results = evaluator.evaluate_many(
        designs, jobs=project.optimisation.parallel_jobs, on_result=progress
    )
    evaluator.store.finish_study(evaluator.study)

    metrics = list(
        dict.fromkeys(
            [o.metric for o in project.objectives] + [c.metric for c in project.constraints]
        )
    )
    return StudyResult(
        name=evaluator.study,
        kind="doe",
        results=results,
        front=pareto_front(results, project.objectives),
        sensitivity=analyse(results, project.design_space, metrics),
        failures=failure_summary(results),
        wall_time=time.monotonic() - started,
    )


def run_optimisation(
    project: Project,
    evaluator: Evaluator,
    *,
    budget: int | None = None,
    population: int | None = None,
    seed: int | None = None,
    seed_with_doe: bool = True,
    progress=None,
) -> StudyResult:
    """NSGA-II multi-objective optimisation.

    The DOE stage is run first by default and its results seed the initial
    population, which is both cheaper and more reliable than letting NSGA-II
    discover the feasible region from a random start.
    """
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.operators.sampling.lhs import LHS
    from pymoo.optimize import minimize

    from .problem import StructuralProblem

    algorithm_settings = project.optimisation.algorithm
    total_budget = budget if budget is not None else algorithm_settings.evaluation_budget
    population_size = population if population is not None else algorithm_settings.population_size
    random_seed = seed if seed is not None else algorithm_settings.seed

    started = time.monotonic()
    evaluator.store.start_study(
        evaluator.study,
        "optimisation",
        {
            "algorithm": algorithm_settings.name,
            "population": population_size,
            "budget": total_budget,
            "seed": random_seed,
        },
    )

    prior: list[EvaluationResult] = []
    if seed_with_doe:
        doe = run_doe(
            project,
            evaluator,
            evaluations=min(
                project.optimisation.initial_sampling.evaluations,
                max(4, total_budget // 4),
            ),
            progress=progress,
        )
        prior = doe.results

    remaining = max(population_size, total_budget - len(prior))
    generations = max(1, remaining // population_size)

    problem = StructuralProblem(
        project.design_space,
        project.objectives,
        project.constraints,
        evaluate_batch=lambda designs, jobs=0: evaluator.evaluate_many(
            designs, jobs=jobs, on_result=progress
        ),
        jobs=project.optimisation.parallel_jobs,
    )

    algorithm = NSGA2(pop_size=population_size, sampling=LHS(), eliminate_duplicates=True)
    minimize(
        problem,
        algorithm,
        ("n_gen", generations),
        seed=random_seed,
        verbose=False,
        save_history=False,
    )
    evaluator.store.finish_study(evaluator.study)

    all_results = prior + problem.history
    metrics = list(
        dict.fromkeys(
            [o.metric for o in project.objectives] + [c.metric for c in project.constraints]
        )
    )
    return StudyResult(
        name=evaluator.study,
        kind="optimisation",
        results=all_results,
        front=pareto_front(all_results, project.objectives),
        sensitivity=analyse(all_results, project.design_space, metrics),
        failures=failure_summary(all_results),
        wall_time=time.monotonic() - started,
        error_count=problem.error_count,
    )


def write_study_json(study: StudyResult, project: Project, path: Path) -> Path:
    payload = {
        "summary": study.summary(),
        "pareto": summarise_front(study.front, project.objectives, project.preferences),
        "front": [r.to_dict() for r in study.front],
        "sensitivity": {
            metric: [
                {
                    "variable": effect.variable,
                    "spearman": effect.spearman,
                    "p_value": effect.p_value,
                    "linear_r2": effect.linear_r2,
                    "span_effect": effect.span_effect,
                }
                for effect in report.ranked
            ]
            for metric, report in study.sensitivity.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
