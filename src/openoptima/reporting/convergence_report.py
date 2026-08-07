"""The mesh convergence report.

The reader of this report has one question: can I trust the number my study
gave me? The report answers it by showing what each quantity did as the mesh
was refined, and how wide the remaining uncertainty is.

It deliberately does not answer a second question -- "is that good enough?".
That is the engineer's decision, and ``AGENTS.md`` puts it out of the
software's reach.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..convergence.study import ConvergenceStudy
from ..domain.convergence import Behaviour
from ..domain.project import Project
from .report import _table

#: Plain wording for each observed behaviour, and what the reader should do.
_BEHAVIOUR_WORDS = {
    Behaviour.SETTLING: "settling",
    Behaviour.DIVERGING: "running away",
    Behaviour.OSCILLATING: "unsteady",
    Behaviour.FLAT: "unchanged",
    Behaviour.NOT_ENOUGH_DATA: "not enough data",
}


def _number(value: float | None, digits: int = 4) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    if abs(value) >= 1000 or (abs(value) < 0.01 and value != 0):
        return f"{value:.{digits}g}"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _percent(fraction: float | None) -> str:
    """Format a fraction as a percentage, keeping small values readable.

    Two decimal places would render 0.0036% as "0.00%", which reads as zero
    and hides the difference between "settled to four parts in a hundred
    thousand" and "not measured".
    """
    if fraction is None or not math.isfinite(fraction):
        return "n/a"
    percent = fraction * 100.0
    if percent == 0.0:
        return "0%"
    if abs(percent) < 0.01:
        return f"{percent:.1e}%"
    if abs(percent) < 1.0:
        return f"{percent:.3f}%"
    return f"{percent:.2f}%"


def _level_status(outcome) -> str:
    if outcome.infeasible:
        return "solved (design breaks its limits)"
    if outcome.usable:
        return "solved"
    return (outcome.error or "failed")[:40]


def build_convergence_report(assessment: ConvergenceStudy, project: Project) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# Mesh convergence — {assessment.project_name}")
    add("")
    add("One design, run at several mesh densities, to find out whether its")
    add("numbers have stopped changing.")
    add("")
    add(f"**Design:** {assessment.design.canonical_text().replace(chr(10), ', ')}")
    add("")

    # -- what was run ------------------------------------------------------
    add("## The meshes")
    add("")
    rows = []
    for outcome in assessment.outcomes:
        mesh = outcome.result.mesh if outcome.result else None
        rows.append(
            [
                outcome.level.label,
                _number(outcome.level.requested_size, 3),
                _number(outcome.achieved_size, 3),
                f"{mesh.node_count:,}" if mesh else "—",
                f"{mesh.element_count:,}" if mesh else "—",
                _number(mesh.min_scaled_jacobian, 3) if mesh else "—",
                _level_status(outcome),
            ]
        )
    add(
        _table(
            [
                "level",
                "size asked (mm)",
                "size got (mm)",
                "nodes",
                "elements",
                "worst element",
                "outcome",
            ],
            rows,
        )
    )
    add("")
    add("`size got` is the average element size the mesher actually produced.")
    add("The comparison below uses that, not the size that was requested: a")
    add("mesher never delivers exactly what it is asked for, and comparing")
    add("meshes by the request would give the wrong answer.")
    add("")

    if any(outcome.infeasible for outcome in assessment.outcomes):
        add(
            "This design breaks one or more of its own limits. That does not "
            "affect this report. Whether a design passes its limits and "
            "whether its numbers have settled are separate questions, and "
            "this report answers only the second one. Checking a design that "
            "sits right on a limit is often exactly what you want to do."
        )
        add("")

    usable = assessment.usable_levels
    if len(usable) < 3:
        add("## Not enough meshes succeeded")
        add("")
        add(
            f"Only {len(usable)} of {len(assessment.outcomes)} meshes produced a "
            "result. Three are needed before anything can be said about whether "
            "the numbers have settled. The `outcome` column above says what "
            "went wrong."
        )
        add("")
        return "\n".join(lines)

    # -- the numbers -------------------------------------------------------
    add("## What each number did")
    add("")
    rows = []
    for name, metric in assessment.metrics.items():
        rows.append(
            [
                name,
                _number(metric.finest_value),
                _BEHAVIOUR_WORDS[metric.behaviour],
                _percent(metric.spread),
                _number(metric.observed_order, 2),
                _number(metric.extrapolated),
                _percent(metric.uncertainty),
            ]
        )
    add(
        _table(
            [
                "metric",
                "finest mesh",
                "behaviour",
                "moved by",
                "rate",
                "heading for",
                "uncertainty",
            ],
            rows,
        )
    )
    add("")
    add("**How to read this table.**")
    add("")
    add("- **finest mesh** — the value from the finest mesh that ran.")
    add("- **behaviour** — whether the value settled as the mesh got finer.")
    add("- **moved by** — the total change across every mesh tried. Read this")
    add("  next to the behaviour: a value can be called unsteady and still")
    add("  have moved by only a twentieth of one percent, which is a very")
    add("  different situation from one that moved by ten percent.")
    add("- **rate** — how fast it settled, in powers of element size. A rate")
    add("  of 2 means halving the element size cuts the remaining error by")
    add("  four. A negative rate means the number grew instead of settling.")
    add("- **heading for** — the value a perfect mesh would give, estimated")
    add("  from the trend.")
    add("- **uncertainty** — how far the finest mesh's value is estimated to")
    add("  sit from that perfect-mesh value.")
    add("")
    add("**This report does not tell you whether a number is good enough to")
    add("use.** It tells you how far it might still move. Deciding whether")
    add("that is acceptable is your call, not the software's.")
    add("")

    # -- the warnings ------------------------------------------------------
    diverging = assessment.diverging()
    if diverging:
        add("## Numbers that will never settle")
        add("")
        add(
            "These grew with every refinement instead of settling. Refining "
            "further will not fix them."
        )
        add("")
        for metric in diverging:
            add(f"- **{metric.metric}** — {metric.note}")
        add("")
        if any(m.metric == "stress_raw_max_mpa" for m in diverging):
            add(
                "`stress_raw_max_mpa` running away is normal and expected. The "
                "true stress at a sharp internal corner or at a fully fixed "
                "face is infinite, so its computed peak climbs forever. This "
                "is exactly why OpenOptima does not optimise the raw peak by "
                "default. If the measure driving your objective is settling, "
                "your results are unaffected."
            )
            add("")

    unsettled = assessment.unsettled()
    if unsettled:
        add("## Numbers that could not be assessed")
        add("")
        for metric in unsettled:
            add(f"- **{metric.metric}** — {metric.note}")
        add("")

    flagged = [
        m for m in assessment.metrics.values() if m.behaviour is Behaviour.SETTLING and m.note
    ]
    if flagged:
        add("## Estimates to treat with caution")
        add("")
        add(
            "These settled, but not in the well-behaved way the uncertainty "
            "band assumes. Read their bands as optimistic."
        )
        add("")
        for metric in flagged:
            add(f"- **{metric.metric}** — {metric.note}")
        add("")

    # -- the control -------------------------------------------------------
    mass = assessment.metrics.get("mass_kg")
    if mass is not None and mass.behaviour not in (Behaviour.FLAT, Behaviour.SETTLING):
        add("## Warning: the shape changed between meshes")
        add("")
        add(
            "`mass_kg` depends on the shape, not on the analysis, so it should "
            "barely move between meshes. It did move. That means the geometry "
            "was not rebuilt identically at each level, and nothing else in "
            "this report can be relied on. Check the geometry template before "
            "reading anything above."
        )
        add("")

    add("---")
    add("")
    add(f"Ran {len(assessment.outcomes)} meshes in {assessment.wall_time:.1f} s.")
    add(f"Stress measure: {project.stress_evaluation.measure}.")
    add("")
    return "\n".join(lines)


def write_convergence_report(assessment: ConvergenceStudy, project: Project, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_convergence_report(assessment, project), encoding="utf-8")
    return path


def write_convergence_json(assessment: ConvergenceStudy, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(assessment.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


def summarise_for_terminal(assessment: ConvergenceStudy) -> list[str]:
    """The short version printed at the end of a run."""
    lines: list[str] = []
    usable = assessment.usable_levels
    if len(usable) < 3:
        lines.append(
            f"Only {len(usable)} of {len(assessment.outcomes)} meshes succeeded. "
            "Three are needed to say anything about convergence."
        )
        return lines

    for name, metric in assessment.metrics.items():
        word = _BEHAVIOUR_WORDS[metric.behaviour]
        head = f"  {name:<22} {_number(metric.finest_value):>12}   "
        moved = f"moved {_percent(metric.spread)} across all meshes"
        if metric.behaviour is Behaviour.SETTLING:
            lines.append(f"{head}{word} to within {_percent(metric.uncertainty)}, {moved}")
        elif metric.behaviour is Behaviour.FLAT:
            lines.append(f"{head}{word}")
        else:
            lines.append(f"{head}{word}, {moved}")
    return lines
