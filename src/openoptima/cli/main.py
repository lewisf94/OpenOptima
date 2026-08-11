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

from ..domain.failures import EvaluationFailure, Outcome
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


def command_converge(args: argparse.Namespace) -> int:
    from ..convergence.study import (
        DEFAULT_LEVEL_COUNT,
        DEFAULT_REFINEMENT_RATIO,
        element_growth,
        mesh_levels,
        run_convergence,
    )
    from ..reporting.convergence_report import (
        summarise_for_terminal,
        write_convergence_json,
        write_convergence_report,
    )

    project, root = _load(args.project)
    workspace = _workspace(root, args.workspace)

    design, error = _design_for_convergence(args, project, workspace)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return _EXIT_BAD_USAGE

    count = args.levels or DEFAULT_LEVEL_COUNT
    ratio = args.ratio or DEFAULT_REFINEMENT_RATIO
    try:
        levels = mesh_levels(project, count=count, ratio=ratio)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_BAD_USAGE

    print(f"Mesh convergence check on {project.name}")
    print(f"Design: {design.canonical_text().replace(chr(10), ', ')}")
    print()
    print("Running the same design at these mesh sizes:")
    for level in levels:
        print(f"  {level.label}  {level.requested_size:8.3f} mm")
    growth = element_growth(levels)
    print()
    print(
        f"Together these are roughly {growth:.0f} times the work of one "
        "evaluation at your project's own mesh setting, because halving the "
        "element size multiplies the element count by about eight."
    )
    print()

    def show(outcome) -> None:
        mesh = outcome.result.mesh if outcome.result else None
        if outcome.usable and mesh:
            note = "  (design breaks its limits)" if outcome.infeasible else ""
            print(
                f"  {outcome.level.label}  {mesh.element_count:>9,} elements  "
                f"avg size {outcome.achieved_size:6.3f} mm  "
                f"({outcome.result.wall_time:.1f} s){note}"
            )
        else:
            print(f"  {outcome.level.label}  FAILED  {outcome.error[:70]}")

    assessment = run_convergence(
        project,
        design,
        workspace,
        count=count,
        ratio=ratio,
        study=args.study or "convergence",
        keep_artifacts=not args.discard_artifacts,
        project_root=root,
        use_cache=not args.no_cache,
        progress=show,
    )

    print()
    print("=" * 70)
    for line in summarise_for_terminal(assessment):
        print(line)
    print("=" * 70)

    report_path = write_convergence_report(
        assessment, project, workspace / "reports" / "convergence.md"
    )
    write_convergence_json(assessment, workspace / "reports" / "convergence.json")
    print(f"\nFull report: {report_path}")

    if len(assessment.usable_levels) < 3:
        print("\nToo few meshes succeeded to say anything about convergence. Three are needed.")
        return _EXIT_FAILED
    return _EXIT_OK


def _design_for_convergence(args: argparse.Namespace, project: Project, workspace: Path):
    """Pick the design to check: a stored run, explicit values, or the defaults."""
    if args.run:
        from ..storage.database import ResultStore

        database = workspace / "openoptima.sqlite"
        if not database.exists():
            return None, f"no results database at {database}. Run a study first."
        with ResultStore(database) as store:
            for record in store.evaluations():
                if str(record["run_id"]) == str(args.run):
                    result = _record_to_result(record, project)
                    if result is None:
                        return None, f"run {args.run} could not be read back"
                    return result.design, ""
        return None, f"run {args.run!r} not found in {database}"

    values = project.design_space.defaults().as_dict()
    for assignment in args.set or []:
        if "=" not in assignment:
            return None, f"--set expects name=value, received {assignment!r}"
        name, _, raw = assignment.partition("=")
        name = name.strip()
        if name not in project.design_space.ids:
            return None, (
                f"unknown variable {name!r}. Available: {', '.join(project.design_space.ids)}"
            )
        try:
            values[name] = float(raw)
        except ValueError:
            values[name] = raw.strip()
    return project.design_space.decode(values), ""


