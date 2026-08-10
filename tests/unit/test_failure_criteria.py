"""Directional failure criteria, checked against answers known in closed form.

Every case here has an exact expected value that comes from somewhere other
than this code: von Mises for the isotropic reduction, the definition of a
strength for the single-axis cases, and the criterion's own defining equation
for the factor of safety.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openoptima.domain.failure_criteria import (
    Hoffman,
    MaximumStress,
    criterion_for,
)
from openoptima.domain.model import StressEvaluation
from openoptima.domain.orthotropic import (
    DirectionalStrength,
    InadmissibleMaterial,
    OrthotropicMaterial,
)
from openoptima.results.directional import (
    directional_margin,
    evaluate_field,
    to_material_axes,
)


def von_mises(stress: tuple[float, ...]) -> float:
    s11, s22, s33, s12, s23, s31 = stress
    return math.sqrt(
        0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2)
        + 3.0 * (s12**2 + s23**2 + s31**2)
    )


def isotropic_strength(allowable: float = 250.0) -> DirectionalStrength:
    """Equal in every direction, with the shear strength von Mises implies."""
    return DirectionalStrength(
        tension=(allowable,) * 3,
        compression=(allowable,) * 3,
        shear=(allowable / math.sqrt(3.0),) * 3,
    )


PRINTED = DirectionalStrength(
    tension=(50.0, 50.0, 30.0),
    compression=(60.0, 60.0, 55.0),
    shear=(25.0, 25.0, 28.0),
    basis="illustrative, not a real datasheet",
)


class TestIsotropicReduction:
    """With equal strengths, Hoffman must reproduce von Mises exactly.

    This is the anchor for the whole module. If it holds, the coefficient
    algebra and the factor-of-safety solve are both right in the one case
    where an independent answer exists.
    """

    @pytest.mark.parametrize(
        "stress",
        [
            (100.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            (100.0, -40.0, 20.0, 30.0, -10.0, 5.0),
            (0.0, 0.0, 0.0, 60.0, 0.0, 0.0),
            (-15.0, 90.0, 33.0, -12.0, 44.0, -7.0),
        ],
    )
    def test_matches_von_mises(self, stress):
        allowable = 250.0
        criterion = Hoffman.from_strength(isotropic_strength(allowable))
        result = criterion.evaluate(stress)

        assert result.failure_index == pytest.approx((von_mises(stress) / allowable) ** 2)
        assert result.factor_of_safety == pytest.approx(allowable / von_mises(stress))

    def test_hydrostatic_compression_never_fails(self):
        """Squeezing equally from every side does not break it, in this model."""
        criterion = Hoffman.from_strength(isotropic_strength())
        result = criterion.evaluate((-500.0, -500.0, -500.0, 0.0, 0.0, 0.0))
        assert result.failure_index == pytest.approx(0.0)
        assert result.factor_of_safety == math.inf


class TestSingleAxisCases:
    """Loading exactly to one quoted strength must give a factor of exactly 1."""

    @pytest.mark.parametrize(
        ("label", "stress"),
        [
            ("tension 1", (50.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            ("tension 2", (0.0, 50.0, 0.0, 0.0, 0.0, 0.0)),
            ("tension 3", (0.0, 0.0, 30.0, 0.0, 0.0, 0.0)),
            ("compression 1", (-60.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            ("compression 2", (0.0, -60.0, 0.0, 0.0, 0.0, 0.0)),
            ("compression 3", (0.0, 0.0, -55.0, 0.0, 0.0, 0.0)),
            ("shear 12", (0.0, 0.0, 0.0, 28.0, 0.0, 0.0)),
            ("shear 23", (0.0, 0.0, 0.0, 0.0, 25.0, 0.0)),
            ("shear 31", (0.0, 0.0, 0.0, 0.0, 0.0, 25.0)),
        ],
    )
    def test_at_the_strength_the_factor_is_one(self, label, stress):
        for criterion in (Hoffman.from_strength(PRINTED), MaximumStress(strength=PRINTED)):
            result = criterion.evaluate(stress)
            assert result.failure_index == pytest.approx(1.0), label
            assert result.factor_of_safety == pytest.approx(1.0), label


class TestFactorOfSafety:
    def test_is_not_the_reciprocal_root_of_the_index(self):
        """Regression guard on the trap this calculation invites.

        The failure index mixes squared and plain stress terms, so it does not
        scale as the square of the load. Taking ``1/sqrt(index)`` as the factor
        of safety is the obvious shortcut and it is wrong -- here by 6 per
        cent, in the unsafe direction, and worse the more unequal tension and
        compression are. Printed parts are exactly where they are most unequal.
        """
        criterion = Hoffman.from_strength(PRINTED)
        stress = (30.0, -20.0, 12.0, 8.0, 0.0, 0.0)
        result = criterion.evaluate(stress)

        shortcut = 1.0 / math.sqrt(result.failure_index)
        assert shortcut != pytest.approx(result.factor_of_safety, rel=0.01)
        assert shortcut < result.factor_of_safety

    def test_scaling_the_load_by_it_reaches_failure_exactly(self):
        """The defining property: at ``factor`` times the load, the index is 1."""
        criterion = Hoffman.from_strength(PRINTED)
        for stress in [
            (30.0, -20.0, 12.0, 8.0, 0.0, 0.0),
            (-45.0, 5.0, -3.0, 0.0, 11.0, 2.0),
            (1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        ]:
            factor = criterion.evaluate(stress).factor_of_safety
            scaled = tuple(value * factor for value in stress)
            assert criterion.evaluate(scaled).failure_index == pytest.approx(1.0)

    def test_no_stress_means_unbounded_margin(self):
        criterion = Hoffman.from_strength(PRINTED)
        result = criterion.evaluate((0.0,) * 6)
        assert result.factor_of_safety == math.inf


class TestAdmissibility:
    """Hoffman must refuse a material it cannot bound, rather than report one.

    Past a through-layer strength of half the in-plane strength, the failure
    surface opens: one stress state costs nothing in the criterion, so the
    computed margin against it is infinite. That is optimistic, silent, and
    exactly the failure mode this project exists to stop.
    """

    @pytest.mark.parametrize("ratio", [1.0, 0.9, 0.7, 0.55, 0.51])
    def test_accepts_a_material_it_can_describe(self, ratio):
        strength = DirectionalStrength(
            tension=(50.0, 50.0, 50.0 * ratio),
            compression=(50.0, 50.0, 50.0 * ratio),
            shear=(25.0, 25.0, 25.0),
        )
        assert Hoffman.from_strength(strength) is not None

    @pytest.mark.parametrize("ratio", [0.5, 0.49, 0.4, 0.3, 0.1])
    def test_refuses_a_material_it_cannot(self, ratio):
        strength = DirectionalStrength(
            tension=(50.0, 50.0, 50.0 * ratio),
            compression=(50.0, 50.0, 50.0 * ratio),
            shear=(25.0, 25.0, 25.0),
        )
        with pytest.raises(InadmissibleMaterial, match="maximum stress"):
            Hoffman.from_strength(strength)

    def test_maximum_stress_accepts_what_hoffman_refuses(self):
        """The fallback has to work for the case that forced it to exist."""
        strength = DirectionalStrength(
            tension=(50.0, 50.0, 15.0),
            compression=(50.0, 50.0, 40.0),
            shear=(25.0, 25.0, 25.0),
        )
        with pytest.raises(InadmissibleMaterial):
            Hoffman.from_strength(strength)

        result = MaximumStress(strength=strength).evaluate((0.0, 0.0, 15.0, 0.0, 0.0, 0.0))
        assert result.factor_of_safety == pytest.approx(1.0)

    def test_the_open_direction_would_have_reported_no_limit(self):
        """Shows what the refusal prevents, using hand-built coefficients.

        These are the coefficients a 40-per-cent through-layer material
        produces. Pulling along the layers while pressing across them costs
        nothing at all, so the margin comes back unbounded.
        """
        open_envelope = Hoffman(
            quadratic=(1.5625e-3, 1.5625e-3, -7.8125e-4),
            linear=(0.0, 0.0, 0.0),
            shear=(1.0 / 625.0,) * 3,
        )
        result = open_envelope.evaluate((100.0, -100.0, 0.0, 0.0, 0.0, 0.0))
        assert result.factor_of_safety == math.inf


class TestAgainstVonMises:
    def test_von_mises_would_overstate_a_printed_part(self):
        """The reason this module exists, as a number.

        A pull straight across the print layers, at 28 MPa. Judged against the
        in-plane strength of 50 MPa -- which is what a single allowable stress
        means -- it looks comfortable. Against the real through-layer strength
        of 30 MPa it is nearly at failure.
        """
        stress = (0.0, 0.0, 28.0, 0.0, 0.0, 0.0)
        naive = 50.0 / von_mises(stress)
        honest = Hoffman.from_strength(PRINTED).evaluate(stress).factor_of_safety

        assert naive == pytest.approx(1.7857, rel=1e-3)
        assert honest == pytest.approx(1.0713, rel=1e-3)
        assert naive > 1.6 * honest


class TestFieldEvaluation:
    def test_vectorised_form_matches_the_scalar_one(self):
        """Pins the numpy copy of the formula to the plain-Python original.

        Two copies of one equation is a real risk. This is what stops them
        drifting apart.
        """
        rng = np.random.default_rng(20260808)
        field = rng.uniform(-60.0, 60.0, size=(200, 6))

        for criterion in (Hoffman.from_strength(PRINTED), MaximumStress(strength=PRINTED)):
            index, factor = evaluate_field(criterion, field)
            for row in range(field.shape[0]):
                expected = criterion.evaluate(tuple(field[row]))
                assert index[row] == pytest.approx(expected.failure_index)
                assert factor[row] == pytest.approx(expected.factor_of_safety)

    def test_rotation_is_identity_when_layers_lie_along_z(self):
        material = OrthotropicMaterial.transversely_isotropic(
            name="print",
            in_plane_modulus_mpa=3500.0,
            through_layer_modulus_mpa=2200.0,
            in_plane_poisson=0.36,
            through_layer_poisson=0.30,
            through_layer_shear_mpa=850.0,
            density_kg_m3=1240.0,
            build_direction=(0.0, 0.0, 1.0),
        )
        from openoptima.results.directional import rotation_to_material_axes

        rotation = rotation_to_material_axes(material)
        stress = np.array([[10.0, 20.0, 30.0, 4.0, 5.0, 6.0]])
        rotated = to_material_axes(stress, rotation)

        # Axis 3 must still be the build direction, so s33 is unchanged.
        assert rotated[0, 2] == pytest.approx(30.0)
        # The rotation is a pure relabelling of the in-plane axes, so the
        # three invariants of the tensor cannot move.
        assert rotated[0, :3].sum() == pytest.approx(stress[0, :3].sum())

    def test_rotation_finds_the_weak_direction_when_the_part_is_built_sideways(self):
        """The check that matters: same stress, different build direction.

        A part pulled along global x, printed with its layers normal to x, is
        being pulled straight across its layers and is in trouble. The same
        part printed with layers normal to z is being pulled along them and is
        fine. Only the rotation can tell the two apart.
        """
        stress = np.array([[28.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
        criterion = Hoffman.from_strength(PRINTED)
        evaluation = StressEvaluation(measure="raw_max")

        weak = OrthotropicMaterial.transversely_isotropic(
            name="built sideways",
            in_plane_modulus_mpa=3500.0,
            through_layer_modulus_mpa=2200.0,
            in_plane_poisson=0.36,
            through_layer_poisson=0.30,
            through_layer_shear_mpa=850.0,
            density_kg_m3=1240.0,
            build_direction=(1.0, 0.0, 0.0),
            strength=PRINTED,
        )
        strong = OrthotropicMaterial.transversely_isotropic(
            name="built flat",
            in_plane_modulus_mpa=3500.0,
            through_layer_modulus_mpa=2200.0,
            in_plane_poisson=0.36,
            through_layer_poisson=0.30,
            through_layer_shear_mpa=850.0,
            density_kg_m3=1240.0,
            build_direction=(0.0, 0.0, 1.0),
            strength=PRINTED,
        )

        across = directional_margin(criterion, weak, stress, evaluation)
        along = directional_margin(criterion, strong, stress, evaluation)

        assert across.factor_of_safety == pytest.approx(1.0713, rel=1e-3)
        assert along.factor_of_safety == pytest.approx(50.0 / 28.0, rel=1e-3)
        assert along.factor_of_safety > 1.6 * across.factor_of_safety


class TestCriterionSelection:
    def test_by_name(self):
        assert isinstance(criterion_for("hoffman", PRINTED), Hoffman)
        assert isinstance(criterion_for("max_stress", PRINTED), MaximumStress)

    def test_unknown_name_is_refused(self):
        with pytest.raises(ValueError, match="unknown failure criterion"):
            criterion_for("tsai_wu", PRINTED)
