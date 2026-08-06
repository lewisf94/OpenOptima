"""pymoo adapter.

The one thing this adapter must get right: an infrastructure error is not a bad
design.  pymoo needs a number for every individual, so a failed evaluation has
to be given one — and if that number looks like "a terrible design", the search
learns to avoid a perfectly good region of the design space because a solver
happened to crash there.

So failures are handled by kind:

* **infeasible** — real constraint violations, passed through as pymoo
  constraint values so the search is pushed away from them properly;
* **error** — masked with the worst *observed* objective values so far and a
  large constraint violation, and recorded separately so the study report can
  say how often it happened.  Retries have already been exhausted by the
  evaluator before we get here.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..domain.objectives import Constraint, Objective
from ..domain.results import EvaluationResult
from ..domain.variables import DesignSpace

try:  # pymoo is an optional extra
    from pymoo.core.problem import Problem

    PYMOO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on installs without the extra
    Problem = object  # type: ignore[assignment,misc]
    PYMOO_AVAILABLE = False


class StructuralProblem(Problem):  # type: ignore[misc]
    """Wraps an :class:`~openoptima.evaluation.Evaluator` as a pymoo problem."""

    def __init__(
        self,
        space: DesignSpace,
        objectives: tuple[Objective, ...],
        constraints: tuple[Constraint, ...],
        evaluate_batch,
        *,
        jobs: int = 0,
    ) -> None:
        if not PYMOO_AVAILABLE:  # pragma: no cover
            raise ImportError(
                "pymoo is required for optimisation. Install with: "
                "pip install 'openoptima[optimise]'"
            )
        lower, upper = space.bounds()
        super().__init__(
            n_var=len(space),
            n_obj=len(objectives),
            n_ieq_constr=len(constraints),
            xl=np.array(lower, dtype=float),
            xu=np.array(upper, dtype=float),
        )
        self.space = space
        self.objectives = objectives
        self.constraints = constraints
        self.evaluate_batch = evaluate_batch
        self.jobs = jobs
        self.history: list[EvaluationResult] = []
        self.error_count = 0
        self._worst_objectives = np.full(len(objectives), -np.inf)

    def _evaluate(self, x, out, *args, **kwargs) -> None:
        designs = [self.space.from_array(row.tolist()) for row in np.atleast_2d(x)]
        results = self.evaluate_batch(designs, jobs=self.jobs)
        self.history.extend(results)

        objective_values, constraint_values = self._to_arrays(results)
        out["F"] = objective_values
        if self.constraints:
            out["G"] = constraint_values

    def _to_arrays(self, results: Sequence[EvaluationResult]) -> tuple[np.ndarray, np.ndarray]:
        count = len(results)
        objective_values = np.zeros((count, len(self.objectives)))
        constraint_values = np.zeros((count, max(1, len(self.constraints))))

        # First pass: real values, and track the worst genuine objectives seen.
        usable: list[int] = []
        for row, result in enumerate(results):
            if result.outcome.value == "error":
                self.error_count += 1
                continue
            values = np.array(
                [
                    objective.signed(result.metric(objective.metric))
                    for objective in self.objectives
                ],
                dtype=float,
            )
            if not np.all(np.isfinite(values)):
                continue
            objective_values[row] = values
            self._worst_objectives = np.maximum(self._worst_objectives, values)
            usable.append(row)

        penalty = np.where(np.isfinite(self._worst_objectives), self._worst_objectives, 1.0)
        penalty = np.where(penalty > 0, penalty * 10.0, penalty + 10.0)

        for row, result in enumerate(results):
            if row in usable:
                for column, constraint in enumerate(self.constraints):
                    constraint_values[row, column] = constraint.violation(
                        result.metric(constraint.metric)
                    )
            else:
                # Unknown, not bad. Masked with a penalty so the individual dies
                # out, but recorded so the report can show how often it happened.
                objective_values[row] = penalty
                constraint_values[row, :] = 10.0

        if not self.constraints:
            constraint_values = np.zeros((count, 0))
        return objective_values, constraint_values
