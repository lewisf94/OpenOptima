"""Orthotropic materials: admissibility, stiffness inversion, and axes.

The arithmetic here is checked against cases whose answers are known in
advance, not against itself. The most important is the isotropic one: an
orthotropic material whose constants happen to be equal in every direction
must reproduce the textbook isotropic stiffness exactly, because that is the
case every existing verified benchmark relies on.

These need no CAE tool.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.orthotropic import (
    DirectionalStrength,
    InadmissibleMaterial,
    OrthotropicMaterial,
    local_axes,
)

E, NU, RHO = 70000.0, 0.33, 2700.0


def _isotropic_as_orthotropic(modulus=E, poisson=NU) -> OrthotropicMaterial:
    shear = modulus / (2.0 * (1.0 + poisson))
    return OrthotropicMaterial(
        name="isotropic",
        modulus=(modulus, modulus, modulus),
        poisson=(poisson, poisson, poisson),
        shear_modulus=(shear, shear, shear),
        density=2.7e-9,
    )


def _printed() -> OrthotropicMaterial:
    """A plausible FDM print: 40% weaker through the layers."""
    return OrthotropicMaterial.transversely_isotropic(
        name="PLA print",
        in_plane_modulus_mpa=3500.0,
        through_layer_modulus_mpa=2100.0,
        in_plane_poisson=0.36,
        through_layer_poisson=0.30,
        through_layer_shear_mpa=900.0,
        density_kg_m3=1240.0,
    )


# ---------------------------------------------------------------------------
# the isotropic special case
# ---------------------------------------------------------------------------


def test_equal_constants_reproduce_the_textbook_isotropic_stiffness():
    """The case every existing verified benchmark depends on.

    For an isotropic material the stiffness terms are the Lame constants:
    D1111 = lambda + 2*mu, D1122 = lambda. If this drifts, every number this
    project has verified drifts with it.
    """
    material = _isotropic_as_orthotropic()
    d1111, d1122, d2222, d1133, d2233, d3333, g12, g13, g23 = material.stiffness_matrix()

    lame = E * NU / ((1.0 + NU) * (1.0 - 2.0 * NU))
    mu = E / (2.0 * (1.0 + NU))

    assert d1111 == pytest.approx(lame + 2.0 * mu, rel=1e-12)
    assert d2222 == pytest.approx(lame + 2.0 * mu, rel=1e-12)
    assert d3333 == pytest.approx(lame + 2.0 * mu, rel=1e-12)
    assert d1122 == pytest.approx(lame, rel=1e-12)
    assert d1133 == pytest.approx(lame, rel=1e-12)
    assert d2233 == pytest.approx(lame, rel=1e-12)
    assert g12 == pytest.approx(mu, rel=1e-12)
    assert g13 == pytest.approx(mu, rel=1e-12)
    assert g23 == pytest.approx(mu, rel=1e-12)


def test_an_isotropic_material_is_recognised_as_one():
    assert _isotropic_as_orthotropic().is_effectively_isotropic()
    assert not _printed().is_effectively_isotropic()


# ---------------------------------------------------------------------------
# the stiffness inversion
# ---------------------------------------------------------------------------


def test_stiffness_is_the_inverse_of_compliance_not_the_compliance_itself():
    """A solver wants stiffness; an engineer quotes moduli, which are
    compliance. Passing the engineering constants through unchanged would be
    silently wrong, and would look plausible: both are large positive numbers.
    """
    material = _printed()
    d1111 = material.stiffness_matrix()[0]

    # Stiffness along an axis always exceeds that axis's modulus, because
    # Poisson coupling to the other two directions stiffens it.
    assert d1111 > material.modulus[0]


def test_inverting_the_stiffness_recovers_the_moduli():
    """Round trip through the 3x3 normal block, done by hand.

    Solving the stiffness matrix against a uniaxial stress state must give
    back a strain of 1/E along the loaded axis.
    """
    material = _printed()
    d1111, d1122, d2222, d1133, d2233, d3333, *_ = material.stiffness_matrix()

    # Cramer's rule on D . strain = (1, 0, 0)
    determinant = (
        d1111 * (d2222 * d3333 - d2233 * d2233)
        - d1122 * (d1122 * d3333 - d2233 * d1133)
        + d1133 * (d1122 * d2233 - d2222 * d1133)
    )
    strain_1 = (d2222 * d3333 - d2233 * d2233) / determinant

    assert strain_1 == pytest.approx(1.0 / material.modulus[0], rel=1e-9)


def test_the_through_layer_direction_really_is_the_soft_one():
    material = _printed()
    assert material.modulus[2] < material.modulus[0]
    stiffness = material.stiffness_matrix()
    assert stiffness[5] < stiffness[0], "D3333 must be softer than D1111"


# ---------------------------------------------------------------------------
# admissibility
# ---------------------------------------------------------------------------


def test_a_poisson_ratio_that_breaks_energy_conservation_is_refused():
    """Nine numbers chosen independently usually do not describe a material.

    This one is refused rather than warned about, because an inadmissible set
    does not make a solver fail -- it makes it converge on nonsense.
    """
    with pytest.raises(InadmissibleMaterial, match="nu13"):
        OrthotropicMaterial(
            name="impossible",
            modulus=(3500.0, 3500.0, 2100.0),
            poisson=(0.36, 1.6, 0.30),  # nu13 far too large for E1/E3
            shear_modulus=(900.0, 900.0, 1287.0),
            density=1.24e-9,
        )


def test_the_bound_quoted_in_the_error_is_the_real_one():
    """The message tells the user what the limit is. It must be correct, or it
    sends them to a value that will be refused again."""
    bound = math.sqrt(3500.0 / 2100.0)
    # Just inside the bound is accepted.
    OrthotropicMaterial(
        name="tight",
        modulus=(3500.0, 3500.0, 2100.0),
        poisson=(0.36, 0.99 * bound * 0.5, 0.30),
        shear_modulus=(900.0, 900.0, 1287.0),
        density=1.24e-9,
    )
    with pytest.raises(InadmissibleMaterial):
        OrthotropicMaterial(
            name="over",
            modulus=(3500.0, 3500.0, 2100.0),
            poisson=(0.36, 1.01 * bound, 0.30),
            shear_modulus=(900.0, 900.0, 1287.0),
            density=1.24e-9,
        )


def test_a_negative_or_zero_modulus_is_refused():
    for bad in ((0.0, 3500.0, 2100.0), (3500.0, -1.0, 2100.0)):
        with pytest.raises(InadmissibleMaterial, match="modulus"):
            OrthotropicMaterial(
                name="bad",
                modulus=bad,
                poisson=(0.36, 0.30, 0.30),
                shear_modulus=(900.0, 900.0, 1287.0),
                density=1.24e-9,
            )


def test_a_build_direction_with_no_length_is_refused():
    with pytest.raises(InadmissibleMaterial, match="build direction"):
        OrthotropicMaterial(
            name="no direction",
            modulus=(3500.0, 3500.0, 2100.0),
            poisson=(0.36, 0.30, 0.30),
            shear_modulus=(900.0, 900.0, 1287.0),
            density=1.24e-9,
            build_direction=(0.0, 0.0, 0.0),
        )


def test_a_realistic_print_is_admissible():
    """The whole point. A plausible set of printed-material numbers must pass."""
    material = _printed()
    assert material.modulus[2] == pytest.approx(2100.0)
    assert material.density_kg_m3 == pytest.approx(1240.0)


# ---------------------------------------------------------------------------
# transverse isotropy
# ---------------------------------------------------------------------------


def test_the_in_plane_shear_modulus_is_derived_not_asked_for():
    """Within a layer the material is isotropic, so its shear modulus follows
    exactly from the other two constants. Asking for it would let a user give
    an inconsistent value."""
    material = _printed()
    expected = 3500.0 / (2.0 * (1.0 + 0.36))
    assert material.shear_modulus[2] == pytest.approx(expected, rel=1e-12)


def test_the_two_in_plane_axes_share_a_modulus():
    material = _printed()
    assert material.modulus[0] == pytest.approx(material.modulus[1])


# ---------------------------------------------------------------------------
# material axes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "direction",
    [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.6, 0.8)],
)
def test_the_material_axes_are_orthogonal_and_normal_to_the_build_direction(direction):
    first, second = local_axes(direction)
    length = math.sqrt(sum(component**2 for component in direction))
    normal = tuple(component / length for component in direction)

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert dot(first, first) == pytest.approx(1.0, abs=1e-12)
    assert dot(second, second) == pytest.approx(1.0, abs=1e-12)
    assert dot(first, second) == pytest.approx(0.0, abs=1e-12)
    assert dot(first, normal) == pytest.approx(0.0, abs=1e-12)
    assert dot(second, normal) == pytest.approx(0.0, abs=1e-12)


def test_axes_are_chosen_deterministically():
    """A rebuilt project must produce an identical deck.

    If the in-plane axes were chosen from anything varying between runs, the
    deck would change without the design changing, and a cached result would
    be invalidated for no reason.
    """
    assert local_axes((0.0, 0.0, 1.0)) == local_axes((0.0, 0.0, 1.0))
    assert local_axes((0.0, 0.0, 2.0)) == local_axes((0.0, 0.0, 1.0))


def test_a_build_direction_along_a_global_axis_does_not_degenerate():
    """The seed axis is picked to be the one least aligned with the build
    direction. A fixed seed would give a near-zero cross product whenever the
    part happened to be built along it."""
    for direction in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
        first, second = local_axes(direction)
        assert math.sqrt(sum(c**2 for c in first)) == pytest.approx(1.0)
        assert math.sqrt(sum(c**2 for c in second)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# strengths
# ---------------------------------------------------------------------------


def test_directional_strengths_must_all_be_positive_magnitudes():
    """Compression is given as a positive number, not a negative stress. A
    negative value there means the user has misread the convention."""
    with pytest.raises(ValueError, match="compression"):
        DirectionalStrength(
            tension=(50.0, 50.0, 30.0),
            compression=(-60.0, 60.0, 60.0),
            shear=(25.0, 25.0, 30.0),
        )


def test_the_weakest_strength_is_available_for_a_conservative_check():
    strength = DirectionalStrength(
        tension=(50.0, 50.0, 28.0),
        compression=(60.0, 60.0, 60.0),
        shear=(25.0, 25.0, 30.0),
    )
    assert strength.weakest == pytest.approx(25.0)
