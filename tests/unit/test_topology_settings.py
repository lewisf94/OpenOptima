"""Topology optimisation settings, and the rules that keep a result makeable.

The danger with topology optimisation is not that it fails. It is that it
succeeds and hands back a shape that is optimal and impossible: members thinner
than any tool can cut, or a checkerboard of material whose stiffness is an
artefact of the mesh. The minimum feature size is what stops that, and these
tests pin the rules that make it mean something.
"""

from __future__ import annotations

import pytest

from openoptima.domain.topology import TopologySettings, is_converged


class TestSettingsRejectNonsense:
    """An impossible setting must be refused, not quietly clamped."""

    @pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
    def test_a_volume_fraction_outside_zero_to_one_is_rejected(self, fraction):
        with pytest.raises(ValueError, match="volume_fraction"):
            TopologySettings(volume_fraction=fraction)

    def test_a_feature_size_of_zero_is_rejected(self):
        """Without one, the optimiser may invent members nothing can make."""
        with pytest.raises(ValueError, match="minimum_feature_size_mm"):
            TopologySettings(minimum_feature_size_mm=0.0)

    def test_a_negative_filter_radius_is_rejected(self):
        with pytest.raises(ValueError, match="filter_radius_mm"):
            TopologySettings(filter_radius_mm=-1.0)

    def test_zero_iterations_is_rejected(self):
        with pytest.raises(ValueError, match="maximum_iterations"):
            TopologySettings(maximum_iterations=0)

    @pytest.mark.parametrize("rate", [0.0, 1.0, 2.0])
    def test_an_impossible_evolution_rate_is_rejected(self, rate):
        with pytest.raises(ValueError, match="evolution_rate"):
            TopologySettings(evolution_rate=rate)

    def test_the_error_says_what_the_setting_is_for(self):
        """A number out of range is a chance to explain, not just to refuse."""
        with pytest.raises(ValueError) as info:
            TopologySettings(volume_fraction=1.5)
        assert "share of the starting material" in str(info.value)


class TestTheFilterHoldsTheFeatureSize:
    """The filter radius is what makes a minimum feature size real.

    The filter blurs material over its own radius, so a feature narrower than
    twice that radius cannot survive it. Get this relationship wrong and the
    stated minimum feature size is decoration.
    """

    def test_the_default_radius_is_half_the_feature_size(self):
        settings = TopologySettings(minimum_feature_size_mm=3.0)
        assert settings.effective_filter_radius_mm == 1.5

    def test_an_explicit_radius_wins(self):
        settings = TopologySettings(minimum_feature_size_mm=3.0, filter_radius_mm=2.0)
        assert settings.effective_filter_radius_mm == 2.0

    def test_a_radius_too_small_to_hold_the_feature_size_is_called_out(self):
        settings = TopologySettings(minimum_feature_size_mm=4.0, filter_radius_mm=0.5)
        warnings = settings.feature_size_warnings()
        assert any("not be manufacturable" in w for w in warnings)

    def test_a_matching_radius_says_nothing(self):
        settings = TopologySettings(minimum_feature_size_mm=4.0, filter_radius_mm=2.0)
        assert settings.feature_size_warnings() == []


class TestTheMeshHasToBeFineEnough:
    def test_it_says_how_fine_the_mesh_must_be(self):
        """Three elements across a feature is the usual minimum to draw one."""
        settings = TopologySettings(minimum_feature_size_mm=3.0)
        assert settings.required_element_size_mm() == 1.0

    def test_a_mesh_too_coarse_for_the_feature_size_is_called_out(self):
        settings = TopologySettings(minimum_feature_size_mm=3.0)
        warnings = settings.feature_size_warnings(element_size_mm=5.0)
        assert any("artefact of the mesh" in w for w in warnings)

    def test_a_fine_enough_mesh_says_nothing(self):
        settings = TopologySettings(minimum_feature_size_mm=3.0)
        assert settings.feature_size_warnings(element_size_mm=0.8) == []


class TestWarningsThatDoNotStopTheRun:
    """Judgement calls stay the engineer's; silence is not an option though."""

    def test_a_very_aggressive_volume_fraction_is_flagged(self):
        warnings = TopologySettings(volume_fraction=0.05).feature_size_warnings()
        assert any("disconnected" in w for w in warnings)

    def test_a_fast_evolution_rate_is_flagged(self):
        warnings = TopologySettings(evolution_rate=0.25).feature_size_warnings()
        assert any("load path can be deleted" in w for w in warnings)

    def test_sensible_settings_produce_no_noise(self):
        assert TopologySettings().feature_size_warnings() == []


class TestTheDigestCoversEverythingThatChangesAResult:
    """Anything affecting the answer must reach the evaluation hash.

    Miss one and a stale result is served as a fresh one, which is the quietest
    way this project can be wrong.
    """

    def test_every_setting_that_matters_is_in_the_digest(self):
        digest = TopologySettings().digest_fields()
        for field in (
            "volume_fraction",
            "minimum_feature_size_mm",
            "filter_radius_mm",
            "maximum_iterations",
            "evolution_rate",
        ):
            assert field in digest

    def test_changing_a_setting_changes_the_digest(self):
        assert (
            TopologySettings().digest_fields()
            != TopologySettings(volume_fraction=0.4).digest_fields()
        )

    def test_the_digest_records_the_radius_actually_used(self):
        """Not the unset default, or two different runs would hash the same."""
        derived = TopologySettings(minimum_feature_size_mm=4.0)
        explicit = TopologySettings(minimum_feature_size_mm=4.0, filter_radius_mm=2.0)
        assert derived.digest_fields() == explicit.digest_fields()


class TestConvergence:
    """ "It stopped moving" has to mean more than one quiet round.

    Material comes off in steps, and one step can happen to be small. Calling
    that convergence would stop a run that had not settled and report whatever
    shape it was holding.
    """

    def test_a_settled_run_is_converged(self):
        assert is_converged([10.0] * 12)

    def test_a_run_still_moving_is_not(self):
        assert not is_converged([float(10 - i) for i in range(12)])

    def test_too_little_history_is_not_converged(self):
        """Not converged is the safe answer when there is nothing to judge."""
        assert not is_converged([10.0, 10.0, 10.0])

    def test_one_quiet_round_is_not_enough(self):
        history = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 4.0, 3.99]
        assert not is_converged(history)

    def test_a_run_that_went_bad_is_not_converged(self):
        assert not is_converged([10.0] * 10 + [float("nan")] * 2)

    def test_an_all_zero_history_is_converged(self):
        assert is_converged([0.0] * 12)

    def test_the_window_must_be_sensible(self):
        with pytest.raises(ValueError, match="window"):
            is_converged([1.0] * 12, window=0)
