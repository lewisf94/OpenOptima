"""A project file describing a 3D-printed material.

The arithmetic of a directional material is tested in ``test_orthotropic.py``
and ``test_failure_criteria.py``. What is tested here is the step that was
missing until this landed: getting one *out of a project file at all*. The
whole directional stack -- the material, both criteria, the deck writer, the
verification benchmark -- was built and verified, and then no ``project.yaml``
could reach it, because ``MaterialSchema`` accepted only an ordinary material.

Two of these tests guard against a silent wrong answer rather than an error:

* :func:`test_the_shear_strengths_land_on_the_right_planes` -- the schema asks
  for strengths in print terms and the domain stores them by axis pair, in a
  different order. Swapping them puts the weak interlayer shear strength on
  the strong plane. Nothing about a part in pure tension would reveal it.
* the digest tests -- a printed material's fields are not the fields an
  ordinary one has, so the cache hash had to be rewritten to cover them. If it
  misses one, a result computed for a part printed one way is served for a
  part printed the other, which the measured numbers in
  ``docs/plain-english-guide.md`` show is a factor of two in strength.

These need no CAE tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openoptima.domain.model import Material
from openoptima.domain.orthotropic import OrthotropicMaterial
from openoptima.domain.project import _material_digest
from openoptima.schema.loader import ProjectLoadError, load_project
from openoptima.schema.project_schema import MaterialSchema

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"

#: A defensible printed PLA: measured strengths with a design factor taken off.
#: Through-layer tension is half the in-plane value, which is typical, and
#: through-layer *compression* is barely reduced, because layers press together
#: perfectly well and only pull apart badly.
PRINTED = {
    "build_direction": [0.0, 0.0, 1.0],
    "along_layers_modulus_mpa": 3500.0,
    "through_layers_modulus_mpa": 2600.0,
    "in_plane_poisson": 0.36,
    "through_layers_poisson": 0.33,
    "through_layers_shear_modulus_mpa": 1100.0,
    "strength": {
        "along_layers_tension_mpa": 22.0,
        "through_layers_tension_mpa": 11.0,
        "along_layers_compression_mpa": 30.0,
        "through_layers_compression_mpa": 28.0,
        "in_plane_shear_mpa": 16.0,
        "through_layers_shear_mpa": 9.0,
        "basis": "measured, divided by a design factor of 2.5",
    },
}

ORDINARY = {
    "name": "Aluminium 6082-T6",
    "elastic_modulus_mpa": 70000.0,
    "poisson_ratio": 0.33,
    "density_kg_m3": 2700.0,
    "allowable_stress_mpa": 160.0,
}


def _printed(**overrides) -> MaterialSchema:
    printed = {**PRINTED, **overrides.pop("printed", {})}
    return MaterialSchema(
        name="PLA, printed solid", density_kg_m3=1240.0, printed=printed, **overrides
    )


# -- an ordinary material still behaves exactly as it did --------------------


def test_an_ordinary_material_is_unchanged() -> None:
    material = MaterialSchema(**ORDINARY).to_domain()
    assert isinstance(material, Material)
    assert material.elastic_modulus == 70000.0
    assert material.allowable_stress == 160.0


@pytest.mark.parametrize(
    "missing", ["elastic_modulus_mpa", "poisson_ratio", "allowable_stress_mpa"]
)
def test_an_ordinary_material_still_needs_all_three(missing: str) -> None:
    fields = {key: value for key, value in ORDINARY.items() if key != missing}
    with pytest.raises(ValidationError, match=missing):
        MaterialSchema(**fields)


def test_a_material_with_nothing_at_all_is_refused() -> None:
    with pytest.raises(ValidationError, match="printed"):
        MaterialSchema(name="mystery", density_kg_m3=1240.0)


def test_a_failure_criterion_on_an_ordinary_material_is_refused() -> None:
    """Not silently ignored: it means the user expected directional strengths."""
    with pytest.raises(ValidationError, match="applies only to a printed material"):
        MaterialSchema(**ORDINARY, failure_criterion="max_stress")


# -- a printed material ------------------------------------------------------


def test_a_printed_material_becomes_an_orthotropic_one() -> None:
    material = _printed().to_domain()
    assert isinstance(material, OrthotropicMaterial)
    # Axes 1 and 2 lie in the layer plane; axis 3 is through the layers.
    assert material.modulus == (3500.0, 3500.0, 2600.0)
    assert material.normalised_build_direction == (0.0, 0.0, 1.0)
    assert not material.is_effectively_isotropic()


def test_the_tension_strengths_land_on_the_right_axes() -> None:
    strength = _printed().to_domain().strength
    assert strength is not None
    assert strength.tension == (22.0, 22.0, 11.0)
    assert strength.compression == (30.0, 30.0, 28.0)


def test_the_shear_strengths_land_on_the_right_planes() -> None:
    """The schema asks in print terms; the domain stores by axis pair.

    ``DirectionalStrength.shear`` is ordered on planes 23, 13, 12. Axis 3 is
    through the layers, so planes 23 and 13 are the two that slide one layer
    across the next, and plane 12 lies wholly within a layer. Getting this
    backwards would give the weak interlayer value to the strong plane and
    report a part as sound that is about to slide apart -- and no test of a
    part in pure tension would notice, because tension does not touch these
    terms at all.
    """
    strength = _printed().to_domain().strength
    assert strength is not None
    assert strength.shear == (9.0, 9.0, 16.0), (
        "expected the two through-layer planes (23, 13) to carry the interlayer "
        "shear strength of 9 MPa and the in-plane plane (12) to carry 16 MPa"
    )


def test_a_printed_material_may_omit_its_strengths() -> None:
    """Stress and deflection are still computed; the factor of safety is not."""
    material = _printed(printed={"strength": None}).to_domain()
    assert isinstance(material, OrthotropicMaterial)
    assert material.strength is None


def test_giving_both_kinds_of_material_is_refused() -> None:
    with pytest.raises(ValidationError, match="both a `printed:` block"):
        MaterialSchema(
            name="confused",
            density_kg_m3=1240.0,
            printed=PRINTED,
            elastic_modulus_mpa=3500.0,
        )


def test_a_printed_material_may_not_carry_an_allowable_stress() -> None:
    """It has no single one, so accepting the field would invite trusting it."""
    with pytest.raises(ValidationError, match="both a `printed:` block"):
        MaterialSchema(
            name="confused",
            density_kg_m3=1240.0,
            printed=PRINTED,
            allowable_stress_mpa=20.0,
        )


# -- Hoffman's limit, caught when the file is read ---------------------------


def test_hoffman_is_refused_at_load_time_not_after_a_solve() -> None:
    """The criterion cannot bound this material, and waiting costs a whole run.

    Hoffman stops predicting failure at all for one combination of stresses
    once the weakest direction falls far enough below the strongest. The
    margin it reports there is unbounded, which is wrong in the unsafe
    direction. Catching it here turns a wasted optimisation into an error the
    moment the file is read.
    """
    weak = {
        **PRINTED["strength"],  # type: ignore[dict-item]
        "through_layers_tension_mpa": 4.0,
        "through_layers_compression_mpa": 6.0,
    }
    with pytest.raises(ValidationError, match="max_stress"):
        _printed(printed={"strength": weak})


def test_the_same_material_is_accepted_under_max_stress() -> None:
    """The criterion Hoffman told the user to switch to must actually work."""
    weak = {
        **PRINTED["strength"],  # type: ignore[dict-item]
        "through_layers_tension_mpa": 4.0,
        "through_layers_compression_mpa": 6.0,
    }
    material = _printed(printed={"strength": weak}, failure_criterion="max_stress").to_domain()
    assert isinstance(material, OrthotropicMaterial)


def test_hoffmans_limit_acts_on_tension_times_compression() -> None:
    """A print is weak through its layers in tension only, and that is allowed.

    Measured: holding in-plane strength at 22/30 MPa and dropping only the
    through-layer tension, Hoffman accepts down to a tension ratio of 0.27 and
    refuses at 0.23. The limit is on the *product* of tension and compression
    -- it must stay above a quarter of the in-plane product -- not on tension
    alone. That matters here because it is exactly the shape of a real print:
    layers press together perfectly well and only pull apart badly, so the
    compression term holds the product up. A criterion refused on the tension
    ratio alone would send most real prints to ``max_stress`` needlessly.
    """
    ok = {**PRINTED["strength"], "through_layers_tension_mpa": 6.0}  # type: ignore[dict-item]
    _printed(printed={"strength": ok})  # 6/22 = 0.27 of the in-plane tension

    refused = {**PRINTED["strength"], "through_layers_tension_mpa": 5.0}  # type: ignore[dict-item]
    with pytest.raises(ValidationError, match="max_stress"):
        _printed(printed={"strength": refused})


# -- the cache hash ----------------------------------------------------------
#
# Anything that can change a number must change the digest, or a stale result
# is served as a fresh one. A printed material's fields are not an ordinary
# one's, so every one of them needed adding by hand.


def _digest(material: MaterialSchema) -> str:
    return json.dumps(_material_digest(material.to_domain()), sort_keys=True)


def test_the_digest_changes_when_the_print_direction_changes() -> None:
    """The most dangerous one. Same shape, same loads, same mass, same mesh.

    Only the direction the layers run changes, and on the drone arm example
    that moves the factor of safety from 3.07 to 1.55 while the stress stays
    at 7.5 MPa. If the digest missed it, the passing result would be served
    for the failing design.
    """
    flat = _digest(_printed())
    upright = _digest(_printed(printed={"build_direction": [1.0, 0.0, 0.0]}))
    assert flat != upright


def test_the_digest_changes_when_a_strength_changes() -> None:
    stronger = {**PRINTED["strength"], "through_layers_tension_mpa": 12.0}  # type: ignore[dict-item]
    assert _digest(_printed()) != _digest(_printed(printed={"strength": stronger}))


def test_the_digest_changes_when_a_stiffness_changes() -> None:
    stiffer = _digest(_printed(printed={"through_layers_modulus_mpa": 2700.0}))
    assert _digest(_printed()) != stiffer


def test_the_digest_separates_a_printed_material_from_an_ordinary_one() -> None:
    """Even when their numbers coincide.

    An ordinary material and a printed one whose stiffness happened to match
    it are different physics -- the printed one has a weak direction and no
    single allowable stress. The ``kind`` field keeps them apart.
    """
    printed = _printed(
        printed={
            "along_layers_modulus_mpa": 70000.0,
            "through_layers_modulus_mpa": 70000.0,
            "in_plane_poisson": 0.33,
            "through_layers_poisson": 0.33,
            "through_layers_shear_modulus_mpa": 70000.0 / (2.0 * 1.33),
        }
    )
    ordinary = MaterialSchema(**{**ORDINARY, "density_kg_m3": 1240.0})
    assert _digest(printed) != _digest(ordinary)


def test_the_digest_survives_a_material_with_no_strengths() -> None:
    """It hashes rather than raising: strengths are optional on a print."""
    assert _digest(_printed(printed={"strength": None}))


# -- buckling ----------------------------------------------------------------


def test_buckling_is_refused_for_a_printed_material(tmp_path) -> None:
    """Refused, not warned about.

    The check that decides whether a buckling factor can be trusted needs one
    stiffness for the whole member, and a print has two. An unchecked buckling
    number is wrong in the optimistic direction, and the optimiser acts on the
    number whatever is attached to it.

    Driven through the real example file, because this rule has to fire when a
    user edits their project, not only when the domain object is built by hand.
    """
    text = DRONE_ARM.read_text()
    assert "buckling:\n  enabled: false" in text
    project = tmp_path / "project.yaml"
    project.write_text(text.replace("buckling:\n  enabled: false", "buckling:\n  enabled: true"))

    with pytest.raises(ProjectLoadError, match="cannot check a buckling result"):
        load_project(project)


def test_the_drone_arm_example_declares_a_printed_material() -> None:
    """The example is the documentation for this feature; keep them together."""
    project = load_project(DRONE_ARM)
    assert isinstance(project.material, OrthotropicMaterial)
    assert project.failure_criterion == "hoffman"
    assert project.material.strength is not None
    # Printed flat on the bed: bending runs along the layers, the strong way.
    assert project.material.normalised_build_direction == (0.0, 0.0, 1.0)
    # Buckling must stay off: the check that decides whether a buckling factor
    # can be trusted needs one stiffness, and a print has two.
    assert not project.buckling.enabled
