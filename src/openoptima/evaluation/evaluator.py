"""The evaluator: caching, retries, persistence and parallelism around the pipeline.

Everything above this line — DOE, optimiser, CLI — talks to an
:class:`Evaluator` and never to the pipeline directly.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path

from ..domain.failures import FailureCode, Outcome, is_retryable
from ..domain.project import Project
from ..domain.results import EvaluationResult
from ..domain.variables import DesignVector
from ..storage.database import ResultStore
from .cache import evaluation_hash
from .pipeline import EvaluationPipeline, rejudge
from .runspace import tool_versions


@dataclass
class EvaluationBudget:
    """Tracks how much of the user's evaluation allowance has been spent."""

    limit: int
    spent: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


def default_job_count(requested: int = 0) -> int:
    """Concurrent evaluations to run.

    Leaves one core free so an interactive machine stays usable, and never
    exceeds the core count — oversubscribing a solver makes everything slower.
    """
    if requested and requested > 0:
        return requested
    cores = os.cpu_count() or 1
    return max(1, cores - 1)


class Evaluator:
    def __init__(
        self,
        project: Project,
        workspace: Path,
        *,
        study: str = "",
        keep_artifacts: bool = True,
        project_root: Path | None = None,
        store: ResultStore | None = None,
    ) -> None:
        self.project = project
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.study = study
        self.keep_artifacts = keep_artifacts
        self.project_root = project_root or Path.cwd()
        self.versions = tool_versions()
        self._owns_store = store is None
        self.store = store or ResultStore(self.workspace / "openoptima.sqlite")
        self.pipeline = EvaluationPipeline(
            project,
            self.workspace / "runs",
            keep_artifacts=keep_artifacts,
            project_root=self.project_root,
        )
        self.setup_digest = project.setup_digest()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> Evaluator:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- single evaluation ---------------------------------------------------
    def hash_for(self, design: DesignVector) -> str:
        return evaluation_hash(self.project, design, self.versions)

    def evaluate(self, design: DesignVector, *, use_cache: bool = True) -> EvaluationResult:
        digest = self.hash_for(design)

        if use_cache:
            cached = self.store.cached_result(digest, self.project.design_space)
            if cached is not None:
                return rejudge(cached, self.project)

        attempts = max(1, self.project.optimisation.max_retries + 1)
        result = self.pipeline.evaluate(design, digest)
        attempt = 1
        while (
            result.outcome is Outcome.ERROR
            and is_retryable(result.failure_code)
            and attempt < attempts
        ):
            attempt += 1
            result.warnings.append(
                f"retrying after {result.failure_code.value if result.failure_code else '?'} "
                f"(attempt {attempt} of {attempts})"
            )
            retried = self.pipeline.evaluate(design, digest)
            retried.warnings = result.warnings + retried.warnings
            result = retried

        self.store.record(result, setup_digest=self.setup_digest, study=self.study)
        return result

    # -- batch ---------------------------------------------------------------
    def evaluate_many(
        self,
        designs: Sequence[DesignVector],
        *,
        jobs: int = 0,
        use_cache: bool = True,
        on_result: Callable[[EvaluationResult], None] | None = None,
    ) -> list[EvaluationResult]:
        """Evaluate a batch, in parallel where it helps.

        Parallelism is by *process*: gmsh keeps global C state and CalculiX is a
        subprocess, so threads would serialise or corrupt each other.
        """
        if not designs:
            return []

        pending: list[tuple[int, DesignVector, str]] = []
        results: list[EvaluationResult | None] = [None] * len(designs)

        for index, design in enumerate(designs):
            digest = self.hash_for(design)
            if use_cache:
                cached = self.store.cached_result(digest, self.project.design_space)
                if cached is not None:
                    judged = rejudge(cached, self.project)
                    results[index] = judged
                    if on_result:
                        on_result(judged)
                    continue
            pending.append((index, design, digest))

        if not pending:
            return [r for r in results if r is not None]

        worker_count = min(default_job_count(jobs), len(pending))

        if worker_count <= 1:
            for index, design, digest in pending:
                result = self.pipeline.evaluate(design, digest)
                self.store.record(result, setup_digest=self.setup_digest, study=self.study)
                results[index] = result
                if on_result:
                    on_result(result)
        else:
            # 'spawn', never the platform default. On Linux the default is
            # 'fork', which copies this process's memory into the child --
            # including gmsh's initialised C library state and its internal
            # locks. A child that inherits a half-initialised gmsh deadlocks on
            # its first call and the study hangs forever with no error. Spawn
            # costs an interpreter start per worker and is worth every
            # millisecond.
            context = multiprocessing.get_context("spawn")
            run_ids = self.pipeline.factory.reserve(len(pending))
            payloads = [
                (
                    self.project,
                    str(self.workspace / "runs"),
                    design.as_dict(),
                    digest,
                    run_id,
                    self.keep_artifacts,
                    str(self.project_root),
                )
                for (_index, design, digest), run_id in zip(pending, run_ids, strict=True)
            ]
            with ProcessPoolExecutor(max_workers=worker_count, mp_context=context) as pool:
                futures = {
                    pool.submit(_evaluate_in_worker, payload): position
                    for position, payload in enumerate(payloads)
                }
                for future in as_completed(futures):
                    position = futures[future]
                    index, design, digest = pending[position]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = EvaluationResult.failed(
                            design,
                            *_worker_failure(exc),
                            evaluation_hash=digest,
                        )
                    result.design = design
                    self.store.record(result, setup_digest=self.setup_digest, study=self.study)
                    results[index] = result
                    if on_result:
                        on_result(result)

        return [r for r in results if r is not None]


def _worker_failure(exc: BaseException) -> tuple[FailureCode, str]:
    """Classify a worker that did not return a result.

    ``BrokenProcessPool`` is the one worth naming. It means a worker process
    vanished rather than raising -- the operating system stopped it, and on a
    machine solving several designs at once that is almost always memory.
    Measured: a worker killed with SIGKILL, which is what the kernel sends,
    surfaces here as exactly this exception with no exit code attached, so the
    cause cannot be read off it. The message therefore says what is certain
    and names the likely reason without asserting it.

    It also takes the **whole pool** down, so every other design still running
    fails with it. Those are not bad designs and none of them was judged.
    """
    if isinstance(exc, BrokenProcessPool):
        return (
            FailureCode.OUT_OF_MEMORY,
            "A worker process was stopped by the operating system part way "
            "through, which takes down every design being solved alongside it. "
            "Nothing in OpenOptima chose to stop, so this is almost always the "
            "machine running out of memory: each worker holds a whole meshed "
            "model. Run fewer at a time with optimisation.parallel_jobs, or use "
            "a coarser mesh. These designs are not at fault and have not been "
            "judged.",
        )
    return (
        FailureCode.WORKER_CRASH,
        f"evaluation worker died: {type(exc).__name__}: {exc}",
    )


def _evaluate_in_worker(
    payload: tuple[Project, str, dict, str, str, bool, str],
) -> EvaluationResult:
    """Entry point in a child process. Must be module level to be picklable."""
    project, runs_root, design_values, digest, run_id, keep_artifacts, project_root = payload
    pipeline = EvaluationPipeline(
        project,
        Path(runs_root),
        keep_artifacts=keep_artifacts,
        project_root=Path(project_root),
    )
    design = project.design_space.decode(design_values)
    return pipeline.evaluate(design, digest, run_id=run_id)
