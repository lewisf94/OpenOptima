"""Command line interface.

``doctor`` is the important one and should be run before any study.  It builds
the extremes of the design space and checks that every region selector still
resolves uniquely there — catching the setup mistake that would otherwise be
discovered two hundred evaluations later, or worse, not at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..domain.failures import Outcome
from ..domain.project import Project
from ..evaluation.evaluator import Evaluator, default_job_count
from ..schema.loader import ProjectLoadError, load_project

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_BAD_USAGE = 2


def _load(path: str) -> tuple[Project, Path]:
    project_path = Path(path).resolve()
    project = load_project(project_path)
    return project, project_path.parent


def _workspace(project_root: Path, override: str | None) -> Path:
    return Path(override).resolve() if override else project_root / "openoptima_work"


def _progress_printer(quiet: bool):
    state = {"count": 0}

    def report(result) -> None:
        state["count"] += 1
        if quiet:
            return
        marker = {
            Outcome.OK: "ok      ",
            Outcome.INFEASIBLE: "rejected",
            Outcome.ERROR: "ERROR   ",
        }[result.outcome]
        detail = ""
        if result.outcome is Outcome.OK:
            bits = []
            for metric in ("mass_kg", "factor_of_safety", "displacement_max_mm"):
                if metric in result.metrics:
                    bits.append(f"{metric}={result.metrics[metric]:.4g}")
            detail = "  ".join(bits)
        else:
            detail = result.message[:90]
        cached = " (cached)" if result.from_cache else ""
        print(f"  [{state['count']:4d}] {marker} {detail}{cached}", flush=True)

    return report


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def command_doctor(args: argparse.Namespace) -> int:
    from ..geometry import create_provider
    from ..geometry.gmsh_session import gmsh_session
    from ..regions.matcher import resolve_regions
    from ..regions.signature import outward_normal_check, solid_face_signatures
    from ..solvers import create_solver

    project, root = _load(args.project)
    print(f"Project      : {project.name}")
    print(f"Units        : {project.unit_system.describe()}")
    print(f"Setup digest : {project.setup_digest()}")
    print(f"Variables    : {len(project.design_space)} -> {', '.join(project.design_space.ids)}")
    print(f"Regions      : {', '.join(r.name for r in project.regions)}")
    print(f"Parallel jobs: {default_job_count(project.optimisation.parallel_jobs)}")
    print()

    problems: list[str] = []

    solver = create_solver(project.solver)
    available, message = solver.available()
    print(f"Solver       : {'OK ' if available else 'MISSING'} {message}")
    if not available:
        problems.append(message)
    if project.solver.name == "analytic":
        problems.append(
            "solver is 'analytic', which is a testing stand-in and not a finite "
            "element solver. Its numbers are not engineering results."
        )

    provider = create_provider(project.geometry)
    report = provider.validate_definition()
    print(f"Geometry     : {'OK ' if report.ok else 'BAD'} {project.geometry.template}")
    for error in report.errors:
        problems.append(error)
    for warning in report.warnings:
        print(f"               warning: {warning}")
    print()

    # The real check: do the selectors survive the extremes of the design range?
    print("Checking region selectors across the design range...")
    lower, upper = project.design_space.bounds()
    probes = {
        "all minimum": project.design_space.from_array(list(lower)),
        "defaults": project.design_space.defaults(),
        "all maximum": project.design_space.from_array(list(upper)),
    }

    workspace = _workspace(root, args.workspace) / "doctor"
    for label, design in probes.items():
        print(f"\n  {label}: {design.canonical_text().replace(chr(10), ', ')}")
        try:
            geometry = provider.build(design, workspace / label.replace(" ", "_"))
        except Exception as exc:
            print(f"    geometry FAILED: {exc}")
            problems.append(f"geometry failed at '{label}': {exc}")
            continue

        print(
            f"    volume {geometry.volume:,.1f} mm^3, "
            f"mass {geometry.volume * project.material.density * 1e3:.4f} kg"
        )
        try:
            with gmsh_session() as gmsh:
                gmsh.model.add("doctor")
                gmsh.model.occ.importShapes(str(geometry.brep_path))
                gmsh.model.occ.synchronize()
                volume_tag = gmsh.model.getEntities(3)[0][1]
                signatures = solid_face_signatures(gmsh, volume_tag)
                outward, ratio = outward_normal_check(signatures, geometry.volume)
                if not outward:
                    problems.append(f"'{label}': face normals point inward (ratio {ratio:.3f})")
                region_map = resolve_regions(
                    project.regions, signatures, scale_length=geometry.bbox.diagonal
                )
            for name, match in region_map.matches.items():
                margin = "unique" if match.margin == float("inf") else f"margin {match.margin:.3f}"
                print(
                    f"    {name:<18} {len(match.face_tags)} face(s), "
                    f"{match.total_area:9.1f} mm^2, {margin}"
                )
        except Exception as exc:
            print(f"    regions FAILED: {exc}")
            problems.append(f"region resolution failed at '{label}': {exc}")

    print()
    if problems:
        print(f"{len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  - {problem}")
        return _EXIT_FAILED
    print("All checks passed. The project is ready to run.")
    return _EXIT_OK


def command_evaluate(args: argparse.Namespace) -> int:
    project, root = _load(args.project)
    overrides: dict[str, float] = {}
    for assignment in args.set or []:
        if "=" not in assignment:
            print(f"error: --set expects name=value, received {assignment!r}", file=sys.stderr)
            return _EXIT_BAD_USAGE
        name, _, raw = assignment.partition("=")
        name = name.strip()
        if name not in project.design_space.ids:
            print(
                f"error: unknown variable {name!r}. "
                f"Available: {', '.join(project.design_space.ids)}",
                file=sys.stderr,
            )
            return _EXIT_BAD_USAGE
        try:
            overrides[name] = float(raw)
        except ValueError:
            overrides[name] = raw.strip()  # type: ignore[assignment]

    base = project.design_space.defaults().as_dict()
    base.update(overrides)
    design = project.design_space.decode(base)

    with Evaluator(
        project,
        _workspace(root, args.workspace),
        study=args.study,
        keep_artifacts=not args.discard_artifacts,
        project_root=root,
    ) as evaluator:
        print(f"Evaluating: {design.canonical_text().replace(chr(10), ', ')}")
        result = evaluator.evaluate(design, use_cache=not args.no_cache)

    print(f"\nOutcome: {result.outcome.value.upper()}  (state: {result.state.value})")
    if result.from_cache:
        print("  (reused a cached result — pass --no-cache to force a re-run)")
    if result.run_directory:
        print(f"  run directory: {result.run_directory}")
    if result.mesh:
        print(
            f"  mesh: {result.mesh.node_count:,} nodes, {result.mesh.element_count:,} "
            f"{result.mesh.element_type}, worst Jacobian "
            f"{result.mesh.min_scaled_jacobian:.3f}, volume error "
            f"{result.mesh.volume_error:.3%}"
        )
    if result.metrics:
        print("\n  Metrics")
        for name in sorted(result.metrics):
            if "." in name:
                continue
            print(f"    {name:<26} {result.metrics[name]:>14.6g}")
    if result.constraint_violations:
        print("\n  Constraint violations")
        for name, value in sorted(result.constraint_violations.items()):
            print(f"    {name:<40} {value:.4g}")
    if result.failure_code:
        print(f"\n  Failure: {result.failure_code.value}\n  {result.message}")
    for warning in result.warnings:
        print(f"\n  WARNING: {warning}")

    return _EXIT_OK if result.outcome is not Outcome.ERROR else _EXIT_FAILED


def command_doe(args: argparse.Namespace) -> int:
    from ..optimisation.study import run_doe, write_study_json
    from ..reporting.report import write_report

    project, root = _load(args.project)
    workspace = _workspace(root, args.workspace)
    with Evaluator(
        project,
        workspace,
        study=args.study or "doe",
        keep_artifacts=not args.discard_artifacts,
        project_root=root,
    ) as evaluator:
        print(f"Running DOE on {project.name}")
        study = run_doe(
            project,
            evaluator,
            evaluations=args.evaluations,
            method=args.method,
            seed=args.seed,
            progress=_progress_printer(args.quiet),
        )

    _print_summary(study, project)
    report_path = write_report(study, project, workspace / "reports" / "doe.md")
    write_study_json(study, project, workspace / "reports" / "doe.json")
    print(f"\nReport: {report_path}")
    return _EXIT_OK


def command_optimise(args: argparse.Namespace) -> int:
    from ..optimisation.study import run_optimisation, write_study_json
    from ..reporting.report import write_report

    project, root = _load(args.project)
    workspace = _workspace(root, args.workspace)
    with Evaluator(
        project,
        workspace,
        study=args.study or "optimisation",
        keep_artifacts=not args.discard_artifacts,
        project_root=root,
    ) as evaluator:
        print(f"Optimising {project.name}")
        study = run_optimisation(
            project,
            evaluator,
            budget=args.budget,
            population=args.population,
            seed=args.seed,
            seed_with_doe=not args.no_doe,
            progress=_progress_printer(args.quiet),
        )

    _print_summary(study, project)
    report_path = write_report(study, project, workspace / "reports" / "optimisation.md")
    write_study_json(study, project, workspace / "reports" / "optimisation.json")
    print(f"\nReport: {report_path}")
    return _EXIT_OK if study.front else _EXIT_FAILED


def command_report(args: argparse.Namespace) -> int:
    from ..optimisation.pareto import pareto_front
    from ..optimisation.study import StudyResult
    from ..reporting.report import build_report

    project, root = _load(args.project)
    workspace = _workspace(root, args.workspace)
    from ..storage.database import ResultStore

    with ResultStore(workspace / "openoptima.sqlite") as store:
        records = store.evaluations(study=args.study or None)
        if not records:
            print("No stored evaluations found. Run 'doe' or 'optimise' first.")
            return _EXIT_FAILED
        results = [_record_to_result(record, project) for record in records]

    study = StudyResult(
        name=args.study or "(all)",
        kind="report",
        results=[r for r in results if r is not None],
    )
    study.front = pareto_front(study.results, project.objectives)
    text = build_report(study, project)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(text)
    return _EXIT_OK


def _record_to_result(record: dict, project: Project):
    import json

    from ..domain.failures import EvaluationState, FailureCode
    from ..domain.results import EvaluationResult

    try:
        design = project.design_space.decode(json.loads(record["design_json"]))
    except Exception:
        return None
    return EvaluationResult(
        design=design,
        outcome=Outcome(record["outcome"]),
        state=EvaluationState(record["state"]),
        metrics=json.loads(record["metrics_json"]),
        constraint_violations=json.loads(record["violations_json"]),
        failure_code=FailureCode(record["failure_code"]) if record["failure_code"] else None,
        message=record["message"],
        warnings=json.loads(record["warnings_json"]),
        run_id=record["run_id"],
        run_directory=record["run_directory"],
        evaluation_hash=record["evaluation_hash"],
        wall_time=record["wall_time"],
    )


def command_templates(_args: argparse.Namespace) -> int:
    from ..geometry.occ.templates import available_templates

    for template in available_templates():
        print(f"{template.name}")
        print(f"  {template.description}")
        print(f"  parameters: {', '.join(sorted(template.defaults))}")
        if template.suggested_regions:
            print(f"  typical regions: {', '.join(template.suggested_regions)}")
        print()
    return _EXIT_OK


def _print_summary(study, project: Project) -> None:
    summary = study.summary()
    print("\n" + "=" * 62)
    print(
        f"  evaluated {summary['evaluated']}   feasible {summary['feasible']}   "
        f"infeasible {summary['infeasible']}   errors {summary['errors']}"
    )
    print(f"  Pareto front: {summary['pareto_size']} design(s) in {summary['wall_time_s']:.1f} s")
    if study.failures:
        print("  failures: " + ", ".join(f"{k}={v}" for k, v in study.failures.items()))
    print("=" * 62)

    if not study.front:
        print(
            "\n  No feasible design was found. The constraints may be unreachable "
            "within the current variable ranges."
        )
        return

    from ..optimisation.pareto import apply_trade_rules, knee_point

    knee = knee_point(study.front, project.objectives)
    if knee:
        print(f"\n  Best compromise (knee of the front): run {knee.run_id}")
        _print_design(knee, project)

    chosen = apply_trade_rules(study.front, project.preferences.trade_rules)
    if chosen is not None and chosen.run_id != (knee.run_id if knee else None):
        print(f"\n  Your trade rule prefers: run {chosen.run_id}")
        _print_design(chosen, project)


def _print_design(result, project: Project) -> None:
    for name in ("mass_kg", "factor_of_safety", "displacement_max_mm", "stress_max_mpa"):
        if name in result.metrics:
            print(f"    {name:<24} {result.metrics[name]:.6g}")
    print(f"    {result.design.canonical_text().replace(chr(10), ', ')}")


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openoptima",
        description="Open-source parametric design optimisation for FEA.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("project", help="path to the project YAML file")
        sub.add_argument("--workspace", help="where to write runs and results")
        sub.add_argument("--study", default="", help="name for this study")
        sub.add_argument(
            "--discard-artifacts",
            action="store_true",
            help="delete meshes and geometry after each evaluation to save disk",
        )
        sub.add_argument("--quiet", action="store_true", help="suppress per-design output")

    doctor = subparsers.add_parser(
        "doctor", help="check a project builds and its regions resolve across the range"
    )
    add_common(doctor)
    doctor.set_defaults(func=command_doctor)

    evaluate = subparsers.add_parser("evaluate", help="evaluate a single design")
    add_common(evaluate)
    evaluate.add_argument(
        "--set", action="append", metavar="NAME=VALUE", help="override a design variable"
    )
    evaluate.add_argument("--no-cache", action="store_true", help="ignore cached results")
    evaluate.set_defaults(func=command_evaluate)

    doe = subparsers.add_parser("doe", help="run a space-filling design of experiments")
    add_common(doe)
    doe.add_argument("--evaluations", type=int, help="number of samples")
    doe.add_argument("--method", choices=["sobol", "lhs", "random", "factorial"])
    doe.add_argument("--seed", type=int)
    doe.set_defaults(func=command_doe)

    optimise = subparsers.add_parser("optimise", help="run a multi-objective optimisation")
    add_common(optimise)
    optimise.add_argument("--budget", type=int, help="total evaluation budget")
    optimise.add_argument("--population", type=int, help="population size")
    optimise.add_argument("--seed", type=int)
    optimise.add_argument("--no-doe", action="store_true", help="skip the DOE seeding stage")
    optimise.set_defaults(func=command_optimise)

    report = subparsers.add_parser("report", help="rebuild a report from stored results")
    add_common(report)
    report.add_argument("--output", help="write to a file instead of stdout")
    report.set_defaults(func=command_report)

    templates = subparsers.add_parser("templates", help="list built-in geometry templates")
    templates.set_defaults(func=command_templates)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ProjectLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_BAD_USAGE
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
