"""Turning a blocky topology result into a solid that can be analysed.

The behaviour worth guarding is what this refuses and what it says out loud.
Smoothing removes material, material is strength, and a shape that came out in
two pieces is not a part. None of that may pass in silence.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.topology import solidify as sol

trimesh = pytest.importorskip("trimesh")


def brick_mesh(nx: int, ny: int, nz: int, size: float = 1.0, skip=()) -> str:
    """A CalculiX deck of hex elements, with ``skip`` elements left out."""

    def nid(i, j, k):
        return 1 + i + (nx + 1) * j + (nx + 1) * (ny + 1) * k

    lines = ["*NODE"]
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                lines.append(f"{nid(i, j, k)}, {i * size}, {j * size}, {k * size}")
    lines.append("*ELEMENT, TYPE=C3D8, ELSET=state1")
    number = 0
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                number += 1
                if (i, j, k) in skip:
                    continue
                corners = [
                    nid(i, j, k),
                    nid(i + 1, j, k),
                    nid(i + 1, j + 1, k),
                    nid(i, j + 1, k),
                    nid(i, j, k + 1),
                    nid(i + 1, j, k + 1),
                    nid(i + 1, j + 1, k + 1),
                    nid(i, j + 1, k + 1),
                ]
                lines.append(f"{number}, " + ", ".join(str(c) for c in corners))
    return "\n".join(lines) + "\n"


@pytest.fixture
def cube(tmp_path):
    path = tmp_path / "file010_state1.inp"
    path.write_text(brick_mesh(3, 3, 3))
    return path


class TestReadingTheResult:
    def test_a_hex_mesh_is_read(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        assert element_type == "C3D8"
        assert len(elements) == 27
        assert len(nodes) == 4**3

    def test_an_unsupported_element_type_is_refused(self, tmp_path):
        path = tmp_path / "odd.inp"
        path.write_text("*NODE\n1, 0, 0, 0\n*ELEMENT, TYPE=B31, ELSET=s\n1, 1, 1\n")
        with pytest.raises(EvaluationFailure) as caught:
            sol.read_element_mesh(path)
        assert caught.value.code == FailureCode.RESULT_PARSE_FAILED
        assert "B31" in str(caught.value)

    def test_an_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "empty.inp"
        path.write_text("** nothing here\n")
        with pytest.raises(EvaluationFailure, match="nodes and elements"):
            sol.read_element_mesh(path)

    def test_second_order_elements_reuse_their_corners(self):
        """C3D10 is a C3D20's tetrahedral cousin: corners first, then midsides."""
        assert sol.ELEMENT_FACES["C3D10"] == sol.ELEMENT_FACES["C3D4"]
        assert sol.ELEMENT_FACES["C3D20"] == sol.ELEMENT_FACES["C3D8"]


class TestFindingTheOutside:
    def test_a_solid_cube_gives_a_sealed_surface(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        mesh.fix_normals()
        assert mesh.is_watertight
        # A 3x3x3 block of unit cubes.
        assert mesh.volume == pytest.approx(27.0)

    def test_interior_faces_are_dropped(self, cube):
        """A face shared by two elements is inside, and cancels.

        Six faces per side of a 3x3x3 block: 6 x 9 = 54 quads, so 108 triangles.
        """
        nodes, elements, element_type = sol.read_element_mesh(cube)
        _vertices, faces = sol.boundary_surface(nodes, elements, element_type)
        assert len(faces) == 108

    def test_a_hole_through_the_middle_is_kept(self, tmp_path):
        """A topology result is mostly holes; they must survive extraction."""
        path = tmp_path / "holed.inp"
        path.write_text(brick_mesh(3, 3, 3, skip={(1, 1, 0), (1, 1, 1), (1, 1, 2)}))
        nodes, elements, element_type = sol.read_element_mesh(path)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        mesh.fix_normals()
        assert mesh.is_watertight
        assert mesh.volume == pytest.approx(24.0)  # 27 less the 3 removed


class TestSmoothing:
    """Taubin alternates a shrinking pass with an inflating one."""

    def test_an_odd_number_of_passes_is_rounded_up(self, cube):
        """The trap this enforces.

        Measured on a real result: one pass costs 14.0 per cent of the volume,
        two cost 0.8 per cent, three cost 14.9 per cent. Stopping on an odd
        pass leaves the shape shrunk, and nothing about the file would say so.
        """
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)

        for asked, expected in ((1, 2), (3, 4), (5, 6), (6, 6), (0, 0)):
            _moved, used = sol.smooth(vertices, faces, asked)
            assert used == expected, f"asked {asked}, expected {expected}, got {used}"

    def test_the_default_is_even(self):
        assert sol.DEFAULT_SMOOTHING_PASSES % 2 == 0

    def test_zero_passes_changes_nothing(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)
        moved, used = sol.smooth(vertices, faces, 0)
        assert used == 0
        assert np.array_equal(moved, vertices)

    def test_negative_passes_are_refused(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)
        with pytest.raises(ValueError, match="negative"):
            sol.smooth(vertices, faces, -2)


