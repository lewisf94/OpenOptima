"""Meshing a shape that arrived as triangles, against the real gmsh.

The arithmetic that measures faces from triangles is covered in
``tests/unit/test_discrete_faces.py`` with a stand-in for gmsh.  What is checked
here is the part that only the real thing can show: that gmsh splits faces the
way this code expects, that a solid can be built from the pieces, and that the
mesh which comes out is fit to solve.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.domain.model import MeshSpecification
from openoptima.domain.regions import (
    BoundingBox,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.geometry.base import SurfaceArtifact
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.regions.discrete import measure_discrete_surface

pytestmark = pytest.mark.gmsh

LENGTH, DEPTH, THICKNESS = 40.0, 20.0, 6.0


def write_bar_stl(path, triangle_size: float = 2.0):
    with gmsh_session() as gmsh:
        gmsh.model.add("bar")
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, LENGTH, DEPTH, THICKNESS)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", triangle_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", triangle_size)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(path))
    return path


def measured(path, note: str = "triangles") -> SurfaceArtifact:
    with gmsh_session() as gmsh:
        gmsh.model.add("measure")
        gmsh.merge(str(path))
        gmsh.model.mesh.classifySurfaces(math.radians(30.0), True, True, math.radians(180.0))
        surface = measure_discrete_surface(gmsh)
    return SurfaceArtifact(
        stl_path=path,
        volume=surface.volume,
        bbox=surface.bbox,
        surface_area=surface.surface_area,
        source_description=note,
    )


def face_at(x: float, normal, name: str, mode=SelectionMode.ALL) -> SemanticRegion:
    return SemanticRegion(
        name,
        RegionSelector(
            surface_type=SurfaceType.PLANE,
            normal=normal,
            within_box=BoundingBox(x - 0.1, -1.0, -1.0, x + 0.1, DEPTH + 1, THICKNESS + 1),
            mode=mode,
        ),
    )


REGIONS = (
    face_at(0.0, (-1.0, 0.0, 0.0), "left"),
    face_at(LENGTH, (1.0, 0.0, 0.0), "right"),
)

SPECIFICATION = MeshSpecification(
    global_size=3.0,
    minimum_size=1.5,
    element_order=2,
    curvature_refinement=False,
    size_from_thickness=False,
)


class TestGmshSplitsFacesAndTheyArePutBack:
    def test_gmsh_really_does_hand_a_flat_face_over_in_pieces(self, tmp_path):
        """The premise the whole module rests on, checked against gmsh itself."""
        stl = write_bar_stl(tmp_path / "bar.stl")
        with gmsh_session() as gmsh:
            gmsh.model.add("check")
            gmsh.merge(str(stl))
            gmsh.model.mesh.classifySurfaces(math.radians(30.0), True, True, math.radians(180.0))
            patches = len(gmsh.model.getEntities(2))
            surface = measure_discrete_surface(gmsh)

        assert patches > 6, "gmsh used to split faces; if it stopped, the merging is untested"
        assert len(surface.signatures) == 6

    def test_the_measured_volume_matches_the_solid(self, tmp_path):
        surface = measured(write_bar_stl(tmp_path / "bar.stl"))
        assert surface.volume == pytest.approx(LENGTH * DEPTH * THICKNESS, rel=1e-9)


class TestMeshingFromTriangles:
    def test_it_produces_a_solvable_mesh_with_the_regions_on_it(self, tmp_path):
        surface = measured(write_bar_stl(tmp_path / "bar.stl"))
        mesh, region_map = GmshMesher(SPECIFICATION).generate_from_surface(
            surface, REGIONS, tmp_path / "mesh"
        )

        assert mesh.element_type == "C3D10"
        assert mesh.element_count > 0
        assert set(region_map.matches) == {"left", "right"}
        for name in ("left", "right"):
            assert mesh.surface_triangles[name].shape[1] == 6
            assert len(mesh.surface_nodes[name]) > 0
            assert region_map[name].total_area == pytest.approx(DEPTH * THICKNESS, rel=1e-6)

    def test_no_element_comes_out_inside_out(self, tmp_path):
        """The measurement that decided how the midside nodes are placed.

        Pushing them onto the surface turned 6 of 2060 elements inside out on a
        real topology result, and a finer mesh did not help. A surface made of
        triangles has no curve to push them onto.
        """
        surface = measured(write_bar_stl(tmp_path / "bar.stl"))
        mesh, _regions = GmshMesher(SPECIFICATION).generate_from_surface(
            surface, REGIONS, tmp_path / "mesh"
        )
        assert mesh.quality is not None
        assert mesh.quality.inverted_elements == 0
        assert mesh.quality.min_scaled_jacobian > 0.0

    def test_the_mesh_fills_the_shape(self, tmp_path):
        surface = measured(write_bar_stl(tmp_path / "bar.stl"))
        mesh, _regions = GmshMesher(SPECIFICATION).generate_from_surface(
            surface, REGIONS, tmp_path / "mesh"
        )
        assert mesh.quality is not None
        assert mesh.quality.volume_error < 0.01

    def test_a_region_can_cover_several_pieces_of_one_face(self, tmp_path):
        """A region is expanded back into gmsh's own pieces before meshing.

        Get this wrong and a load lands on a fraction of the face it was meant
        for, which is the worst thing this software can do.
        """
        surface = measured(write_bar_stl(tmp_path / "bar.stl", triangle_size=1.0))
        top = SemanticRegion(
            "top",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(0.0, 0.0, 1.0),
                within_box=BoundingBox(
                    -1.0, -1.0, THICKNESS - 0.1, LENGTH + 1, DEPTH + 1, THICKNESS + 0.1
                ),
                mode=SelectionMode.ALL,
            ),
        )
        mesh, region_map = GmshMesher(SPECIFICATION).generate_from_surface(
            surface, (top,), tmp_path / "mesh"
        )
        assert region_map["top"].total_area == pytest.approx(LENGTH * DEPTH, rel=1e-6)
        # Every triangle of the whole top face, not just one piece of it.
        assert len(mesh.surface_nodes["top"]) > 50


class TestItRefusesWhatItCannotUse:
    def test_a_file_with_no_triangles_is_reported_plainly(self, tmp_path):
        empty = tmp_path / "empty.stl"
        empty.write_text("solid empty\nendsolid empty\n")
        surface = SurfaceArtifact(
            stl_path=empty,
            volume=0.0,
            bbox=BoundingBox(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            surface_area=0.0,
        )
        with pytest.raises(EvaluationFailure) as raised:
            GmshMesher(SPECIFICATION).generate_from_surface(surface, REGIONS, tmp_path / "mesh")
        assert raised.value.code is FailureCode.INVALID_SOLID

    def test_a_region_that_matches_nothing_stops_rather_than_guessing(self, tmp_path):
        surface = measured(write_bar_stl(tmp_path / "bar.stl"))
        nowhere = face_at(LENGTH * 3, (1.0, 0.0, 0.0), "nowhere")
        with pytest.raises(EvaluationFailure) as raised:
            GmshMesher(SPECIFICATION).generate_from_surface(surface, (nowhere,), tmp_path / "mesh")
        assert raised.value.code is FailureCode.REGION_NOT_FOUND
