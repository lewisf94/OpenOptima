"""The evaluation pipeline: one design vector in, one classified result out.

This is where the failure taxonomy earns its keep.  Each stage advances the
state machine, and each classified failure carries a code that says whether the
*design* was bad (feed it to the optimiser as a constraint violation) or whether
*we* failed (retry, and never let the optimiser conclude anything about the
design from it).
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ..domain.failures import (
    EvaluationFailure,
    EvaluationState,
    FailureCode,
    Outcome,
    outcome_for,
)
from ..domain.project import Project
from ..domain.regions import RegionMap
from ..domain.results import EvaluationResult, MeshSummary
from ..domain.variables import DesignSpace, DesignVector
from ..geometry import create_provider
from ..geometry.base import SurfaceArtifact
from ..meshing.base import MeshData
from ..meshing.gmsh_mesher import GmshMesher
from ..results.metrics import collect_metrics
from ..solvers import create_solver
from .runspace import RunSpace, RunSpaceFactory, tool_versions

#: Stands in for a design vector when a shape did not come from the design
#: space at all -- a topology result, or any imported surface.  The shape is
#: still evaluated exactly like any other, and this says plainly that no set of
#: parameter values produced it.
NO_DESIGN = DesignVector(values={}, space=DesignSpace(variables=()))


class EvaluationPipeline:
    """Runs one design through geometry, meshing, solving and metric extraction."""

    def __init__(
        self,
        project: Project,
        runs_root: Path,
        *,
        keep_artifacts: bool = True,
        project_root: Path | None = None,
    ) -> None:
        self.project = project
        self.project_root = project_root or Path.cwd()
        self.factory = RunSpaceFactory(runs_root, keep_artifacts=keep_artifacts)
        self.keep_artifacts = keep_artifacts
        self._versions = tool_versions()

    # -- public API ----------------------------------------------------------
    def evaluate(
        self,
        design: DesignVector,
        evaluation_hash: str = "",
        run_id: str | None = None,
    ) -> EvaluationResult:
        return self._evaluate(
            design,
            run_id,
            lambda run: self._geometry_stage(design, run),
            evaluation_hash=evaluation_hash,
        )

    def evaluate_surface(
        self,
        surface: SurfaceArtifact,
        *,
        run_id: str | None = None,
    ) -> EvaluationResult:
        """Evaluate a shape that arrived as triangles rather than as CAD.

        This is how a topology result becomes a number anybody may quote.  What
        the optimiser hands over is a shape, and a shape on its own says nothing
        about stress, deflection or how close the part is to failing.  Putting it
        through here re-meshes it into solid elements, puts the same loads and
        supports back on by re-resolving the same region selectors, and solves it
        exactly like any other design.

        Nothing about the analysis is relaxed for it.  The same quality gates,
        the same constraints and the same failure classification apply, so the
        answer is comparable with the parametric designs beside it.
        """
        return self._evaluate(NO_DESIGN, run_id, lambda run: self._surface_stage(surface, run))

    def _evaluate(
        self,
        design: DesignVector,
        run_id: str | None,
        stage: Callable[[RunSpace], tuple[MeshData, RegionMap, float, list[str]]],
        evaluation_hash: str = "",
    ) -> EvaluationResult:
        """Shared body of :meth:`evaluate` and :meth:`evaluate_surface`."""
        started = time.monotonic()
        run = self.factory.allocate(run_id)
        state = EvaluationState.CREATED

        run.manifest = {
            "run_id": run.run_id,
            "project": self.project.name,
            "setup_digest": self.project.setup_digest(),
            "design": design.as_dict(),
            "design_digest": design.digest(),
            "evaluation_hash": evaluation_hash,
            "unit_system": self.project.unit_system_name,
            "versions": self._versions,
            "started_at": time.time(),
        }

        try:
            mesh, region_map, reference_volume, warnings = stage(run)
            result = self._analyse(design, run, mesh, region_map, reference_volume, warnings)
            result.run_id = run.run_id
            result.run_directory = str(run.directory)
            result.evaluation_hash = evaluation_hash
            result.wall_time = time.monotonic() - started
            result.provenance = dict(self._versions)
            run.manifest["outcome"] = result.outcome.value
            run.manifest["state"] = result.state.value
            run.manifest["metrics"] = result.metrics
            run.manifest["wall_time_s"] = result.wall_time
            run.write_manifest()
            run.write_json("results/metrics.json", result.to_dict())
            if not self.keep_artifacts:
                run.discard_bulk()
            return result

        except EvaluationFailure as failure:
            result = EvaluationResult.failed(
                design,
                failure.code,
                failure.message,
                state=state,
                run_id=run.run_id,
                run_directory=str(run.directory),
                evaluation_hash=evaluation_hash,
                wall_time=time.monotonic() - started,
                provenance=dict(self._versions),
            )
            run.manifest["outcome"] = result.outcome.value
            run.manifest["failure_code"] = failure.code.value
            run.manifest["failure_message"] = failure.message
            run.manifest["failure_detail"] = failure.detail
            run.write_manifest()
            return result

        except Exception as exc:  # a bug in us, not a statement about the design
            run.manifest["outcome"] = Outcome.ERROR.value
            run.manifest["failure_code"] = FailureCode.INTERNAL_ERROR.value
            run.manifest["failure_message"] = str(exc)
            run.manifest["traceback"] = traceback.format_exc()
            run.write_manifest()
            return EvaluationResult.failed(
                design,
                FailureCode.INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
                state=state,
                run_id=run.run_id,
                run_directory=str(run.directory),
                evaluation_hash=evaluation_hash,
                wall_time=time.monotonic() - started,
                provenance=dict(self._versions),
            )

    # -- stages --------------------------------------------------------------
    def _geometry_stage(
        self, design: DesignVector, run: RunSpace
    ) -> tuple[MeshData, RegionMap, float, list[str]]:
        """Build the parametric solid and mesh it."""
        project = self.project

        provider = create_provider(project.geometry, project.regions)
        if hasattr(provider, "root"):
            provider.root = self.project_root  # type: ignore[attr-defined]
        geometry = provider.build(design, run.geometry_dir)
        run.manifest["geometry"] = geometry.to_dict()

        mesher = GmshMesher(project.mesh)
        mesh, region_map = mesher.generate(
            geometry,
            project.regions,
            run.mesh_dir,
            write_mesh_file=self.keep_artifacts,
        )
        self._record_mesh(run, mesh, region_map)
        return mesh, region_map, geometry.volume, list(geometry.warnings)

    def _surface_stage(
        self, surface: SurfaceArtifact, run: RunSpace
    ) -> tuple[MeshData, RegionMap, float, list[str]]:
        """Mesh a closed triangle surface, working its faces out by measurement."""
        run.manifest["surface"] = surface.to_dict()
        mesher = GmshMesher(self.project.mesh)
        mesh, region_map = mesher.generate_from_surface(
            surface,
            self.project.regions,
            run.mesh_dir,
            write_mesh_file=self.keep_artifacts,
        )
        self._record_mesh(run, mesh, region_map)
        return mesh, region_map, surface.volume, list(surface.warnings)

    def _record_mesh(self, run: RunSpace, mesh: MeshData, region_map: RegionMap) -> None:
        assert mesh.quality is not None
        run.manifest["regions"] = region_map.to_dict()
        run.manifest["mesh"] = mesh.quality.to_dict()
        run.write_json("mesh/regions.json", region_map.to_dict())

    def _analyse(
        self,
        design: DesignVector,
        run: RunSpace,
        mesh: MeshData,
        region_map: RegionMap,
        reference_volume: float,
        stage_warnings: list[str],
    ) -> EvaluationResult:
        """Solve, measure and check. Identical whatever produced the mesh."""
        project = self.project
        state = EvaluationState.MESH_VALIDATED
        assert mesh.quality is not None

        # 3. solve
        solver = create_solver(project.solver)
        available, message = solver.available()
        if not available:
            raise EvaluationFailure(FailureCode.SOLVER_NOT_FOUND, message)
        state = EvaluationState.SOLVER_INPUT_WRITTEN
        analysis = solver.solve(project.analysis_model(), mesh, run.solver_dir)
        state = EvaluationState.SOLVED
        run.manifest["solver"] = {
            "name": analysis.solver_name,
            "version": analysis.solver_version,
            "warnings": list(analysis.warnings),
        }

        # 4. metrics
        metrics, load_cases, metric_warnings = collect_metrics(
            analysis, project.analysis_model(), mesh, region_map, reference_volume
        )
        state = EvaluationState.RESULTS_PARSED

        warnings = (
            list(analysis.warnings) + list(mesh.quality.warnings) + stage_warnings + metric_warnings
        )

        # 5. engineering constraints
        violations = self._constraint_violations(metrics)
        state = EvaluationState.CHECKS_COMPLETE

        outcome = Outcome.OK
        failure_code = None
        message_text = ""
        if violations:
            outcome = Outcome.INFEASIBLE
            failure_code = FailureCode.ENGINEERING_CONSTRAINT_FAILED
            message_text = "; ".join(
                f"{name} violated by {value:.4g} (normalised)"
                for name, value in sorted(violations.items())
            )

        result = EvaluationResult(
            design=design,
            outcome=outcome,
            state=EvaluationState.ACCEPTED if outcome is Outcome.OK else state,
            metrics=metrics,
            load_cases=load_cases,
            mesh=MeshSummary(
                node_count=mesh.quality.node_count,
                element_count=mesh.quality.element_count,
                element_type=mesh.quality.element_type,
                min_scaled_jacobian=mesh.quality.min_scaled_jacobian,
                mesh_volume=mesh.quality.mesh_volume,
                cad_volume=mesh.quality.cad_volume,
                volume_error=mesh.quality.volume_error,
                algorithm=mesh.quality.algorithm,
                attempt=mesh.quality.attempt,
            ),
            failure_code=failure_code,
            message=message_text,
            warnings=warnings,
            constraint_violations=violations,
        )
        return result

    def _constraint_violations(self, metrics: dict[str, float]) -> dict[str, float]:
        """Evaluate hard constraints. Missing metrics are a setup error, not a pass."""
        return constraint_violations(self.project, metrics)


def constraint_violations(project: Project, metrics: dict[str, float]) -> dict[str, float]:
    """Which of the project's hard limits this set of metrics breaks.

    Module level rather than a method because a *cached* result has to be
    judged by exactly the same rule as a fresh one -- see :func:`rejudge`.
    """
    violations: dict[str, float] = {}
    all_constraints = list(project.constraints) + list(project.preferences.hard_limits)
    for constraint in all_constraints:
        if constraint.metric not in metrics:
            raise EvaluationFailure(
                FailureCode.INTERNAL_ERROR,
                f"constraint refers to unknown metric {constraint.metric!r}. "
                f"Available metrics: {sorted(metrics)}",
            )
        violation = constraint.violation(metrics[constraint.metric])
        if violation > 0:
            violations[constraint.describe()] = violation
    return violations


def rejudge(result: EvaluationResult, project: Project) -> EvaluationResult:
    """Apply the project's *current* constraints to a result read from cache.

    **A constraint threshold is not part of the evaluation hash, and must not
    be.** Changing "factor of safety at least 2" to "at least 2.5" changes no
    computed number: the stress, the mass and the frequency are all still
    correct, and re-solving them would be waste. So the cache keeps serving
    them, which is right.

    What is *not* right is replaying the verdict that was stored alongside
    them. Feasibility is a judgement about metrics against limits, and the
    limits have moved. Measured on ``examples/drone_arm`` when its frequency
    limit was lowered from 195 to 170 Hz: 30 of 50 designs in the next run
    came back from cache carrying their old verdict, 9 of them marked
    infeasible while actually clearing the new limit -- and the lightest of
    those, 72.1 g at 175 Hz, beat the 72.7 g the run went on to report as its
    best. The optimiser was told its own answer was unavailable.

    Only a verdict that constraints decided is revisited. An infeasible design
    that broke a manufacturing rule, lost a region to a feature or failed to
    build is a fact about the shape, not about a limit, and no change to a
    constraint makes it feasible.
    """
    decided_by_constraints = result.outcome is Outcome.OK or (
        result.failure_code is FailureCode.ENGINEERING_CONSTRAINT_FAILED
    )
    if not decided_by_constraints or not result.metrics:
        return result

    violations = constraint_violations(project, result.metrics)
    if violations == result.constraint_violations:
        return result

    if violations:
        return replace(
            result,
            outcome=Outcome.INFEASIBLE,
            state=EvaluationState.CHECKS_COMPLETE,
            failure_code=FailureCode.ENGINEERING_CONSTRAINT_FAILED,
            message="; ".join(
                f"{name} violated by {value:.4g} (normalised)"
                for name, value in sorted(violations.items())
            ),
            constraint_violations=violations,
        )
    return replace(
        result,
        outcome=Outcome.OK,
        state=EvaluationState.ACCEPTED,
        failure_code=None,
        message="",
        constraint_violations={},
    )


def outcome_of(code: FailureCode | None) -> Outcome:
    return outcome_for(code)
