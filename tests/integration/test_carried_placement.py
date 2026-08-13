"""Where a sized carried item actually lands, on a real mesh.

The arithmetic is covered in ``tests/unit/test_carried_size.py`` and the
physics in ``tests/verification/test_carried_size.py``. What is left, and what
needs a mesh, is the step between them: finding which way is up off the face
the item bolts to, and refusing when there is no answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.carried import CarriedShape, CarriedSize
from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome, outcome_for
from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    ModalSettings,
    PointMass,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.deck import write_deck

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh]

LENGTH, WIDTH, DEPTH = 100.0, 10.0, 5.0


def _material() -> Material:
    return Material.from_engineering_units(
        name="Steel",
        elastic_modulus_mpa=210000.0,
        poisson_ratio=0.3,
        density_kg_m3=7850.0,
        allowable_stress_mpa=250.0,
    )


@pytest.fixture(scope="module")
def box_mesh(tmp_path_factory):
    """A plain box: the +x end face is flat, so 'up off it' is well defined."""
    directory = tmp_path_factory.mktemp("carried_box")
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="cantilever_box",
            parameters={"length": LENGTH, "width": WIDTH, "height": DEPTH},
        )
    )
    space = DesignSpace(
        (DesignVariable(id="length", minimum=LENGTH, maximum=LENGTH, default=LENGTH),)
    )
    geometry = provider.build(space.defaults(), directory / "geometry")
    regions = (
        SemanticRegion(
            "fixed_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(-1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
        SemanticRegion(
            "load_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
    )
    mesher = GmshMesher(MeshSpecification(global_size=4.0, minimum_size=1.5, element_order=2))
    mesh, _ = mesher.generate(geometry, regions, directory / "mesh")
    return mesh, directory


def _model(size: CarriedSize | None, region: str = "load_face") -> AnalysisModel:
    return AnalysisModel(
        name="placement",
        material=_material(),
        load_cases=(
            LoadCase(
                id="held",
                boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=(1, 2, 3)),),
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -1.0)),),
            ),
        ),
        point_masses=(PointMass(name="tip", region=region, mass=0.2e-3, size=size),),
        modal=ModalSettings(enabled=True, modes=4),
    )


def _carried_nodes(mesh_inp: str) -> np.ndarray:
    """Coordinates of the nodes written for the carried item."""
    rows: list[list[float]] = []
    grabbing = False
    for line in mesh_inp.splitlines():
        if line.startswith("*"):
            grabbing = line.upper().startswith("*NODE") and "NSET=NC_" in line.upper()
            continue
        if grabbing and line.strip():
            parts = line.split(",")
            rows.append([float(p) for p in parts[1:4]])
    return np.asarray(rows)


def test_the_item_stands_off_the_face_it_bolts_to(box_mesh, tmp_path) -> None:
    """The end face points along +x, so a 20 mm tall item's middle sits at
    x = 120 -- outside the part, not inside it.

    Getting the direction backwards is the failure this checks for, and it is
    silent: the frequency would come out plausible, and slightly wrong in the
    direction that looks safe.
    """
    mesh, _ = box_mesh
    size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=20.0)
    write_deck(_model(size), mesh, tmp_path / "deck")
    nodes = _carried_nodes((tmp_path / "deck" / "mesh.inp").read_text())

    assert len(nodes) == 8, "seven lumps and the node carrying the rotations"
    middle = nodes.mean(axis=0)
    assert middle[0] == pytest.approx(LENGTH + 10.0, abs=0.2)
    assert middle[1] == pytest.approx(WIDTH / 2.0, abs=0.2)
    assert middle[2] == pytest.approx(DEPTH / 2.0, abs=0.2)
    assert nodes[:, 0].min() > LENGTH, "part of the item was placed inside the part"


def test_a_taller_item_stands_further_off(box_mesh, tmp_path) -> None:
    mesh, _ = box_mesh
    heights = []
    for height in (10.0, 40.0):
        size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=height)
        write_deck(_model(size), mesh, tmp_path / f"deck{height:g}")
        nodes = _carried_nodes((tmp_path / f"deck{height:g}" / "mesh.inp").read_text())
        heights.append(float(nodes.mean(axis=0)[0]))
    assert heights[1] > heights[0]
    # The middle of a uniform item sits at half its height, so doubling and
    # doubling again moves it from 5 mm off the face to 20 mm off it.
    assert heights[0] == pytest.approx(LENGTH + 5.0, abs=0.2)
    assert heights[1] == pytest.approx(LENGTH + 20.0, abs=0.2)


def test_the_middle_can_be_moved_without_changing_the_shape(box_mesh, tmp_path) -> None:
    mesh, _ = box_mesh
    size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=20.0, centre_height=17.0)
    write_deck(_model(size), mesh, tmp_path / "deck")
    nodes = _carried_nodes((tmp_path / "deck" / "mesh.inp").read_text())
    assert float(nodes.mean(axis=0)[0]) == pytest.approx(LENGTH + 17.0, abs=0.2)


def test_the_item_is_tied_to_the_face(box_mesh, tmp_path) -> None:
    """Without the tie the lumps float free, and CalculiX would report modes
    for a set of unconnected masses rather than for the part."""
    mesh, _ = box_mesh
    size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=20.0)
    write_deck(_model(size), mesh, tmp_path / "deck")
    job = (tmp_path / "deck" / "job.inp").read_text()
    assert "*RIGID BODY" in job
    assert "R_LOAD_FACE" in job.split("*RIGID BODY")[0].split("*INCLUDE")[-1]
    # Before the first step, or it applies to only part of the analysis.
    assert job.index("*RIGID BODY") < job.index("*STEP")


def test_every_lump_is_given_weight(box_mesh, tmp_path) -> None:
    """Trap 18: a MASS element is not in Eall, so gravity misses it unless its
    set is named. A sized item has seven sets rather than one, and missing any
    of them would leave the item lighter than it is under acceleration."""
    mesh, _ = box_mesh
    size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=20.0)
    model = AnalysisModel(
        name="gravity",
        material=_material(),
        load_cases=(
            LoadCase(
                id="held",
                boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=(1, 2, 3)),),
                loads=(Load(kind=LoadKind.ACCELERATION, region=None, vector=(0.0, 0.0, -9810.0)),),
            ),
        ),
        point_masses=(PointMass(name="tip", region="load_face", mass=0.2e-3, size=size),),
    )
    write_deck(model, mesh, tmp_path / "deck")
    job = (tmp_path / "deck" / "job.inp").read_text()
    material = (tmp_path / "deck" / "material.inp").read_text()

    written = {
        line.split("ELSET=")[1].strip()
        for line in material.splitlines()
        if line.upper().startswith("*ELEMENT") and "TYPE=MASS" in line.upper()
    }
    assert len(written) == 7
    gravity_lines = [line for line in job.splitlines() if "GRAV" in line]
    named = {line.split(",")[0].strip() for line in gravity_lines}
    assert written <= named, f"gravity misses {sorted(written - named)}"
    assert "Eall" in named


def test_a_face_with_no_single_up_is_refused_rather_than_guessed(box_mesh, tmp_path) -> None:
    """A region that is not flat has no single direction that is 'up' off it.

    Built here from the two ends of the box, which point exactly opposite ways.
    Averaging them would put the item at the middle of the part, and the
    frequency that came back would look entirely ordinary. This is the
    ambiguity rule applied to a carried item: stop and say so.
    """
    mesh, _ = box_mesh
    mesh.surface_triangles["two_ends"] = np.concatenate(
        [mesh.surface_triangles["load_face"], mesh.surface_triangles["fixed_face"]]
    )
    mesh.surface_nodes["two_ends"] = np.unique(
        np.concatenate([mesh.surface_nodes["load_face"], mesh.surface_nodes["fixed_face"]])
    )
    try:
        size = CarriedSize(CarriedShape.CYLINDER, across=4.0, deep=0.0, height=20.0)
        with pytest.raises(EvaluationFailure) as caught:
            write_deck(_model(size, region="two_ends"), mesh, tmp_path / "deck")
    finally:
        del mesh.surface_triangles["two_ends"]
        del mesh.surface_nodes["two_ends"]

    assert caught.value.code is FailureCode.CARRIED_MASS_UNPLACEABLE
    assert "not flat" in str(caught.value)
    assert "180" in str(caught.value), "the message should say how far apart they point"
    # An infrastructure error, never an infeasible design: the same faces are
    # the same faces at every size, so the optimiser must learn nothing from it.
    assert outcome_for(caught.value.code) is Outcome.ERROR


def test_an_item_with_no_size_writes_the_deck_it_always_did(box_mesh, tmp_path) -> None:
    """The promise that this feature moved no existing project's numbers."""
    mesh, _ = box_mesh
    write_deck(_model(None), mesh, tmp_path / "deck")
    mesh_inp = (tmp_path / "deck" / "mesh.inp").read_text()
    job = (tmp_path / "deck" / "job.inp").read_text()
    assert "NSET=NC_" not in mesh_inp.upper()
    assert "*RIGID BODY" not in job