def command_topology(args: argparse.Namespace) -> int:
    """Run a topology optimisation and hand back a shape to look at.

    Deliberately does **not** report a stress, a displacement or a factor of
    safety, because none has been computed. What comes out of a topology run is
    a proposal: a shape somebody still has to analyse properly. Saying anything
    else would be the most dangerous thing this software could do.
    """
    from ..domain.topology import TopologySettings
    from ..topology import fetch
    from ..topology.runner import REPRODUCIBLE_CPU_CORES, run_topology
    from ..topology.solidify import to_solid

    project, root = _load(args.project)
    workspace = _workspace(root, args.workspace)

    deck = Path(args.deck)
    if not deck.is_file():
        print(f"error: no analysis file at {deck}", file=sys.stderr)
        return _EXIT_BAD_USAGE

    try:
        settings = TopologySettings(
            volume_fraction=args.keep,
            minimum_feature_size_mm=args.feature_size,
            maximum_iterations=args.rounds,
            evolution_rate=args.removal_rate,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_BAD_USAGE

    output = workspace / "topology"
    print(f"Topology optimisation on {project.name}")
    print(f"  keeping at most {settings.volume_fraction:.0%} of the material")
    print(f"  smallest feature {settings.minimum_feature_size_mm:g} mm")
    print(f"  up to {settings.maximum_iterations} rounds on {args.cores} core(s)")
    for warning in settings.feature_size_warnings():
        print(f"  note: {warning}")

    try:
        # Reported once rather than as a percentage: these are seven small
        # files, so a progress bar would flicker past and only clutter a log.
        if not fetch.verify(fetch.install_directory(workspace / "tools")):
            print("  fetching the topology optimiser...")
        installed = fetch.install(workspace / "tools")
        outcome = run_topology(
            settings=settings,
            material=project.material,
            deck=deck,
            beso=installed,
            solver_executable=args.solver or "ccx",
            output_directory=output,
            cpu_cores=args.cores,
        )
    except (fetch.BesoFetchError, EvaluationFailure) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_FAILED

    print(f"  finished in {outcome.iterations} rounds")
    if outcome.mass_fraction is not None:
        print(f"  material left: {outcome.mass_fraction:.1%}")
    for warning in outcome.warnings:
        print(f"  note: {warning}")

    try:
        solid = to_solid(outcome.solid_mesh, smoothing_passes=args.smoothing)
    except EvaluationFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_FAILED

    stl = solid.write_stl(output / "shape.stl")
    print(
        f"  smoothed over {solid.smoothing_passes} passes, "
        f"losing {abs(solid.volume_change):.1%} of the material"
    )
    for warning in solid.warnings:
        print(f"  note: {warning}")
    print(f"\nShape written to {stl}")

    print(
        "\nThis is a proposal, not a result. Nothing here has been analysed:\n"
        "  no stress, no deflection and no factor of safety has been computed\n"
        "  for this shape. Rebuild it as a parametric model, or import it, and\n"
        "  run it through 'openoptima evaluate' before relying on any number."
    )
    if args.cores != REPRODUCIBLE_CPU_CORES:
        print(
            "  It may also not be repeatable: running again on more than one\n"
            "  core can produce a different shape."
        )
    return _EXIT_OK


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

    converge = subparsers.add_parser(
        "converge",
        help="re-run one design at several mesh densities to see if its numbers have settled",
    )
    add_common(converge)
    converge.add_argument("--run", help="check the design from this stored run id")
    converge.add_argument(
        "--set", action="append", metavar="NAME=VALUE", help="override a design variable"
    )
    converge.add_argument("--levels", type=int, help="how many mesh densities to try (minimum 3)")
    converge.add_argument(
        "--ratio", type=float, help="how much finer each mesh is than the last (minimum 1.1)"
    )
    converge.add_argument("--no-cache", action="store_true", help="ignore cached results")
    converge.set_defaults(func=command_converge)

    topology = subparsers.add_parser(
        "topology",
        help="find where material should go, rather than tuning a shape you already have",
    )
    add_common(topology)
    topology.add_argument(
        "--deck", required=True, help="CalculiX analysis file describing the space and the loads"
    )
    topology.add_argument(
        "--keep", type=float, default=0.4, help="share of the material to keep (default 0.4)"
    )
    topology.add_argument(
        "--feature-size",
        type=float,
        default=2.0,
        help="smallest feature the result may contain, in mm (default 2.0)",
    )
    topology.add_argument(
        "--rounds", type=int, default=100, help="most rounds to run (default 100)"
    )
    topology.add_argument(
        "--removal-rate",
        type=float,
        default=0.02,
        help="share of the remaining material taken away each round (default 0.02). "
        "Larger is faster but can delete a load path before its value shows up",
    )
    topology.add_argument(
        "--smoothing",
        type=int,
        default=6,
        help="smoothing passes; rounded up to an even number (default 6)",
    )
    topology.add_argument(
        "--cores",
        type=int,
        default=1,
        help="processor cores. More than one is faster but the result stops being repeatable",
    )
    topology.add_argument("--solver", help="path to the CalculiX executable")
    topology.set_defaults(func=command_topology)

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
