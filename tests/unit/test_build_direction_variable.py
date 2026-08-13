"""Letting the optimiser choose which way up a part is printed.

Print direction is not a workshop preference. Measured on
``examples/drone_arm`` at its starting section -- identical shape, identical
loads, identical mesh, only the orientation changing:

    printed along z (flat)      stress 7.53 MPa   factor of safety 3.07
    printed along y (on edge)   stress 7.56 MPa   factor of safety 3.05
    printed along x (upright)   stress 7.54 MPa   factor of safety 1.55

The stress does not move and the strength halves, so this is worth handing to
the search rather than guessing at. What is checked here is that the choice
actually reaches the material, and that the cache can tell two orientations
apart -- because if it cannot, the answer for a strong orientation is served
for a weak one and every number in it looks right.

These need no CAE tool.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from openoptima.domain.model import Material
from openoptima.domain.orthotropic import BUILD_AXES, OrthotropicMaterial
from openoptima.schema.loader import ProjectLoadError, load_project

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"

_FIXED = "build_direction: print_direction"
_VARIABLE_BLOCK = "    - id: print_direction\n"


def _project_text() -> str:
    text = DRONE_ARM.read_text()
    assert _FIXED in text, "the example is expected to hand this choice to the optimiser"
    return text


def _load(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "project.yaml"
        path.write_text(text)
        return load_project(path)


def _design(**overrides):
    design = {"width": 20.0, "height": 18.0, "wall": 3.0, "print_direction": "z"}
    design.update(overrides)
    return design


# -- the choice reaches the material -----------------------------------------


def test_the_example_hands_the_choice_to_the_optimiser() -> None:
    project = load_project(DRONE_ARM)
    assert project.build_direction_variable == "print_direction"
    variable = next(v for v in project.design_space if v.id == "print_direction")
    assert set(variable.choices) == set(BUILD_AXES)


@pytest.mark.parametrize("axis", sorted(BUILD_AXES))
def test_each_choice_becomes_the_matching_axis(axis: str) -> None:
    project = load_project(DRONE_ARM)
    material = project.material_for(_design(print_direction=axis))
    assert isinstance(material, OrthotropicMaterial)
    assert material.normalised_build_direction == BUILD_AXES[axis]


def test_the_analysis_model_carries_the_chosen_direction() -> None:
    """The solver sees it, not just the project object.

    ``analysis_model`` is what the deck writer is handed, so a direction that
    resolved correctly on the project and then failed to reach here would
    write an ``*ORIENTATION`` for the wrong axis and change nothing else.
    """
    project = load_project(DRONE_ARM)
    model = project.analysis_model(_design(print_direction="x"))
    assert isinstance(model.material, OrthotropicMaterial)
    assert model.material.normalised_build_direction == (1.0, 0.0, 0.0)


def test_no_design_falls_back_to_the_materials_own_direction() -> None:
    """``analysis_model()`` with no design must still produce a real material.

    Several callers ask for the model without a design -- reporting, checks.
    They must get something valid rather than a crash or a half-built object.
    """
    project = load_project(DRONE_ARM)
    material = project.analysis_model().material
    assert isinstance(material, OrthotropicMaterial)
    assert material.normalised_build_direction in set(BUILD_AXES.values())


def test_a_fixed_direction_still_works_and_ignores_any_design() -> None:
    """Stating a vector must keep the decision with the engineer."""
    text = _project_text().replace(_FIXED, "build_direction: [0.0, 1.0, 0.0]")
    project = _load(text)
    assert project.build_direction_variable is None
    assert project.material_for(_design(print_direction="x")).normalised_build_direction == (
        0.0,
        1.0,
        0.0,
    )


# -- the cache ---------------------------------------------------------------


def test_two_orientations_are_different_designs() -> None:
    """The design hash separates them, so one cannot be served for the other.

    The setup digest deliberately does *not* pin a direction when the
    optimiser chooses it -- the chosen axis rides on the design vector. This
    checks the two halves add up.
    """
    project = load_project(DRONE_ARM)
    space = project.design_space
    flat = space.decode(_design(print_direction="z")).canonical_text()
    upright = space.decode(_design(print_direction="x")).canonical_text()
    assert flat != upright


def test_the_setup_digest_does_not_pin_one_direction() -> None:
    """Otherwise every orientation of one section hashes as a single result.

    Compared against the same project with the direction fixed: those are
    different studies and must not share a digest.
    """
    variable = load_project(DRONE_ARM).setup_digest()
    fixed = _load(_project_text().replace(_FIXED, "build_direction: [0.0, 0.0, 1.0]"))
    assert variable != fixed.setup_digest()


# -- refusals ----------------------------------------------------------------


def test_naming_a_variable_that_does_not_exist_is_refused() -> None:
    text = _project_text().replace(_FIXED, "build_direction: no_such_variable")
    with pytest.raises(ProjectLoadError, match="no_such_variable"):
        _load(text)


def test_a_choice_that_is_not_an_axis_is_refused() -> None:
    """A typo must fail loudly rather than quietly printing it flat."""
    text = _project_text().replace("choices: [x, y, z]", "choices: [x, y, sideways]")
    with pytest.raises(ProjectLoadError, match="sideways"):
        _load(text)


def test_choosing_a_direction_for_an_ordinary_material_is_refused() -> None:
    """Which way up a billet of aluminium is machined does not change it.

    A project file cannot express this -- ``build_direction`` sits inside the
    ``printed:`` block, so removing the block removes the choice with it. The
    guard is for a ``Project`` built in code, and it is kept because accepting
    the combination would imply the software was accounting for something it
    was not.
    """
    project = load_project(DRONE_ARM)
    ordinary = Material.from_engineering_units(
        name="Aluminium 6082-T6",
        elastic_modulus_mpa=70000.0,
        poisson_ratio=0.33,
        density_kg_m3=2700.0,
        allowable_stress_mpa=160.0,
    )
    with pytest.raises(ValueError, match="not a printed one"):
        replace(project, material=ordinary)