class TestSolidify:
    def test_a_sound_result_comes_back_sealed_and_whole(self, cube):
        result = sol.to_solid(cube)
        assert result.watertight
        assert result.body_count == 1
        assert result.smoothing_passes == sol.DEFAULT_SMOOTHING_PASSES
        assert result.volume_mm3 > 0

    def test_the_material_smoothing_removed_is_reported(self, cube):
        """Not hidden. Material is strength, and the shape is now weaker."""
        result = sol.to_solid(cube)
        assert result.volume_before_smoothing_mm3 == pytest.approx(27.0)
        assert result.volume_change <= 0.0

    def test_a_big_loss_is_said_out_loud(self, cube):
        result = sol.to_solid(cube, smoothing_passes=40)
        if result.volume_change < -sol.VOLUME_LOSS_WARNING:
            assert any("removed" in w for w in result.warnings)

    def test_rounding_the_passes_up_is_explained(self, cube):
        result = sol.to_solid(cube, smoothing_passes=5)
        assert result.smoothing_passes == 6
        assert any("odd number" in w for w in result.warnings)

    def test_a_result_in_two_pieces_is_refused(self, tmp_path):
        """A real outcome when too much material is removed.

        Two cubes touching at nothing. That is not a part, and no amount of
        analysis makes it one.
        """
        path = tmp_path / "split.inp"
        lines = ["*NODE"]
        tag = 0
        for offset in (0.0, 10.0):
            for k in (0.0, 1.0):
                for j in (0.0, 1.0):
                    for i in (0.0, 1.0):
                        tag += 1
                        lines.append(f"{tag}, {i + offset}, {j}, {k}")
        lines.append("*ELEMENT, TYPE=C3D8, ELSET=state1")
        lines.append("1, 1, 2, 4, 3, 5, 6, 8, 7")
        lines.append("2, 9, 10, 12, 11, 13, 14, 16, 15")
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(EvaluationFailure) as caught:
            sol.to_solid(path)
        assert caught.value.code == FailureCode.INVALID_SOLID
        assert "separate pieces" in str(caught.value)

    def test_the_surface_can_be_written_for_re_analysis(self, cube, tmp_path):
        result = sol.to_solid(cube)
        written = result.write_stl(tmp_path / "out" / "shape.stl")
        assert written.is_file()
        assert written.stat().st_size > 0

        reloaded = trimesh.load(written)
        assert reloaded.is_watertight
        assert reloaded.volume == pytest.approx(result.volume_mm3, rel=1e-6)


class TestFlatFacesSurviveSmoothing:
    """The face a part bolts to must still be flat afterwards.

    A topology result inherits the flat faces of the space it was given: the
    mounting face, the face the load arrives on, any symmetry plane. Smoothing
    moves every vertex, so without a constraint those faces come out domed.

    Measured on a real result: 27 vertices sat exactly at x = 0 before
    smoothing and **none** did after, scattered across 0.55 mm. That breaks two
    things at once -- a domed mounting face will not seat against the thing it
    bolts to, and the selectors that put the loads back on for re-analysis look
    for a plane and find nothing.
    """

    def test_the_outer_faces_are_found(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, _faces = sol.boundary_surface(nodes, elements, element_type)
        hold = sol.flat_face_constraints(vertices)

        # A cube: every surface vertex is on at least one face.
        assert hold.any(axis=1).all()
        # And the eight corners are held in all three directions.
        assert (hold.all(axis=1)).sum() == 8

    def test_a_vertex_may_still_slide_within_its_own_face(self, cube):
        """Held per coordinate, not per vertex.

        A vertex on the x = 0 face keeps its x but may move in y and z, so the
        outline of the face smooths while the face itself stays flat.
        """
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, _faces = sol.boundary_surface(nodes, elements, element_type)
        hold = sol.flat_face_constraints(vertices)

        on_one_face_only = hold.sum(axis=1) == 1
        assert on_one_face_only.any(), "expected vertices on exactly one face"

    def test_held_coordinates_do_not_move(self, cube):
        nodes, elements, element_type = sol.read_element_mesh(cube)
        vertices, faces = sol.boundary_surface(nodes, elements, element_type)
        hold = sol.flat_face_constraints(vertices)

        moved, _used = sol.smooth(vertices, faces, 6, hold)
        assert np.array_equal(moved[hold], vertices[hold])

    def test_holding_the_faces_does_not_cost_extra_material(self, cube):
        """Regression guard on a defect this very constraint introduced.

        Applying the constraint by asking the smoothing library for one pass at
        a time restarts its internal counter every time, so every pass shrinks
        and none dilates -- silently turning Taubin into the plain Laplacian
        smoothing that dissolves a part. Measured on a real result: 17.4 per
        cent of the volume lost against 0.3 per cent.

        Holding the faces should if anything *save* material, because the ends
        can no longer pull inwards.
        """
        free = sol.to_solid(cube, preserve_flat_faces=False)
        held = sol.to_solid(cube, preserve_flat_faces=True)
        assert held.volume_mm3 >= free.volume_mm3

    def test_the_shape_is_still_sealed_afterwards(self, cube):
        held = sol.to_solid(cube, preserve_flat_faces=True)
        assert held.watertight
        assert held.body_count == 1

    def test_it_is_on_by_default(self, cube):
        default = sol.to_solid(cube)
        held = sol.to_solid(cube, preserve_flat_faces=True)
        assert default.volume_mm3 == pytest.approx(held.volume_mm3)
