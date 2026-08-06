"""Consistent surface-load integration.

The property that matters: the nodal forces must sum to exactly the requested
total, and on a quadratic surface element the *corner* nodes must receive
essentially nothing.  Lumping the load evenly instead would put spurious force
at the corners, right where peak stress gets read.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.solvers.calculix.loads import (
    build_face_lookup,
    consistent_nodal_forces,
    surface_area,
    surface_element_faces,
)


def unit_square_tri3():
    """Two linear triangles covering a 2x2 square in the z=0 plane."""
    coordinates = {
        1: np.array([0.0, 0.0, 0.0]),
        2: np.array([2.0, 0.0, 0.0]),
        3: np.array([2.0, 2.0, 0.0]),
        4: np.array([0.0, 2.0, 0.0]),
    }
    triangles = np.array([[1, 2, 3], [1, 3, 4]], dtype=np.int64)
    return triangles, coordinates


def single_tri6():
    """One quadratic triangle with straight sides."""
    coordinates = {
        1: np.array([0.0, 0.0, 0.0]),
        2: np.array([2.0, 0.0, 0.0]),
        3: np.array([0.0, 2.0, 0.0]),
        4: np.array([1.0, 0.0, 0.0]),  # mid 1-2
        5: np.array([1.0, 1.0, 0.0]),  # mid 2-3
        6: np.array([0.0, 1.0, 0.0]),  # mid 3-1
    }
    triangles = np.array([[1, 2, 3, 4, 5, 6]], dtype=np.int64)
    return triangles, coordinates


def test_linear_triangles_sum_to_the_requested_total():
    triangles, coordinates = unit_square_tri3()
    forces = consistent_nodal_forces(triangles, coordinates, (0.0, 0.0, -1000.0))
    total = np.sum(list(forces.values()), axis=0)
    assert total == pytest.approx([0.0, 0.0, -1000.0], abs=1e-9)


def test_linear_triangle_load_splits_evenly_across_corners():
    triangles, coordinates = unit_square_tri3()
    forces = consistent_nodal_forces(triangles, coordinates, (0.0, 0.0, -900.0))
    # Nodes 1 and 3 are shared by both triangles, 2 and 4 by one each.
    assert forces[2][2] == pytest.approx(forces[4][2])
    assert forces[1][2] == pytest.approx(forces[3][2])
    assert forces[1][2] == pytest.approx(2 * forces[2][2])


def test_quadratic_triangle_puts_no_load_on_the_corners():
    """The exact integral of a corner shape function over a flat tri6 is zero."""
    triangles, coordinates = single_tri6()
    forces = consistent_nodal_forces(triangles, coordinates, (0.0, 0.0, -300.0))

    for corner in (1, 2, 3):
        assert forces[corner][2] == pytest.approx(0.0, abs=1e-9), (
            f"corner node {corner} should carry no load on a quadratic face"
        )
    for midside in (4, 5, 6):
        assert forces[midside][2] == pytest.approx(-100.0, rel=1e-9)


def test_quadratic_triangle_sums_to_the_total():
    triangles, coordinates = single_tri6()
    forces = consistent_nodal_forces(triangles, coordinates, (10.0, -20.0, -300.0))
    total = np.sum(list(forces.values()), axis=0)
    assert total == pytest.approx([10.0, -20.0, -300.0], abs=1e-9)


def test_direction_is_preserved():
    triangles, coordinates = unit_square_tri3()
    forces = consistent_nodal_forces(triangles, coordinates, (100.0, 200.0, 300.0))
    total = np.sum(list(forces.values()), axis=0)
    assert total == pytest.approx([100.0, 200.0, 300.0], abs=1e-9)


def test_surface_area_is_exact_for_flat_facets():
    triangles, coordinates = unit_square_tri3()
    assert surface_area(triangles, coordinates) == pytest.approx(4.0)


def test_zero_area_surface_is_rejected():
    coordinates = {1: np.zeros(3), 2: np.zeros(3), 3: np.zeros(3)}
    triangles = np.array([[1, 2, 3]], dtype=np.int64)
    with pytest.raises(ValueError, match="zero area"):
        consistent_nodal_forces(triangles, coordinates, (0.0, 0.0, -1.0))


class TestFaceLookup:
    def test_every_tet_face_is_registered(self):
        element_tags = np.array([1], dtype=np.int64)
        connectivity = np.array([[10, 20, 30, 40]], dtype=np.int64)
        lookup = build_face_lookup(element_tags, connectivity)
        assert len(lookup) == 4
        assert lookup[(10, 20, 30)] == (1, 1)

    def test_surface_triangles_resolve_to_element_faces(self):
        element_tags = np.array([7], dtype=np.int64)
        connectivity = np.array([[1, 2, 3, 4]], dtype=np.int64)
        lookup = build_face_lookup(element_tags, connectivity)
        faces = surface_element_faces(np.array([[1, 2, 3]], dtype=np.int64), lookup)
        assert faces == [(7, 1)]

    def test_an_unmatched_triangle_is_an_error_not_a_silent_drop(self):
        lookup = build_face_lookup(
            np.array([1], dtype=np.int64), np.array([[1, 2, 3, 4]], dtype=np.int64)
        )
        with pytest.raises(ValueError, match="could not be matched"):
            surface_element_faces(np.array([[91, 92, 93]], dtype=np.int64), lookup)

    def test_quadratic_tets_use_only_their_corner_nodes(self):
        element_tags = np.array([3], dtype=np.int64)
        connectivity = np.array([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]], dtype=np.int64)
        lookup = build_face_lookup(element_tags, connectivity)
        faces = surface_element_faces(np.array([[1, 2, 3, 5, 6, 7]], dtype=np.int64), lookup)
        assert faces == [(3, 1)]
