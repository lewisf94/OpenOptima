"""Human-readable study reports.

A report that only shows the winner is a report that hides the decision.  These
show the whole front, what each step along it costs, and which design the user's
own stated preferences pick — so the choice is visible and arguable.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..domain.model import AnyMaterial
from ..domain.objectives import Direction
from ..domain.orthotropic import OrthotropicMaterial
from ..domain.project import Project
from ..domain.results import EvaluationResult
from ..domain.variables import BoundPin
from ..optimisation.pareto import (
    apply_trade_rules,
    knee_point,
    marginal_rates,
    rank_by_preference,
)
from ..optimisation.study import StudyResult


def _scope_caveat(project: Project) -> str:
    """What this study did not look at.

    Named from what the project actually switched on, rather than a fixed
    sentence: a report that says "no dynamic effects considered" beside a
    natural frequency constraint teaches a reader to stop believing the
    caveats, which are the part of a report that most needs believing.
    """
    missing = ["fatigue", "contact", "plasticity", "large deflection"]
    if not project.buckling.enabled:
        missing.insert(0, "buckling")
    covered = []
    if project.buckling.enabled:
        covered.append("buckling")
    if project.modal.enabled:
        covered.append("natural frequency")

    ran = (
        f"Linear static analysis, plus {' and '.join(covered)}."
        if covered
        else "Linear static analysis only."
    )
    return f"{ran} Not considered: {', '.join(missing)}."


def _strength_caveat(material: AnyMaterial) -> str:
    """State the strength the factor of safety was measured against.

    A printed material has no single allowable stress, so quoting one would
    have to invent it. Its weakest direction is quoted instead, because that
    is the number a reader most needs to sanity-check, alongside the criterion
    that actually did the measuring.
    """
    if isinstance(material, OrthotropicMaterial):
        if material.strength is None:
            return (
                "No factor of safety was computed: this material is stronger along "
                "its print layers than through them, and no directional strengths "
                "were given. Stresses and deflections are unaffected."
            )
        # `.strip()` because a YAML folded block ends with a newline, which
        # would break the sentence across two bullet lines in the rendered
        # report.
        return (
            f"{material.name} is printed, so it is weaker between its layers than "
            f"along them. Its weakest allowable is "
            f"{material.strength.weakest:g} MPa, on the basis: "
            f"{material.strength.basis.strip()} These are design decisions, not "
            f"properties of the plastic."
        )
    return (
        f"Allowable stress is {material.allowable_stress:g} MPa on the basis: "
        f"{material.allowable_stress_basis.strip()}"
    )


def _format_value(value: float) -> str:
    if not np.isfinite(value):
        return "n/a"
    if abs(value) >= 1000 or (abs(value) < 0.01 and value != 0):
        return f"{value:.4g}"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_(nothing to show)_\n"
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |",
        "|" + "|".join("-" * (w + 2) for w in widths) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return "\n".join(lines) + "\n"


def build_report(study: StudyResult, project: Project) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# {project.name}")
    add("")
    if project.description:
        add(project.description.strip())
        add("")

    # -- what happened -----------------------------------------------------
    summary = study.summary()
    add("## Run summary")
    add("")
    add(
        _table(
            ["Quantity", "Value"],
            [
                ["Study", str(summary["study"]) or "(unnamed)"],
                ["Kind", str(summary["kind"])],
                ["Designs evaluated", str(summary["evaluated"])],
                ["Feasible", str(summary["feasible"])],
                ["Infeasible (design rejected)", str(summary["infeasible"])],
                ["Errors (result unknown)", str(summary["errors"])],
                ["Pareto front size", str(summary["pareto_size"])],
                ["Wall time (s)", str(summary["wall_time_s"])],
            ],
        )
    )

    if study.failures:
        add("### Failures by cause")
        add("")
        add(
            "Infeasible outcomes are information about the design. Errors are not — "
            "they mean the result could not be determined, and a high error count "
            "means the setup needs attention rather than the design.\n"
        )
        add(
            _table(
                ["Cause", "Count"],
                [[code, str(count)] for code, count in study.failures.items()],
            )
        )

    # -- the trade-off -----------------------------------------------------
    add("## Trade-off")
    add("")
    if not study.front:
        add(
            "No feasible designs were found. Either the constraints cannot be met "
            "anywhere in the current variable ranges, or the ranges are too narrow. "
            "Check the infeasible results below for how close the best attempt came.\n"
        )
    else:
        objective_metrics = [o.metric for o in project.objectives]
        headers = ["Run", *[o.display_name for o in project.objectives]]
        extra = [
            m
            for m in ("factor_of_safety", "mass_kg", "displacement_max_mm")
            if m not in objective_metrics
        ]
        headers += extra

        ordered = sorted(study.front, key=lambda r: r.metric(project.objectives[0].metric))
        rows = []
        for result in ordered:
            row = [result.run_id or "-"]
            row += [_format_value(result.metric(o.metric)) for o in project.objectives]
            row += [_format_value(result.metric(m)) for m in extra]
            rows.append(row)
        add(_table(headers, rows))

        knee = knee_point(study.front, project.objectives)
        if knee:
            add(f"**Best compromise (knee of the front): run {knee.run_id}**")
            add("")
            add(_design_block(knee, project))

        # -- what each step actually costs ---------------------------------
        if len(project.objectives) >= 2:
            give, gain = project.objectives[0], project.objectives[1]
            rates = marginal_rates(study.front, give.metric, gain.metric)
            if rates:
                add("### What each step along the front costs")
                add("")
                add(
                    f"Moving up the front in {give.display_name}, this is what each "
                    f"step buys in {gain.display_name}. The point where the return "
                    f"collapses is where to stop paying.\n"
                )
                rows = []
                for rate in rates:
                    improvement = (
                        -rate.gain_delta
                        if gain.direction is Direction.MINIMISE
                        else rate.gain_delta
                    )
                    rows.append(
                        [
                            f"{_format_value(rate.give_delta)}",
                            f"{_format_value(improvement)}",
                            _format_value(abs(rate.rate)) if np.isfinite(rate.rate) else "n/a",
                        ]
                    )
                add(
                    _table(
                        [
                            f"{give.display_name} paid",
                            f"{gain.display_name} gained",
                            "Cost per unit gained",
                        ],
                        rows,
                    )
                )

    # -- preferences -------------------------------------------------------
    if project.preferences.desirability or project.preferences.trade_rules:
        add("## Ranked by your stated preferences")
        add("")
        ranked = rank_by_preference(study.front, project.preferences)
        if ranked and np.isfinite(ranked[0][1]):
            rows = [
                [
                    result.run_id or "-",
                    _format_value(score),
                    *[_format_value(result.metric(o.metric)) for o in project.objectives],
                ]
                for result, score in ranked[:10]
            ]
            add(
                _table(
                    ["Run", "Desirability", *[o.display_name for o in project.objectives]],
                    rows,
                )
            )
            add(
                "Desirability is the weighted geometric mean of your per-metric ramps. "
                "It is zero if any metric falls outside its acceptable range — one bad "
                "number is not compensated by a good one.\n"
            )

        chosen = apply_trade_rules(study.front, project.preferences.trade_rules)
        if chosen is not None:
            rule = project.preferences.trade_rules[0]
            add(
                f"**Your trade rule** — pay up to {rule.give_amount:g} of "
                f"{rule.give_metric} per {rule.gain_amount:g} of {rule.gain_metric} — "
                f"**selects run {chosen.run_id}**."
            )
            add("")
            add(_design_block(chosen, project))

    # -- sensitivity -------------------------------------------------------
    if study.sensitivity:
        add("## Which variables matter")
        add("")
        add(
            "Rank correlation across every evaluated design. This is screening: it "
            "ranks influence, it does not resolve interactions.\n"
        )
        for metric, report in study.sensitivity.items():
            add(f"**{metric}** (n={report.sample_count})")
            add("")
            add(
                _table(
                    ["Variable", "Spearman rho", "R2", "Effect across range"],
                    [
                        [
                            effect.variable,
                            f"{effect.spearman:+.3f}",
                            f"{effect.linear_r2:.3f}",
                            _format_value(effect.span_effect),
                        ]
                        for effect in report.ranked
                    ],
                )
            )
            weak = report.unimportant()
            if weak:
                add(
                    f"Little measured influence on {metric}: {', '.join(weak)}. "
                    f"Consider fixing these to shrink the search.\n"
                )

    # -- caveats -----------------------------------------------------------
    add("## Before you trust this")
    add("")
    add(
        "- Results come from a single mesh setting. Re-run the chosen design at a "
        "finer mesh and confirm the numbers have converged.\n"
        f"- Stress is reported as the **{project.stress_evaluation.measure}** measure"
        + (
            f", excluding {', '.join(project.stress_evaluation.excluded_regions)}"
            if project.stress_evaluation.excluded_regions
            else ""
        )
        + ". The raw peak is recorded on every result and will be higher.\n"
        f"- {_strength_caveat(project.material)}\n"
        f"- {_scope_caveat(project)}\n"
    )

    warned = [r for r in study.front if r.warnings]
    if warned:
        add("### Warnings on front designs")
        add("")
        for result in warned[:10]:
            for warning in result.warnings[:3]:
                add(f"- run {result.run_id}: {warning}")
        add("")

    return "\n".join(lines)


def _design_block(result: EvaluationResult, project: Project) -> str:
    pinned = {
        pin.variable_id: pin
        for pin in project.design_space.pinned_variables(result.design.as_dict())
    }
    lines = ["```"]
    for variable in project.design_space:
        value = result.design.get(variable.id)
        unit = f" {variable.unit}" if variable.unit else ""
        pin = pinned.get(variable.id)
        note = f"   <- at its {pin.bound}" if pin else ""
        lines.append(f"{variable.display_name:<32} {value}{unit}{note}")
    lines.append("")
    for metric in ("mass_kg", "displacement_max_mm", "stress_max_mpa", "factor_of_safety"):
        if metric in result.metrics:
            lines.append(f"{metric:<32} {_format_value(result.metrics[metric])}")
    lines.append("```")
    block = "\n".join(lines) + "\n"
    if pinned:
        block += "\n" + _pinned_note(tuple(pinned.values())) + "\n"
    return block


def _pinned_note(pins: tuple[BoundPin, ...]) -> str:
    """Explain what a value sitting on its own limit actually means.

    Without this the reader sees a number and assumes the search chose it. It
    did not: it went as far as it was allowed and stopped. That is worth
    knowing, because widening the range may well find a better part.
    """
    lines = ["**Some of these values are sitting on the limits you set.**", ""]
    for pin in pins:
        lines.append(f"- {pin.describe()}")
    lines.append("")
    lines.append(
        "Widen the range and run again to find out whether there is something "
        "better beyond it. If a limit is there for a reason -- a minimum fillet "
        "your supplier can cut, a maximum size that has to fit -- then the "
        "answer is right, and this is only telling you the limit is what "
        "decided it."
    )
    return "\n".join(lines)


def write_report(study: StudyResult, project: Project, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(study, project), encoding="utf-8")
    return path
