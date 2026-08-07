"""The material block in the deck, isotropic and orthotropic.

The first test here is the one that protects everything else in the project:
an isotropic material must still write exactly the deck it always wrote. Every
verified benchmark rests on those decks, and adding orthotropic support must
not move any of them.

These need no CAE tool.
"""

from __future__ import annotations

import numpy as np

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
)
from openoptima.domain.orthotropic import OrthotropicMaterial
from openoptima.meshing.base import MeshData
from openoptima.solvers.calculix.deck import write_deck

E, NU = 70000.0, 0.33


def _mesh() -> MeshData:
    return MeshData(
        node_tags=np.array([1, 2, 3, 4]),
        coordinates=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        element_tags=np.array([1]),
        connectivity=np.array([[1, 2, 3, 4]]),
        element_type="C3D4",
        surface_nodes={"base": np.array([1, 2, 3]), "tip": np.array([4])},
        surface_triangles={"base": np.array([[1, 2, 3]]), "tip": np.array([[4, 2, 3]])},
    )


def _model(material) -> AnalysisModel:
    return AnalysisModel(
        name="deck test",
        material=material,
        load_cases=(
            LoadCase(
                id="pull",
                boundary_conditions=(BoundaryCondition(region="base"),),
                loads=(Load(kind=LoadKind.FORCE, region="tip", vector=(1.0, 0.0, 0.0)),),
            ),
        ),
    )


def _material_text(material, tmp_path) -> str:
    write_deck(_model(material), _mesh(), tmp_path)
    return (tmp_path / "material.inp").read_text()


def _isotropic() -> Material:
    return Material.from_engineering_units(
        name="Aluminium",
        elastic_modulus_mpa=E,
        poisson_ratio=NU,
        density_kg_m3=2700.0,
        allowable_stress_mpa=160.0,
    )


def _print_material(build_direction=(0.0, 0.0, 1.0)) -> OrthotropicMaterial:
    return OrthotropicMaterial.transversely_isotropic(
        name="PLA print",
        in_plane_modulus_mpa=3500.0,
        through_layer_modulus_mpa=2100.0,
        in_plane_poisson=0.36,
        through_layer_poisson=0.30,
        through_layer_shear_mpa=900.0,
        density_kg_m3=1240.0,
        build_direction=build_direction,
    )


# ---------------------------------------------------------------------------
# the isotropic deck must not move
# ---------------------------------------------------------------------------


def test_an_isotropic_material_writes_the_deck_it_always_did(tmp_path):
    """Every verified benchmark depends on this exact block.

    Two numbers on a plain *ELASTIC card, no orientation, no stiffness matrix.
    If orthotropic support changed this, every measured value in
    verification-plan.md would need re-measuring.
    """
    text = _material_text(_isotropic(), tmp_path)

    assert "*ELASTIC\n70000, 0.33\n" in text
    assert "TYPE=ORTHO" not in text
    assert "*ORIENTATION" not in text
    assert "*SOLID SECTION, ELSET=Eall, MATERIAL=Aluminium\n" in text
    assert "ORIENTATION=" not in text


def test_the_isotropic_block_is_stable_across_writes(tmp_path):
    """A rebuilt project must produce an identical deck, or a cached result is
    invalidated for no reason."""
    first = _material_text(_isotropic(), tmp_path / "a")
    second = _material_text(_isotropic(), tmp_path / "b")
    assert first == second


# ---------------------------------------------------------------------------
# the orthotropic deck
# ---------------------------------------------------------------------------


def test_an_orthotropic_material_writes_stiffness_not_moduli(tmp_path):
    """CalculiX wants stiffness; an engineer quotes moduli, which are
    compliance. Writing the engineering constants straight through would look
    plausible -- both are large positive numbers -- and be silently wrong."""
    material = _print_material()
    text = _material_text(material, tmp_path)

    assert "*ELASTIC, TYPE=ORTHO" in text
    # The in-plane modulus itself must not appear as a stiffness constant.
    constants = material.stiffness_matrix()
    assert constants[0] > material.modulus[0]
    assert f"{constants[0]:.9g}" in text


def test_an_orthotropic_material_gets_an_orientation(tmp_path):
    """Without it the solver would apply the weak through-layer direction
    along global z, whatever direction the part was really built in."""
    text = _material_text(_print_material(), tmp_path)

    assert "*ORIENTATION, NAME=" in text
    assert "ORIENTATION=" in text
    assert "*SOLID SECTION" in text


def test_the_orientation_follows_the_build_direction(tmp_path):
    """Two parts built in different directions must get different decks.

    If they came out identical, the build direction would be decorative and
    the whole feature would be doing nothing.
    """
    along_z = _material_text(_print_material((0.0, 0.0, 1.0)), tmp_path / "z")
    along_x = _material_text(_print_material((1.0, 0.0, 0.0)), tmp_path / "x")

    assert along_z != along_x


def test_the_orientation_is_stable_for_the_same_build_direction(tmp_path):
    first = _material_text(_print_material((0.0, 0.6, 0.8)), tmp_path / "a")
    second = _material_text(_print_material((0.0, 0.6, 0.8)), tmp_path / "b")
    assert first == second


def test_an_unnormalised_build_direction_gives_the_same_deck(tmp_path):
    """Only the direction matters, not how long the vector is. A user writing
    (0, 0, 2) means the same thing as (0, 0, 1)."""
    unit = _material_text(_print_material((0.0, 0.0, 1.0)), tmp_path / "unit")
    scaled = _material_text(_print_material((0.0, 0.0, 5.0)), tmp_path / "scaled")
    assert unit == scaled


def test_nine_constants_are_written(tmp_path):
    """CalculiX expects exactly nine, split eight then one across two lines."""
    text = _material_text(_print_material(), tmp_path)
    block = text.split("*ELASTIC, TYPE=ORTHO\n")[1].split("*DENSITY")[0]
    values = [v for line in block.strip().splitlines() for v in line.split(",")]
    assert len(values) == 9
    for value in values:
        assert float(value) > 0.0, "every orthotropic stiffness constant is positive"


def test_density_is_written_for_both_kinds(tmp_path):
    for material in (_isotropic(), _print_material()):
        text = _material_text(material, tmp_path / material.name.replace(" ", "_"))
        assert "*DENSITY" in text
        assert f"{material.density:.9g}" in text
