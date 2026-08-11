"""The display mesh used by the 3D viewer, against the real OCC kernel.

This never touches a computed number -- see the module docstring in
``geometry/tessellate.py`` -- so what matters here is that a click can be
turned into the right face, and that the picture is not actively wrong: a
part is not somehow the wrong size, and a round face is not drawn faceted.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.project import GeometryDefinition
from openoptima.domain.variables import DesignSpace
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.geometry.tessellate import tessellate_solid
from openoptima.regions.signature import solid_face_signatures

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh, pytest.mark.gmsh]

FIXED = {
    "length": 120.0,
    "height": 90.0,
    "width": 60.0,
    "bolt_diameter": 9.0,
    "bolt_inset": 15.0,
}
EMPTY_SPACE = DesignSpace(())


def _build(tmp_path):
    provider = OccGeometryProvider(
        GeometryDefinition(provider="occ", template="l_bracket", parameters=FIXED)
    )
    artifact = provider.build(EMPTY_SPACE.defaults(), tmp_path)
    with gmsh_session() as gmsh:
        gmsh.model.add("tessellate_test")
        gmsh.model.occ.importShapes(str(artifact.brep_path))
        gmsh.model.occ.synchronize()
        volume_tag = gmsh.model.getEntities(3)[0][1]
        signatures = solid_face_signatures(gmsh, volume_tag)
        mesh = tessellate_solid(gmsh, volume_tag, scale_length=artifact.bbox.diagonal)
    return artifact, signatures, mesh


class TestTheMeshHasAConsistentShape:
    def test_three_vertices_per_triangle(self, tmp_path):
        _artifact, _sigs, mesh = _build(tmp_path)
        assert len(mesh.positions) == mesh.triangle_count * 9
        assert len(mesh.normals) == mesh.triangle_count * 9

    def test_one_face_tag_per_triangle_not_per_vertex(self, tmp_path):
        _artifact, _sigs, mesh = _build(tmp_path)
        assert len(mesh.face_tags) == mesh.triangle_count

    def test_it_produces_a_real_number_of_triangles(self, tmp_path):
        _artifact, _sigs, mesh = _build(tmp_path)
        assert mesh.triangle_count > 100


class TestEveryTriangleBelongsToARealFace:
    """This is the whole point: a click on a triangle has to resolve to
    exactly the face `describe_faces` would also work with."""

    def test_the_mesh_tags_are_exactly_the_signature_tags(self, tmp_path):
        _artifact, signatures, mesh = _build(tmp_path)
        assert set(mesh.face_tags) == {s.tag for s in signatures}

    def test_every_face_got_at_least_one_triangle(self, tmp_path):
        """A face with zero triangles could never be clicked."""
        _artifact, signatures, mesh = _build(tmp_path)
        tags = np.array(mesh.face_tags)
        for signature in signatures:
            assert np.sum(tags == signature.tag) > 0, f"face {signature.tag} has no triangles"


class TestTheTessellationIsNotActivelyWrong:
    """Not a precision claim -- see the module docstring -- but a real
    tessellation should not misrepresent the part it is drawing."""

    def test_the_bounding_box_matches_the_solid(self, tmp_path):
        artifact, _sigs, mesh = _build(tmp_path)
        assert mesh.bbox.as_tuple() == pytest.approx(artifact.bbox.as_tuple(), abs=1e-3)

    def test_triangle_area_sums_to_roughly_the_analytic_face_area(self, tmp_path):
        """Measured on the real bracket: every face agreed to under 0.5%,
        including the curved ones, where a faceted approximation always
        measures slightly less than the true analytic area it is inscribed
        in -- so a small negative difference on a cylinder is expected, not a
        bug, and this only guards against something far coarser than that."""
        _artifact, signatures, mesh = _build(tmp_path)
        positions = np.array(mesh.positions).reshape(-1, 3)
        tags = np.array(mesh.face_tags)
        for signature in signatures:
            triangle_indices = np.where(tags == signature.tag)[0]
            area = 0.0
            for index in triangle_indices:
                a, b, c = positions[3 * index], positions[3 * index + 1], positions[3 * index + 2]
                area += 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
            assert area == pytest.approx(signature.area, rel=0.02), (
                f"face {signature.tag} ({signature.surface_type.value}): "
                f"mesh area {area:.2f} vs analytic {signature.area:.2f}"
            )

    def test_every_normal_is_a_unit_vector(self, tmp_path):
        _artifact, _sigs, mesh = _build(tmp_path)
        normals = np.array(mesh.normals).reshape(-1, 3)
        lengths = np.linalg.norm(normals, axis=1)
        assert np.all(lengths > 1e-9), "a zero-length normal would fail to shade at all"
        assert np.allclose(lengths, 1.0, atol=1e-3)

    def test_a_curved_face_is_shaded_smooth_not_flat(self, tmp_path):
        """The reason for reading gmsh's analytic surface normal per vertex
        instead of the flat facet normal per triangle: a cylinder tessellated
        with one normal per facet would render as a faceted drum, not a
        round hole, however fine the mesh."""
        _artifact, signatures, mesh = _build(tmp_path)
        cylinder = next(s for s in signatures if s.surface_type.value == "cylinder")
        normals = np.array(mesh.normals).reshape(-1, 3)
        tags = np.array(mesh.face_tags)
        first_vertex_of_each_triangle = np.where(tags == cylinder.tag)[0] * 3
        distinct = {tuple(np.round(normals[i], 3)) for i in first_vertex_of_each_triangle}
        assert len(distinct) > 10, "expected many distinct normal directions around a hole"


class TestItDoesNotDisturbAnythingElseInTheSession:
    """The display mesh is thrown away (`gmsh.model.mesh.clear()`), so
    computing it must not change what the region signatures measure -- before
    or after. This is what makes it safe for the server to compute both from
    one build."""

    def test_signatures_are_identical_before_and_after(self, tmp_path):
        provider = OccGeometryProvider(
            GeometryDefinition(provider="occ", template="l_bracket", parameters=FIXED)
        )
        artifact = provider.build(EMPTY_SPACE.defaults(), tmp_path)
        with gmsh_session() as gmsh:
            gmsh.model.add("order_independence")
            gmsh.model.occ.importShapes(str(artifact.brep_path))
            gmsh.model.occ.synchronize()
            volume_tag = gmsh.model.getEntities(3)[0][1]

            before = solid_face_signatures(gmsh, volume_tag)
            tessellate_solid(gmsh, volume_tag, scale_length=artifact.bbox.diagonal)
            after = solid_face_signatures(gmsh, volume_tag)

        assert [s.area for s in before] == [s.area for s in after]
        assert [s.tag for s in before] == [s.tag for s in after]
