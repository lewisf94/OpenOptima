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
from pathlib import Path

from ..domain.failures import (
    EvaluationFailure,
    EvaluationState,
    FailureCode,
    Outcome,
    outcome_for,
)
from ..domain.project import Project
from ..domain.results import EvaluationResult, MeshSummary
from ..domain.variables import DesignVector
from ..geometry import create_provider
from ..meshing.gmsh_mesher import GmshMesher
from ..results.metrics import collect_metrics
from ..solvers import create_solver
from .runspace import RunSpace, RunSpaceFactory, tool_versions


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
            result = self._run(design, run)
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
    def _run(self, design: DesignVector, run: RunSpace) -> EvaluationResult:
        project = self.project

        # 1. geometry
        provider = create_provider(project.geometry)
        if hasattr(provider, "root"):
            provider.root = self.project_root  # type: ignore[attr-defined]
        geometry = provider.build(design, run.geometry_dir)
        run.manifest["geometry"] = geometry.to_dict()
        state = EvaluationState.GEOMETRY_GENERATED

        # 2. mesh (regions are resolved inside, against the reloaded solid)
        mesher = GmshMesher(project.mesh)
        mesh, region_map = mesher.generate(
            geometry,
            project.regions,
            run.mesh_dir,
            write_mesh_file=self.keep_artifacts,
        )
        state = EvaluationState.MESH_VALIDATED
        assert mesh.quality is not None
        run.manifest["regions"] = region_map.to_dict()
        run.manifest["mesh"] = mesh.quality.to_dict()
        run.write_json("mesh/regions.json", region_map.to_dict())

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
            analysis, project.analysis_model(), mesh, region_map, geometry.volume
        )
        state = EvaluationState.RESULTS_PARSED

        warnings = (
            list(analysis.warnings)
            + list(mesh.quality.warnings)
            + geometry.warnings
            + metric_warnings
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
        violations: dict[str, float] = {}
        all_constraints = list(self.project.constraints) + list(
            self.project.preferences.hard_limits
        )
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


def outcome_of(code: FailureCode | None) -> Outcome:
    return outcome_for(code)
