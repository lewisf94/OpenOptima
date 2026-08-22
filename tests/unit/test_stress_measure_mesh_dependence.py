"""The percentile stress measure is a statistic about nodes, not about the part.

OpenOptima does not optimise raw peak stress, because at a sharp corner the
peak grows without limit as the mesh is refined -- optimising it means
optimising the mesh. The default is a high percentile of the nodal field
instead. That fixes the singularity problem and introduces a quieter one.

**A percentile over nodes asks "what stress do the worst 1% of nodes see",
and the answer depends on which nodes exist.** Refine the mesh everywhere
except the hot spot and the highly stressed nodes become a smaller share of
the population, so a fixed percentile cuts lower -- and the part looks less
stressed than before, on identical physics.

**Measured on `examples/l_bracket`**, which pins its fillet refinement at
2.0 mm while the global size shrinks:

    mesh size   nodes    raw peak   stress_max_mpa   factor of safety
        8.0     14 123    71.4534       69.6897           2.2959
        4.5     30 543    71.7266       67.8165           2.3593
        2.8     78 836    71.4716       58.9137           2.7158

The raw peak is settled to ±0.2%. The percentile falls **15.5%** and is
still moving, and the factor of safety on the identical design rises
**18%** -- in the reassuring direction. The share of nodes above 60 MPa
falls from 4.895% to 0.908% over that refinement, which is the whole
mechanism in one number.

Refining the same part *uniformly*, with the local refinement removed, moves
the percentile only 2.2%. So this is not a general property of percentiles;
it is what happens when part of the mesh refines and part of it does not,
which is exactly what a local refinement is for.

These tests hold that mechanism in place without a solver, so it cannot be
changed by accident. **If the stress measure is ever changed -- to weight by
material volume rather than by node count, say -- these tests must be
updated deliberately, and every example's numbers rechecked.** That is a
decision for the project owner, not a tidy-up.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.model import StressEvaluation
from openoptima.results.metrics import evaluate_stress


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    """The percentile each point would get if it spoke for its own share."""
    order = np.argsort(values)
    ordered_values, ordered_weights = values[order], weights[order]
    cumulative = np.cumsum(ordered_weights)
    target = percentile / 100.0 * cumulative[-1]
    return float(np.interp(target, cumulative, ordered_values))


def _field(hot_points: int, cool_points: int) -> tuple[np.ndarray, np.ndarray]:
    """One stress field, sampled two ways.

    The physics is identical: a small hot region at 100 MPa and a large cool
    one ramping to 40. Only how densely each region is sampled changes, which
    is what refining a mesh unevenly does.
    """
    hot = np.linspace(80.0, 100.0, hot_points)
    cool = np.linspace(1.0, 40.0, cool_points)
    values = np.concatenate([hot, cool])
    # Each sample speaks for its region's share of the material, so the hot
    # region's total weight does not change when it is sampled more finely.
    weights = np.concatenate(
        [np.full(hot_points, 1.0 / hot_points), np.full(cool_points, 20.0 / cool_points)]
    )
    return values, weights


def test_refining_only_the_cool_part_lowers_the_reported_stress() -> None:
    """The defect, with no solver involved.

    The hot region is sampled identically in both. Only the cool region gains
    points -- which is what happens when the global mesh refines while a local
    refinement stays pinned at its own size.
    """
    settings = StressEvaluation(measure="percentile", percentile=99.0)

    coarse_values, _ = _field(hot_points=50, cool_points=950)
    fine_values, _ = _field(hot_points=50, cool_points=9500)

    coarse = evaluate_stress(coarse_values, settings)
    fine = evaluate_stress(fine_values, settings)

    # The part did not change, and neither did its worst stress.
    assert coarse.raw_max == pytest.approx(100.0)
    assert fine.raw_max == pytest.approx(100.0)

    # The reported number did change, and it fell -- the flattering direction.
    assert fine.value < coarse.value
    assert (coarse.value - fine.value) / coarse.value > 0.30


def test_weighting_by_material_holds_still_under_the_same_refinement() -> None:
    """Why the measure is the suspect rather than the mesh.

    Ask instead what stress 1% of the *material* exceeds and the answer barely
    moves, because adding sample points to a region does not add material to
    it. This is not wired into the software: it is here to show that the
    drift above is a property of the statistic, not of the physics.
    """
    coarse_values, coarse_weights = _field(hot_points=50, cool_points=950)
    fine_values, fine_weights = _field(hot_points=50, cool_points=9500)

    coarse = _weighted_percentile(coarse_values, coarse_weights, 99.0)
    fine = _weighted_percentile(fine_values, fine_weights, 99.0)

    assert abs(fine - coarse) / coarse < 0.02


def test_refining_everywhere_together_barely_moves_the_percentile() -> None:
    """The measured contrast: uniform refinement is nearly harmless.

    On the real L-bracket, refining uniformly moved the percentile 2.2% while
    refining around a pinned local refinement moved it 15.5%. The mechanism is
    the *ratio* between the regions changing, not the node count rising.
    """
    settings = StressEvaluation(measure="percentile", percentile=99.0)

    coarse = evaluate_stress(_field(hot_points=50, cool_points=950)[0], settings)
    fine = evaluate_stress(_field(hot_points=500, cool_points=9500)[0], settings)

    assert abs(fine.value - coarse.value) / coarse.value < 0.02


def test_the_raw_peak_is_reported_whatever_the_measure_does() -> None:
    """The peak is always reported beside the measure, and on a part whose hot
    spot is a real feature rather than a singularity it is the *steadier* of
    the two. Measured on the L-bracket: raw peak within +/-0.2% across 14 123
    to 78 836 nodes while the percentile fell 15.5%."""
    settings = StressEvaluation(measure="percentile", percentile=99.0)
    for cool_points in (950, 4750, 9500):
        result = evaluate_stress(_field(hot_points=50, cool_points=cool_points)[0], settings)
        assert result.raw_max == pytest.approx(100.0)
