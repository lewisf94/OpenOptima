"""Background study runner for the desktop app.

A study takes minutes to hours, so the browser cannot wait on a request. Jobs
run on a worker thread, publish progress as they go, and the page polls.

Only one job runs at a time. That is deliberate: the evaluator already spreads
designs across every core, so a second concurrent study would slow both down
and make the progress display meaningless.
"""

from __future__ import annotations

import itertools
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.failures import Outcome
from ..domain.project import Project
from ..evaluation.evaluator import Evaluator
from ..optimisation.pareto import apply_trade_rules, knee_point, marginal_rates
from ..optimisation.study import run_doe, run_optimisation
from ..reporting.report import write_report

_counter = itertools.count(1)


@dataclass
class Job:
    id: str
    kind: str
    project_path: str
    state: str = "running"  # running | finished | failed | cancelled
    progress: list[dict[str, Any]] = field(default_factory=list)
    evaluated: int = 0
    budget: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    front: list[dict[str, Any]] = field(default_factory=list)
    trade_offs: list[dict[str, Any]] = field(default_factory=list)
    highlights: dict[str, Any] = field(default_factory=dict)
    report_path: str = ""
    error: str = ""
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "evaluated": self.evaluated,
            "budget": self.budget,
            "progress": self.progress[-200:],
            "summary": self.summary,
            "front": self.front,
            "trade_offs": self.trade_offs,
            "highlights": self.highlights,
            "report_path": self.report_path,
            "error": self.error,
        }


class JobRunner:
    """Owns the single running job and its history."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._current: Job | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.state == "running"

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def current(self) -> Job | None:
        with self._lock:
            return self._current

    def start(self, project: Project, project_path: Path, kind: str, budget: int | None) -> Job:
        if self.busy:
            raise RuntimeError("a study is already running")

        job = Job(
            id=f"job{next(_counter)}",
            kind=kind,
            project_path=str(project_path),
            budget=budget or project.optimisation.algorithm.evaluation_budget,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._current = job

        thread = threading.Thread(
            target=self._run, args=(job, project, project_path, kind, budget), daemon=True
        )
        thread.start()
        return job

    # -- worker --------------------------------------------------------------
    def _run(
        self,
        job: Job,
        project: Project,
        project_path: Path,
        kind: str,
        budget: int | None,
    ) -> None:
        workspace = project_path.parent / "openoptima_work"

        def on_result(result) -> None:
            if job.cancelled:
                # Co-operative stop: the evaluator finishes the design in flight,
                # then the study loop sees the flag on its next batch.
                raise KeyboardInterrupt("cancelled by the user")
            job.evaluated += 1
            job.progress.append(
                {
                    "n": job.evaluated,
                    "outcome": result.outcome.value,
                    "message": _short_message(result),
                    "metrics": {
                        name: value for name, value in result.metrics.items() if "." not in name
                    },
                    "cached": result.from_cache,
                }
            )

        try:
            with Evaluator(
                project,
                workspace,
                study=kind,
                keep_artifacts=False,
                project_root=project_path.parent,
            ) as evaluator:
                if kind == "doe":
                    study = run_doe(project, evaluator, evaluations=budget, progress=on_result)
                else:
                    study = run_optimisation(project, evaluator, budget=budget, progress=on_result)

            job.summary = study.summary()
            job.front = [_front_entry(project, r) for r in study.front]
            job.trade_offs = _trade_offs(project, study.front)
            job.highlights = _highlights(project, study.front)
            report = write_report(study, project, workspace / "reports" / f"{kind}.md")
            job.report_path = str(report)
            job.state = "finished"

        except KeyboardInterrupt:
            job.state = "cancelled"
            job.error = "Stopped at your request. Results so far are kept."
        except Exception as exc:
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.progress.append(
                {"n": job.evaluated, "outcome": "error", "message": traceback.format_exc()[-800:]}
            )
        finally:
            with self._lock:
                if self._current is job:
                    self._current = None


def _short_message(result) -> str:
    if result.outcome is Outcome.OK:
        bits = []
        for metric in ("mass_kg", "factor_of_safety", "buckling_factor"):
            if metric in result.metrics:
                bits.append(f"{_label(metric)} {result.metrics[metric]:.4g}")
        return "  ".join(bits) or "accepted"
    return result.message[:160]


def _label(metric: str) -> str:
    return {
        "mass_kg": "mass",
        "factor_of_safety": "safety",
        "buckling_factor": "buckling",
        "displacement_max_mm": "deflection",
        "stress_max_mpa": "stress",
    }.get(metric, metric)


def _front_entry(project: Project, result) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "design": result.design.as_dict(),
        "metrics": {name: value for name, value in result.metrics.items() if "." not in name},
        "objectives": [
            {"metric": o.metric, "label": o.display_name, "value": result.metric(o.metric)}
            for o in project.objectives
        ],
        "warnings": result.warnings,
    }


def _trade_offs(project: Project, front: list) -> list[dict[str, Any]]:
    """What each step along the front costs, for the table the user reads."""
    if len(project.objectives) < 2 or len(front) < 2:
        return []
    give, gain = project.objectives[0], project.objectives[1]
    out = []
    for rate in marginal_rates(front, give.metric, gain.metric):
        out.append(
            {
                "give_label": give.display_name,
                "gain_label": gain.display_name,
                "give_delta": rate.give_delta,
                "gain_delta": rate.gain_delta,
                "rate": rate.rate if rate.rate == rate.rate else None,
            }
        )
    return out


def _highlights(project: Project, front: list) -> dict[str, Any]:
    knee = knee_point(front, project.objectives)
    chosen = apply_trade_rules(front, project.preferences.trade_rules)
    return {
        "knee_run_id": knee.run_id if knee else None,
        "trade_rule_run_id": chosen.run_id if chosen else None,
    }
