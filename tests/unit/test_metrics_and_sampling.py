"""Stress measures and DOE sampling."""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.doe.sampling import sample_design_space, sample_unit
from openoptima.domain.model import StressEvaluation
from openoptima.domain.variables import DesignSpace, DesignVariable, VariableType
from openoptima.results.metrics import evaluate_stress


class TestStressMeasures:
    """Why the default is a percentile and not the raw peak.

    A singular corner produces one enormous nodal value that grows with mesh
    refinement. Optimising it means optimising the mesh.
    """

    def field_with_a_singular_spike(self) -> np.ndarray:
        # 999 nodes around 100 MPa, one artificial spike at a re-entrant corner.
        return np.concatenate([np.full(999, 100.0), np.array([5000.0])])

    def test_raw_max_follows_the_spike(self):
        result = evaluate_stress(
            self.field_with_a_singular_spike(), StressEvaluation(measure="raw_max")
        )
        assert result.value == pytest.approx(5000.0)

    def test_percentile_ignores_the_spike(self):
        result = evaluate_stress(
            self.field_with_a_singular_spike(),
            StressEvaluation(measure="percentile", percentile=99.0),
        )
        assert result.value == pytest.approx(100.0, rel=1e-6)

    def test_raw_peak_is_always_reported_even_when_not_used(self):
        result = evaluate_stress(
            self.field_with_a_singular_spike(), StressEvaluation(measure="percentile")
        )
        assert result.raw_max == pytest.approx(5000.0), (
            "the raw peak must never be hidden, only excluded from the objective"
        )

    def test_percentile_still_tracks_a_genuine_stress_rise(self):
        low = evaluate_stress(np.full(1000, 100.0), StressEvaluation())
        high = evaluate_stress(np.full(1000, 150.0), StressEvaluation())
        assert high.value > low.value

    def test_pnorm_sits_between_mean_and_max(self):
        field = np.concatenate([np.full(500, 50.0), np.full(500, 150.0)])
        result = evaluate_stress(field, StressEvaluation(measure="pnorm", pnorm_exponent=8.0))
        assert field.mean() < result.value <= field.max()

    def test_masked_nodes_are_removed_from_the_measure(self):
        field = np.concatenate([np.full(99, 100.0), np.array([9000.0])])
        mask = np.zeros(100, dtype=bool)
        mask[-1] = True
        result = evaluate_stress(field, StressEvaluation(measure="raw_max"), mask)
        assert result.value == pytest.approx(100.0)
        assert result.raw_max == pytest.approx(9000.0)
        assert result.excluded_nodes == 1

    def test_masking_everything_falls_back_rather_than_dividing_by_zero(self):
        field = np.full(10, 42.0)
        result = evaluate_stress(
            field, StressEvaluation(measure="raw_max"), np.ones(10, dtype=bool)
        )
        assert result.value == pytest.approx(42.0)

    def test_unknown_measure_is_rejected(self):
        with pytest.raises(ValueError, match="percentile must be"):
            StressEvaluation(measure="percentile", percentile=0.0)


class TestSampling:
    def test_sobol_fills_the_unit_cube(self):
        points = sample_unit("sobol", 32, 3, seed=1)
        assert points.shape == (32, 3)
        assert points.min() >= 0.0
        assert points.max() <= 1.0

    def test_sobol_is_reproducible(self):
        first = sample_unit("sobol", 16, 2, seed=7)
        second = sample_unit("sobol", 16, 2, seed=7)
        assert np.allclose(first, second)

    def test_different_seeds_give_different_samples(self):
        assert not np.allclose(
            sample_unit("sobol", 16, 2, seed=1), sample_unit("sobol", 16, 2, seed=2)
        )

    def test_sobol_spreads_better_than_random(self):
        """The whole reason for using it: fewer clusters and gaps."""
        sobol = sample_unit("sobol", 64, 2, seed=3)
        random = sample_unit("random", 64, 2, seed=3)

        def worst_gap(points: np.ndarray) -> float:
            distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
            np.fill_diagonal(distances, np.inf)
            return float(distances.min(axis=1).max())

        assert worst_gap(sobol) < worst_gap(random)

    def test_latin_hypercube_shape(self):
        assert sample_unit("lhs", 20, 4, seed=1).shape == (20, 4)

    def test_zero_samples_is_empty_not_an_error(self):
        assert sample_unit("sobol", 0, 3).shape == (0, 3)

    def test_unknown_method_is_rejected(self):
        with pytest.raises(ValueError, match="unknown sampling method"):
            sample_unit("magic", 4, 2)

    def test_samples_respect_variable_bounds_and_types(self):
        space = DesignSpace(
            (
                DesignVariable(id="t", minimum=2.0, maximum=8.0),
                DesignVariable(id="n", type=VariableType.INTEGER, minimum=1, maximum=4, default=2),
                DesignVariable(
                    id="p",
                    type=VariableType.CATEGORICAL,
                    choices=("a", "b"),
                    default="a",
                ),
            )
        )
        for design in sample_design_space(space, "sobol", 16, seed=1):
            assert 2.0 <= design["t"] <= 8.0
            assert design["n"] in (1, 2, 3, 4)
            assert isinstance(design["n"], int)
            assert design["p"] in ("a", "b")
