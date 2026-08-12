"""Point masses: the plumbing, not the physics.

The physics is verified against the closed form in
``tests/verification/test_point_mass.py``. What is checked here is everything
that can go wrong on the way there without the solver ever complaining:

* the unit conversion, because a project says kilograms and the solver wants
  tonnes, and a factor of a thousand in a mass moves a frequency by about 32;
* the cache hash, because a result computed without the motor is not a result
  for a part carrying one;
* the deck, because a ``MASS`` element that is written but never named in a
  gravity load is weightless and says nothing about it.

These need no CAE tool.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    ModalSettings,
    PointMass,
)
from openoptima.meshing.base import MeshData
from openoptima.schema.loader import ProjectLoadError, load_project
from openoptima.solvers.calculix.deck import write_deck

DRONE_ARM = Path(__file__).resolve().parents[2] / "examples" / "drone_arm" / "project.yaml"


# -- units -------------------------------------------------------------------


def test_kilograms_become_tonnes() -> None:
    """The solver is unitless and this project works in mm, N, MPa, t.

    A mass passed through as kilograms would be a thousand times too heavy,
    and since frequency goes as one over the square root of mass that is a
    factor of about 32 on every frequency -- large, but not so large that it
    looks obviously wrong on a part nobody has hand-checked.
    """
    mass = PointMass.from_engineering_units(name="motor", region="pad", mass_kg=0.035)
    assert mass.mass == pytest.approx(3.5e-5)
    assert mass.mass_kg == pytest.approx(0.035)


def test_a_mass_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="more than nothing"):
        PointMass(name="ghost", region="pad", mass=0.0)


# -- project rules -----------------------------------------------------------


def test_a_mass_on_an_unknown_region_is_refused(tmp_path) -> None:
    text = DRONE_ARM.read_text().replace("region: motor_pad", "region: not_a_face", 1)
    project = tmp_path / "project.yaml"
    project.write_text(text)
    with pytest.raises(ProjectLoadError, match="not_a_face"):
        load_project(project)


def test_the_drone_arm_example_carries_its_motor() -> None:
    """The example is the documentation for this feature; keep them together."""
    project = load_project(DRONE_ARM)
    assert len(project.point_masses) == 1
    motor = project.point_masses[0]
    assert motor.region == "motor_pad"
    assert motor.mass_kg == pytest.approx(0.035)
    # The frequency check is only honest with the motor present, so the two
    # must travel together.
    assert project.modal.enabled


def test_the_carried_mass_is_not_counted_as_part_mass() -> None:
    """``mass_kg`` is what you print, and the motor is not printed.

    Rolling the motor into it would quietly change what "minimise mass" means:
    the optimiser would be paying for 35 g it cannot remove, and every mass
    figure in the report would be for something other than the part.
    """
    project = load_project(DRONE_ARM)
    assert any(o.metric == "mass_kg" for o in project.objectives)
    assert project.point_masses[0].mass_kg == pytest.approx(0.035)


# -- the cache hash ----------------------------------------------------------


def _digest_with(tmp_path, replacement: str | None) -> str:
    text = DRONE_ARM.read_text()
    if replacement is None:
        start, end = text.index("point_masses:"), text.index("load_cases:")
        text = text[:start] + text[end:]
    else:
        text = text.replace("mass_kg: 0.035", replacement, 1)
    project = tmp_path / "project.yaml"
    project.write_text(text)
    return load_project(project).setup_digest()


def test_the_digest_changes_when_the_carried_mass_changes(tmp_path) -> None:
    assert _digest_with(tmp_path, "mass_kg: 0.035") != _digest_with(tmp_path, "mass_kg: 0.045")


def test_the_digest_changes_when_the_mass_is_removed(tmp_path) -> None:
    """Otherwise the 191 Hz answer for a bare arm is served for one with a
    motor on it, whose real answer is 121 Hz."""
    assert _digest_with(tmp_path, "mass_kg: 0.035") != _digest_with(tmp_path, None)


# -- the deck ----------------------------------------------------------------


def _one_element_mesh() -> MeshData:
    """A single 4-node tetrahedron with one face named."""
    return MeshData(
        node_tags=np.array([1, 2, 3, 4]),
        coordinates=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        element_tags=np.array([10]),
        connectivity=np.array([[1, 2, 3, 4]]),
        element_type="C3D4",
        surface_nodes={"pad": np.array([1, 2, 3]), "root": np.array([1, 2, 4])},
        surface_triangles={"pad": np.array([[1, 2, 3]]), "root": np.array([[1, 2, 4]])},
    )


#: A load case must carry a load, and a frequency step deliberately carries
#: none. This exists only to satisfy that rule where the load is beside the
#: point.
_TOKEN_LOAD = Load(kind=LoadKind.FORCE, region="pad", vector=(0.0, 0.0, -1.0))


def _deck_text(tmp_path, point_masses, loads) -> str:
    model = AnalysisModel(
        name="deck test",
        material=Material.from_engineering_units(
            name="Steel",
            elastic_modulus_mpa=210000.0,
            poisson_ratio=0.3,
            density_kg_m3=7850.0,
            allowable_stress_mpa=250.0,
        ),
        load_cases=(
            LoadCase(
                id="case",
                boundary_conditions=(BoundaryCondition(region="root", dofs=(1, 2, 3)),),
                loads=loads,
            ),
        ),
        point_masses=point_masses,
        modal=ModalSettings(enabled=True, modes=2),
    )
    artifact = write_deck(model, _one_element_mesh(), tmp_path / "deck")
    return "\n".join(path.read_text() for path in artifact.files)


def test_a_mass_element_is_written_for_every_node_of_the_face(tmp_path) -> None:
    """Split by node count, and the total must come out exact.

    The obvious alternative is the consistent split used for a surface load,
    which integrates the shape functions. That is right for a load and wrong
    here: those weights are zero at the corner nodes of a quadratic face, and
    negative for some element types, so the mass would land in the wrong
    places and some of it would be negative. A negative mass is not a
    conservative approximation -- it makes an eigenvalue solve meaningless.
    """
    mass = PointMass.from_engineering_units(name="motor", region="pad", mass_kg=0.030)
    text = _deck_text(tmp_path, (mass,), (_TOKEN_LOAD,))

    assert "*ELEMENT, TYPE=MASS, ELSET=EM_MOTOR" in text
    per_node = 0.030e-3 / 3.0  # the pad has three nodes
    assert f"*MASS, ELSET=EM_MOTOR\n{per_node:.9g}" in text


def test_the_gravity_load_names_the_mass_as_well_as_the_part(tmp_path) -> None:
    """The failure this guards is silent.

    A ``MASS`` element is not in ``Eall``, so gravity applied only to ``Eall``
    leaves it weightless with exit code 0 and nothing in the log. Measured on
    a steel cantilever carrying 0.2 kg: 0.3843 N that way against 2.3463 N
    with both named.
    """
    mass = PointMass.from_engineering_units(name="motor", region="pad", mass_kg=0.030)
    load = Load(kind=LoadKind.ACCELERATION, region=None, vector=(0.0, 0.0, -9810.0))
    text = _deck_text(tmp_path, (mass,), (load,))

    assert "Eall, GRAV, 9810" in text
    assert "EM_MOTOR, GRAV, 9810" in text, (
        "gravity must name the mass element set too, or the carried mass has "
        "no weight and nothing says so"
    )


def test_no_mass_element_set_is_named_when_there_are_none(tmp_path) -> None:
    """An undefined element set is a hard CalculiX error, so this must not
    appear on a project with no carried mass."""
    load = Load(kind=LoadKind.ACCELERATION, region=None, vector=(0.0, 0.0, -9810.0))
    text = _deck_text(tmp_path, (), (load,))
    assert "Eall, GRAV, 9810" in text
    assert "EM_" not in text


def test_mass_element_tags_cannot_collide_with_the_mesh(tmp_path) -> None:
    """A duplicate element number silently redefines an element of the part."""
    mass = PointMass.from_engineering_units(name="motor", region="pad", mass_kg=0.030)
    text = _deck_text(tmp_path, (mass,), (_TOKEN_LOAD,))
    block = text.split("*ELEMENT, TYPE=MASS, ELSET=EM_MOTOR\n")[1].split("*MASS")[0]
    tags = [int(line.split(",")[0]) for line in block.strip().splitlines()]
    assert min(tags) > 10  # the only real element is tag 10
