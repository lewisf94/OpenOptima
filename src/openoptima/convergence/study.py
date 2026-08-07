"""Run one design at several mesh densities, and report what the numbers did.

Every other command in OpenOptima uses a single mesh setting. That makes
designs comparable with each other, but it does not tell you whether the
numbers themselves have settled. This is the command that finds out.

The documentation has always told users to do this by hand. In practice nobody
does, which means every reported number rests on an unchecked assumption.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.convergence import (
    Behaviour,
    GridLevel,
    MetricConvergence,
    analyse_metric,
    representative_size,
)
from ..domain.failures import Outcome
from ..domain.model import MeshSpecification
from ..domain.project import Project
from ..domain.results import EvaluationResult
from ..domain.variables import DesignVector
from ..evaluation.evaluator import Evaluator
from ..storage.database import ResultStore

#: Quantities worth watching, in the order a reader wants to see them.
#: Mass comes first as a control: it is a property of the shape, not of the
#: analysis, so it should barely move. If mass changes with the mesh, the
#: geometry is not being rebuilt identically and nothing else here is meaningful.
DEFAULT_METRICS: tuple[str, ...] = (
    "mass_kg",
    "displacement_max_mm",
    "stiffness_n_per_mm",
    "stress_max_mpa",
    "stress_raw_max_mpa",
    "factor_of_safety",
    "buckling_factor",
)

#: How much finer each level is than the one before. Roache advises at least
#: 1.3: too small a step and the difference between meshes is buried in
#: numerical noise, which produces a meaningless rate of settling.
DEFAULT_REFINEMENT_RATIO = 1.5

#: Four levels gives three for the arithmetic plus one spare, so a single
#: failed mesh does not waste the whole run.
DEFAULT_LEVEL_COUNT = 4

MINIMUM_RATIO = 1.1


@dataclass(frozen=True)
class MeshLevel:
    """One mesh density to run the design at."""

    label: str
    #: Multiplier on the project's configured element sizes. 1.0 is the
    #: project's own setting; smaller is finer.
    scale: float
    project: Project

    @property
    def requested_size(self) -> float:
        return self.project.mesh.global_size


@dataclass
class LevelOutcome:
    """What happened at one mesh density."""

    level: MeshLevel
    result: EvaluationResult | None = None
    error: str = ""

    @property
    def usable(self) -> bool:
        """Did this mesh produce numbers to compare?

        An **infeasible** design counts. A design that fails its own
        constraints is still a design the solver analysed successfully, and
        whether it passes those constraints has nothing to do with whether its
        numbers have settled. Refusing infeasible designs here would make the
        command useless on exactly the design a user most wants to check: the
        one sitting against a constraint boundary. It would also break the
        rule that a bad design and a broken run are different things.

        Only an **error** -- a mesh that would not build, a solver that
        crashed -- means there is nothing to compare.
        """
        return (
            self.result is not None
            and self.result.outcome is not Outcome.ERROR
            and bool(self.result.metrics)
        )

    @property
    def infeasible(self) -> bool:
        """The numbers are usable, but this design breaks its own constraints."""
        return self.result is not None and self.result.outcome is Outcome.INFEASIBLE

    @property
    def achieved_size(self) -> float:
        """Average element size actually produced, in mm.

        Compared against what was requested, this shows how much the mesher
        adjusted the request. The convergence arithmetic uses this number, not
        the requested one.
        """
        if self.result is None or self.result.mesh is None:
            return float("nan")
        return representative_size(self.result.mesh.mesh_volume, self.result.mesh.element_count)

    def to_dict(self) -> dict[str, Any]:
        mesh = self.result.mesh if self.result else None
        return {
            "label": self.level.label,
            "scale": self.level.scale,
            "requested_size_mm": self.level.requested_size,
            "achieved_size_mm": self.achieved_size,
            "node_count": mesh.node_count if mesh else None,
            "element_count": mesh.element_count if mesh else None,
            "min_scaled_jacobian": mesh.min_scaled_jacobian if mesh else None,
            "outcome": self.result.outcome.value if self.result else "not_run",
            "wall_time_s": self.result.wall_time if self.result else None,
            "metrics": dict(self.result.metrics) if self.result else {},
            "error": self.error,
        }


@dataclass
class ConvergenceStudy:
    """The whole assessment: what was run, and what the numbers did."""

    project_name: str
    design: DesignVector
    outcomes: list[LevelOutcome] = field(default_factory=list)
    metrics: dict[str, MetricConvergence] = field(default_factory=dict)
    wall_time: float = 0.0

    @property
    def usable_levels(self) -> list[LevelOutcome]:
        return [outcome for outcome in self.outcomes if outcome.usable]

    @property
    def finest(self) -> LevelOutcome | None:
        usable = self.usable_levels
        if not usable:
            return None
        return min(usable, key=lambda outcome: outcome.achieved_size)

    def diverging(self) -> list[MetricConvergence]:
        return [m for m in self.metrics.values() if m.behaviour is Behaviour.DIVERGING]

    def unsettled(self) -> list[MetricConvergence]:
        """Quantities that could not be shown to be settling."""
        return [
            m
            for m in self.metrics.values()
            if m.behaviour in (Behaviour.OSCILLATING, Behaviour.NOT_ENOUGH_DATA)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project_name,
            "design": self.design.as_dict(),
            "wall_time_s": round(self.wall_time, 2),
            "levels": [outcome.to_dict() for outcome in self.outcomes],
            "metrics": {name: m.to_dict() for name, m in self.metrics.items()},
        }


def scaled_mesh(mesh: MeshSpecification, scale: float) -> MeshSpecification:
    """The same mesh settings, with every element size multiplied by ``scale``.

    Local refinements are scaled too. Leaving them fixed while the global size
    shrinks would refine part of the model and not the rest, and the comparison
    between meshes would no longer mean anything.

    A refinement's ``distance`` is deliberately *not* scaled. It sets how far
    the refined zone reaches, which is a decision about the model, not about
    mesh density. Scaling it would move the boundary of the fine zone between
    levels and change what is being compared.
    """
    refinements = tuple(
        type(refinement)(
            region=refinement.region,
            size=refinement.size * scale,
            distance=refinement.distance,
        )
        for refinement in mesh.local_refinements
    )
    return mesh.with_overrides(
        global_size=mesh.global_size * scale,
        minimum_size=mesh.minimum_size * scale,
        local_refinements=refinements,
    )


def mesh_levels(
    project: Project,
    *,
    count: int = DEFAULT_LEVEL_COUNT,
    ratio: float = DEFAULT_REFINEMENT_RATIO,
) -> list[MeshLevel]:
    """Mesh densities to run, coarsest first.

    The project's own setting is the coarsest level, and each level after it is
    ``ratio`` times finer. This answers the question the user actually has:
    "my study used this mesh -- would a finer one have changed the answer?"
    """
    import dataclasses

    if count < 3:
        raise ValueError("a convergence study needs at least three mesh levels")
    if ratio < MINIMUM_RATIO:
        raise ValueError(
            f"refinement ratio {ratio} is too small to measure against; "
            f"use at least {MINIMUM_RATIO}. Steps smaller than this leave the "
            "difference between meshes buried in numerical noise."
        )

    levels: list[MeshLevel] = []
    for index in range(count):
        scale = ratio**-index
        mesh = project.mesh if index == 0 else scaled_mesh(project.mesh, scale)
        levels.append(
            MeshLevel(
                label=f"L{index}",
                scale=scale,
                project=dataclasses.replace(project, mesh=mesh),
            )
        )
    return levels


def element_growth(levels: list[MeshLevel]) -> float:
    """Rough multiple of the coarsest mesh's element count, summed over levels.

    Element count grows with the cube of refinement in three dimensions, so a
    modest-looking refinement ratio becomes a long run. This lets the caller
    warn before starting rather than after.
    """
    return sum(level.scale**-3 for level in levels)


def run_convergence(
    project: Project,
    design: DesignVector,
    workspace: Path,
    *,
    count: int = DEFAULT_LEVEL_COUNT,
    ratio: float = DEFAULT_REFINEMENT_RATIO,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    study: str = "convergence",
    keep_artifacts: bool = True,
    project_root: Path | None = None,
    use_cache: bool = True,
    progress: Callable[[LevelOutcome], None] | None = None,
) -> ConvergenceStudy:
    """Evaluate one design at several mesh densities and assess each metric.

    Each level is a project with different mesh settings, so each has its own
    setup digest and therefore its own cache entry and run directory. A
    convergence run can never overwrite, or be served from, the cache of the
    study it is checking.
    """
    levels = mesh_levels(project, count=count, ratio=ratio)
    started = time.monotonic()
    assessment = ConvergenceStudy(project_name=project.name, design=design)

    workspace.mkdir(parents=True, exist_ok=True)
    with ResultStore(workspace / "openoptima.sqlite") as store:
        store.start_study(
            study,
            "convergence",
            {"levels": count, "ratio": ratio, "design": design.as_dict()},
        )
        for level in levels:
            outcome = LevelOutcome(level=level)
            try:
                with Evaluator(
                    level.project,
                    workspace,
                    study=study,
                    keep_artifacts=keep_artifacts,
                    project_root=project_root,
                    store=store,
                ) as evaluator:
                    outcome.result = evaluator.evaluate(design, use_cache=use_cache)
            except Exception as exc:  # pragma: no cover - defensive
                outcome.error = f"{type(exc).__name__}: {exc}"

            # Only a real error is an error. A design that breaks its own
            # constraints analysed perfectly well, and its message describes
            # the violation, not a failure.
            if outcome.result is not None and outcome.result.outcome is Outcome.ERROR:
                outcome.error = outcome.result.message

            assessment.outcomes.append(outcome)
            if progress:
                progress(outcome)
        store.finish_study(study)

    assessment.metrics = assess_metrics(assessment.outcomes, metrics)
    assessment.wall_time = time.monotonic() - started
    return assessment


def assess_metrics(
    outcomes: list[LevelOutcome], metrics: tuple[str, ...] = DEFAULT_METRICS
) -> dict[str, MetricConvergence]:
    """Run the convergence arithmetic for each metric that was produced."""
    usable = [outcome for outcome in outcomes if outcome.usable]
    assessed: dict[str, MetricConvergence] = {}

    for name in metrics:
        grid_levels = [
            GridLevel(
                label=outcome.level.label,
                representative_size=outcome.achieved_size,
                node_count=outcome.result.mesh.node_count if outcome.result.mesh else 0,
                element_count=outcome.result.mesh.element_count if outcome.result.mesh else 0,
                value=outcome.result.metrics[name],
            )
            for outcome in usable
            if outcome.result is not None and name in outcome.result.metrics
        ]
        if not grid_levels:
            continue
        assessed[name] = analyse_metric(name, grid_levels)
    return assessed
