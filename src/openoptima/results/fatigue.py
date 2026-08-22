"""Turning two solved load cases into the stress swing a fatigue check needs.

The rule this implements, and why it is not a subtraction of the numbers
OpenOptima already reports, is set out in ``domain/fatigue.py``. In short: von
Mises stress keeps the size of a stress state and throws away its direction,
so subtracting two von Mises values reports a fully reversed load -- the most
damaging cycle there is -- as no swing at all. The six-number stress tensors
are subtracted instead, and only the result is reduced to one number.

**Amplitude is a magnitude and the mean is not.** How far the stress moves has
no direction, so the amplitude is always positive. The middle of the swing
does have one, and it decides how damaging that swing is: a mean that pulls
the material apart holds a crack open, and one that presses it together holds
it shut. So the mean keeps its sign, and which convention gives it that sign
is the engineer's choice -- see ``EquivalentStress``.

**The two numbers are reported at the same node, deliberately.** An amplitude
from one point in the part and a mean from another describe no real place, and
a fatigue assessment made from that pair would be meaningless. So the mean is
always the mean *at the node with the worst swing*.

The arithmetic itself is pyLife's, per ``docs/capability-audit.md``. It was
checked against this project's own von Mises on 19 787 real nodes and agreed
to 1.4e-14 MPa, which is arithmetic noise -- reuse saves writing the code,
never proving the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.fatigue import EquivalentStress, FatigueCurve, FatigueSettings, LoadCycle
from ..domain.model import StressEvaluation
from ..domain.regions import RegionMap
from ..meshing.base import MeshData
from ..solvers.base import LoadCaseFields

#: The state a published fatigue curve is normally measured in: pushed as
#: hard one way as the other, so the mean stress is nothing. A measured swing
#: is corrected to this before the curve is read.
_FULLY_REVERSED = -1.0


def _equistress() -> Any:
    """Import pyLife only when a project actually asks for fatigue."""
    try:
        from pylife.stress import equistress
    except ImportError as exc:  # pragma: no cover - exercised on a bare install
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            "measuring a stress swing needs the 'pylife' package, which is not "
            "installed. Install it with 'pip install openoptima[fatigue]'.",
        ) from exc
    return equistress


def _columns(tensor: np.ndarray) -> dict[str, np.ndarray]:
    """Our (N, 6) tensor in pyLife's naming.

    Our column order is sxx, syy, szz, sxy, syz, szx. pyLife asks for s11,
    s22, s33, s12, s23, s13 -- note that its third shear is s13 where ours is
    szx. Those are the same component: a stress tensor is symmetric, so
    szx == sxz. Spelled out because getting a shear component into the wrong
    slot would rotate the answer rather than break it.
    """
    return {
        "s11": tensor[:, 0],
        "s22": tensor[:, 1],
        "s33": tensor[:, 2],
        "s12": tensor[:, 3],
        "s23": tensor[:, 4],
        "s13": tensor[:, 5],
    }


@dataclass(frozen=True)
class CycleStress:
    """The swing one named cycle puts the part through."""

    name: str
    between: tuple[str, str]
    #: Largest swing anywhere the stress measure is allowed to look, in MPa.
    #: Half the difference between the two ends, so it compares directly with
    #: an amplitude read off a published fatigue curve.
    amplitude_max: float
    #: The middle of the swing at that same node, in MPa. Positive pulls the
    #: material apart.
    mean_at_worst: float
    #: The amplitude field reduced by the project's own stress measure -- the
    #: percentile or p-norm it already uses for static stress. This is the
    #: number to optimise; ``amplitude_max`` is the number to read.
    amplitude_measure: float
    measure_name: str
    #: Largest swing including every node, whatever was excluded. Always
    #: reported so nothing is hidden, exactly as the static peak is.
    amplitude_raw_max: float

    def as_metrics(self, suffix: str = "") -> dict[str, float]:
        """Metric names, optionally tagged with the cycle they came from.

        The suffix follows the convention already used for per-load-case
        metrics: ``fatigue_amplitude_mpa.each_propeller_turn``.
        """
        tag = f".{suffix}" if suffix else ""
        return {
            f"fatigue_amplitude_mpa{tag}": self.amplitude_measure,
            f"fatigue_amplitude_max_mpa{tag}": self.amplitude_max,
            f"fatigue_amplitude_raw_max_mpa{tag}": self.amplitude_raw_max,
            f"fatigue_mean_mpa{tag}": self.mean_at_worst,
        }


def cycle_stress(
    cycle: LoadCycle,
    fields_by_case: dict[str, LoadCaseFields],
    settings: FatigueSettings,
    evaluation: StressEvaluation,
    mask: np.ndarray | None = None,
) -> CycleStress:
    """The amplitude and mean stress of one cycle, from two solved load cases."""
    first, second = cycle.between
    for name in (first, second):
        if name not in fields_by_case:
            raise EvaluationFailure(
                FailureCode.FATIGUE_CYCLE_INCOMPLETE,
                f"load cycle {cycle.name!r} names load case {name!r} at one end of "
                f"its swing, and no such load case was solved. Known load cases: "
                f"{sorted(fields_by_case)}.",
            )

    low = fields_by_case[first]
    high = fields_by_case[second]

    if low.stress_tensor is None or high.stress_tensor is None:
        raise EvaluationFailure(
            FailureCode.FATIGUE_CYCLE_INCOMPLETE,
            f"load cycle {cycle.name!r} needs the full stress state at both ends of "
            f"its swing, and at least one end produced none. A stress swing cannot "
            f"be measured from von Mises stress alone -- see domain/fatigue.py.",
        )

    # Checked, not assumed. Both fields come from the same mesh and in practice
    # arrive in the same order, but subtracting two arrays that were ordered
    # differently would pair up the wrong nodes and give a plausible, wrong
    # answer with nothing to show for it.
    if not np.array_equal(np.asarray(low.node_tags), np.asarray(high.node_tags)):
        raise EvaluationFailure(
            FailureCode.FATIGUE_CYCLE_INCOMPLETE,
            f"load cases {first!r} and {second!r} list their nodes in different "
            f"orders, so the two ends of cycle {cycle.name!r} cannot be compared "
            f"node for node.",
        )

    a = np.asarray(low.stress_tensor, dtype=float)
    b = np.asarray(high.stress_tensor, dtype=float)
    amplitude_tensor = (b - a) / 2.0
    mean_tensor = (b + a) / 2.0

    equistress = _equistress()
    # An amplitude is how far the stress moved, which has no direction, so the
    # unsigned von Mises is what it is. The mean does have one.
    amplitude = np.asarray(equistress.mises(**_columns(amplitude_tensor)), dtype=float)
    signer = (
        equistress.signed_mises_trace
        if settings.equivalent_stress is EquivalentStress.SIGNED_MISES_TRACE
        else equistress.signed_mises_abs_max_principal
    )
    mean = np.asarray(signer(**_columns(mean_tensor)), dtype=float)

    raw_max = float(amplitude.max()) if amplitude.size else 0.0

    allowed = np.ones(amplitude.shape, dtype=bool)
    if mask is not None and mask.any() and mask.shape == amplitude.shape:
        allowed = ~mask
    if not allowed.any():
        allowed = np.ones(amplitude.shape, dtype=bool)

    working = amplitude[allowed]
    worst_row = int(np.flatnonzero(allowed)[int(np.argmax(working))]) if working.size else 0

    return CycleStress(
        name=cycle.name,
        between=(first, second),
        amplitude_max=float(amplitude[worst_row]) if amplitude.size else 0.0,
        mean_at_worst=float(mean[worst_row]) if mean.size else 0.0,
        amplitude_measure=_reduce(working, evaluation),
        measure_name=_measure_name(evaluation),
        amplitude_raw_max=raw_max,
    )


def cycle_life(cycle: CycleStress, curve: FatigueCurve) -> float:
    """How many of this swing the material survives, from the supplied curve.

    Two steps, both pyLife's. The swing is first corrected for its mean
    stress -- a swing about a mean that pulls the material apart is more
    damaging than the same swing about nothing -- and the corrected swing is
    then looked up on the curve.

    **Computed at the point with the worst swing, not at a percentile.** A
    fatigue crack starts at the hottest point, so a life computed from a
    percentile would be a life for somewhere the part will not crack. That is
    a departure from how the static stress measure works, and it is a
    measured one rather than a preference: on ``examples/l_bracket`` the peak
    swing held to within 0.2% across 14 123 to 78 836 nodes while the 99th
    percentile fell 15.5% over the same refinement. Here the peak is the
    steadier number. See ``AGENTS.md`` trap 23.

    **That only holds where the hottest point is a real feature.** At a sharp
    inside corner or a fully fixed face the peak grows without limit however
    fine the mesh, and a life computed there is a statement about the mesh.
    OpenOptima cannot tell the difference from a single mesh, so it says so
    on every result and leaves the judgement where it belongs.

    Returns ``inf`` when the swing is below the curve's endurance limit. That
    means "below the limit of the curve you supplied", never "this part will
    not break".
    """
    if curve.mean_stress_sensitivity is None:
        raise EvaluationFailure(
            FailureCode.FATIGUE_CURVE_INCOMPLETE,
            f"a fatigue life for cycle {cycle.name!r} needs to know how much a mean "
            f"stress matters for this material, and no mean_stress_sensitivity was "
            f"given. The swing here sits about a mean of {cycle.mean_at_worst:.4g} MPa, "
            f"and a swing about a mean that pulls the material apart is more damaging "
            f"than the same swing about nothing -- assuming otherwise is wrong in the "
            f"unsafe direction. State it on the fatigue curve; if your cycle is fully "
            f"reversed it changes nothing.",
        )

    import pandas as pd
    from pylife.strength.meanstress import fkm_goodman

    # Correct the swing to its fully reversed equivalent, which is the state
    # a published curve is normally measured in.
    corrected = float(
        np.asarray(
            fkm_goodman(
                np.array([cycle.amplitude_max]),
                np.array([cycle.mean_at_worst]),
                curve.mean_stress_sensitivity,
                curve.second_sensitivity,
                _FULLY_REVERSED,
            )
        )[0]
    )

    fields = {
        "SD": curve.endurance_stress,
        "ND": curve.endurance_cycles,
        "k_1": curve.slope,
    }
    if curve.slope_beyond is not None:
        fields["k_2"] = curve.slope_beyond
    woehler = pd.Series(fields).woehler
    return float(np.asarray(woehler.cycles(corrected)).reshape(-1)[0])


def _reduce(field: np.ndarray, evaluation: StressEvaluation) -> float:
    """Reduce the amplitude field the same way the static stress field is.

    Deliberately the project's existing measure rather than a second policy.
    A re-entrant corner is just as singular for a stress swing as for a steady
    stress -- the peak there grows without bound as the mesh is refined, so
    optimising it would mean optimising the mesh.
    """
    if field.size == 0:
        return 0.0
    measure = evaluation.measure
    if measure in ("raw_max", "region_max"):
        return float(np.max(field))
    if measure == "percentile":
        return float(np.percentile(field, evaluation.percentile))
    if measure == "pnorm":
        exponent = evaluation.pnorm_exponent
        scale = float(np.max(field)) or 1.0
        normalised = field / scale
        return float(scale * (np.sum(normalised**exponent) / field.size) ** (1.0 / exponent))
    raise ValueError(f"unknown stress measure {measure!r}")  # pragma: no cover


def _measure_name(evaluation: StressEvaluation) -> str:
    measure = evaluation.measure
    if measure == "percentile":
        return f"p{evaluation.percentile:g} percentile"
    if measure == "pnorm":
        return f"p-norm (p={evaluation.pnorm_exponent:g})"
    if measure == "region_max":
        return "maximum outside excluded regions"
    return "raw maximum"


def fatigue_metrics(
    settings: FatigueSettings,
    fields_by_case: dict[str, LoadCaseFields],
    mesh: MeshData,
    regions: RegionMap,
    evaluation: StressEvaluation,
) -> tuple[dict[str, float], list[CycleStress], list[str]]:
    """Measure every declared cycle and envelope them.

    **Enveloped, never averaged.** Several cycles are several different things
    the part has to survive, and averaging a mild one against a severe one
    hides the severe one. The reported numbers belong to the worst cycle.

    "Worst" here means the largest swing. Once a life is computed from a
    fatigue curve that changes: the most damaging cycle is then the one that
    uses up the most life, which is not always the one that swings furthest,
    because a mean stress that pulls the material apart makes a smaller swing
    more damaging.
    """
    if not settings.enabled or not settings.cycles:
        return ({}, [], [])

    from .metrics import excluded_node_mask

    any_fields = next(iter(fields_by_case.values()), None)
    mask = (
        excluded_node_mask(mesh, regions, evaluation, any_fields.node_tags)
        if any_fields is not None
        else None
    )

    measured = [
        cycle_stress(cycle, fields_by_case, settings, evaluation, mask) for cycle in settings.cycles
    ]
    governing = max(measured, key=lambda c: c.amplitude_max)

    metrics = governing.as_metrics()
    warnings: list[str] = []
    if len(measured) > 1:
        warnings.append(
            f"{len(measured)} load cycles measured; the reported swing is the worst "
            f"of them, {governing.name!r} at {governing.amplitude_max:.4g} MPa"
        )

    if settings.curve is not None:
        metrics.update(_life_metrics(settings, measured, warnings))

    return (metrics, measured, warnings)


def _life_metrics(
    settings: FatigueSettings,
    measured: list[CycleStress],
    warnings: list[str],
) -> dict[str, float]:
    """Life in cycles, and the damage several cycles do between them.

    **Which cycle governs changes once a curve is involved.** Without one the
    worst cycle is simply the one that swings furthest. With one it is the one
    that uses up the most life, and that is not always the same cycle: a
    smaller swing about a mean that pulls the material apart can be more
    damaging than a larger swing about nothing.
    """
    curve = settings.curve
    assert curve is not None
    lives = {cycle.name: cycle_life(cycle, curve) for cycle in measured}

    shortest = min(lives.values())
    metrics: dict[str, float] = {"fatigue_life_cycles": shortest}
    for name, life in lives.items():
        metrics[f"fatigue_life_cycles.{name}"] = life

    # Miner's rule: each cycle uses up a share of the life, and the shares add
    # up. At 1.0 the part is used up. Only computed when every cycle says how
    # many times it happens -- a total built from some of the cycles is not a
    # smaller total, it is a wrong one.
    counts = {cycle.name: cycle.repeats for cycle in settings.cycles}
    if all(count is not None for count in counts.values()):
        metrics["fatigue_damage"] = sum(
            (counts[name] or 0.0) / life for name, life in lives.items() if life > 0.0
        )
    elif any(count is not None for count in counts.values()):
        named = sorted(name for name, count in counts.items() if count is None)
        warnings.append(
            f"no damage total was added up because {', '.join(named)} "
            f"{'does' if len(named) == 1 else 'do'} not say how many times "
            f"{'it happens' if len(named) == 1 else 'they happen'}. A total built "
            f"from only some of the cycles would understate the damage."
        )

    # Said on every result, because the software cannot check it from one mesh.
    finite = [name for name, life in lives.items() if life != float("inf")]
    if finite:
        warnings.append(
            "fatigue life is computed at the point with the worst swing, which is "
            "where a crack starts. That number is only meaningful if the stress "
            "there has settled: run 'openoptima converge' on the design you intend "
            "to use. A life from a fatigue curve is commonly out by a factor of "
            "three even when everything is done properly."
        )
    else:
        warnings.append(
            "every cycle swings less than the endurance limit of the fatigue curve "
            "supplied, so the life is reported as unlimited. That means below the "
            "limit of that curve, never that the part cannot break."
        )
    return metrics
