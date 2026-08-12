"""Features applied to a real shape, with a real CAD kernel.

The volumes are checked against the closed form rather than against a number
recorded from an earlier run. A fillet of radius *r* along a straight
90-degree corner of length *L* removes ``r^2 (1 - pi/4) L``; a chamfer of
size *s* removes ``s^2 L / 2``. A feature placed on the wrong edge, or built
at the wrong size, does not land on those figures by accident.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome
from openoptima.domain.features import EdgeFeature, FeatureKind
from openoptima.domain.variables import DesignSpace, DesignVariable, VariableType
from openoptima.geometry import create_provider
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.regions.describe import BuildSample, describe_faces
from openoptima.regions.signature import solid_face_signatures
from openoptima.schema.loader import load_project

pytestmark = pytest.mark.gmsh

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROJECT = EXAMPLES / "imported_bracket_fillet" / "project.yaml"

#: The corner the example rounds off runs the full 60 mm width of the part.
CORNER_LENGTH = 60.0
#: The imported bracket, before anything is added to it.
BASE_VOLUME = 219043.7403


@pytest.fixture(scope="module")
def project():
    return load_project(PROJECT)


@pytest.fixture(scope="module")
def space() -> DesignSpace:
    """A range wider than the example's, to reach the sizes that fail."""
    return DesignSpace(
        (
            DesignVariable(
                id="corner_radius",
                type=VariableType.CONTINUOUS,
                minimum=0.1,
                maximum=60.0,
                default=6.0,
            ),
        )
    )


def _provider(project, features=None):
    definition = (
        project.geometry if features is None else replace(project.geometry, features=features)
    )
    provider = create_provider(definition, project.regions)
    provider.root = PROJECT.parent
    return provider


def _build(provider, space, size: float, directory: Path):
    return provider.build(space.decode({"corner_radius": size}), directory)


class TestTheMetalRemovedMatchesTheClosedForm:
    @pytest.mark.parametrize("radius", [0.5, 2.0, 10.0, 16.0])
    def test_a_fillet_removes_the_corner_it_should(self, project, space, tmp_path, radius) -> None:
        artifact = _build(_provider(project), space, radius, tmp_path / f"r{radius}")
        expected = radius**2 * (1.0 - math.pi / 4.0) * CORNER_LENGTH
        assert artifact.volume == pytest.approx(BASE_VOLUME - expected, rel=1e-6)

    @pytest.mark.parametrize("size", [3.0, 12.0])
    def test_a_chamfer_removes_half_the_square(self, project, space, tmp_path, size) -> None:
        chamfer = (
            EdgeFeature(
                name="cut_corner",
                kind=FeatureKind.CHAMFER,
                between=("arm_top", "load_face"),
                size="corner_radius",
            ),
        )
        artifact = _build(_provider(project, chamfer), space, size, tmp_path / f"c{size}")
        expected = 0.5 * size**2 * CORNER_LENGTH
        assert artifact.volume == pytest.approx(BASE_VOLUME - expected, rel=1e-6)

    def test_the_record_says_what_was_done(self, project, space, tmp_path) -> None:
        artifact = _build(_provider(project), space, 10.0, tmp_path / "record")
        record = artifact.metadata["features"][0]
        assert record["name"] == "outer_corner"
        assert record["kind"] == "fillet"
        assert record["size_mm"] == 10.0
        # One edge, not two. A selector that quietly picked up a second face
        # would double this while still producing a perfectly good part.
        assert record["edge_count"] == 1
        removed = record["volume_before_mm3"] - record["volume_after_mm3"]
        assert removed == pytest.approx(100.0 * (1.0 - math.pi / 4.0) * CORNER_LENGTH, rel=1e-6)


class TestTheDesignVectorReachesAnImportedShape:
    """The whole point of the piece: an import with something to optimise."""

    def test_two_sizes_give_two_different_parts(self, project, space, tmp_path) -> None:
        small = _build(_provider(project), space, 2.0, tmp_path / "small")
        large = _build(_provider(project), space, 16.0, tmp_path / "large")
        assert large.volume < small.volume
        assert small.surface_area != large.surface_area

    def test_the_same_size_gives_the_same_part_twice(self, project, space, tmp_path) -> None:
        # A design that cannot be reproduced from its own inputs cannot be
        # cached, defended or verified.
        first = _build(_provider(project), space, 7.5, tmp_path / "first")
        second = _build(_provider(project), space, 7.5, tmp_path / "second")
        assert first.volume == second.volume
        assert first.surface_area == second.surface_area

    def test_without_features_the_design_vector_changes_nothing(
        self, project, space, tmp_path
    ) -> None:
        provider = _provider(project, ())
        assert _build(provider, space, 2.0, tmp_path / "a").volume == pytest.approx(
            _build(provider, space, 16.0, tmp_path / "b").volume
        )


class TestAFeatureThatCannotBeBuiltIsABadDesign:
    """Never an infrastructure error: the optimiser has to learn from it."""

    @pytest.mark.parametrize("radius", [19.0, 25.0, 60.0])
    def test_a_round_bigger_than_the_material_is_infeasible(
        self, project, space, tmp_path, radius
    ) -> None:
        # Measured: 18.99 mm builds, 19.0 mm is refused by the kernel. The
        # face being rounded is 19 mm tall.
        with pytest.raises(EvaluationFailure) as caught:
            _build(_provider(project), space, radius, tmp_path / f"r{radius}")
        assert caught.value.code is FailureCode.FEATURE_FAILED
        assert caught.value.outcome is Outcome.INFEASIBLE

    def test_the_size_just_below_that_still_builds(self, project, space, tmp_path) -> None:
        # The dangerous band. It builds, it meshes, it solves -- and it leaves
        # 0.6 mm2 of the face the load sits on. Only `min_area_mm2` catches it;
        # the kernel refusing at 19.0 mm is no protection at all.
        artifact = _build(_provider(project), space, 18.99, tmp_path / "sliver")
        assert artifact.volume > 0
        with gmsh_session() as gmsh:
            gmsh.model.add("sliver")
            gmsh.model.occ.importShapes(str(artifact.brep_path))
            gmsh.model.occ.synchronize()
            signatures = solid_face_signatures(gmsh, gmsh.model.getEntities(3)[0][1])
        load_face = [
            s
            for s in signatures
            if s.normal is not None and s.normal[0] > 0.99 and s.centroid[0] > 119.0
        ]
        assert len(load_face) == 1
        assert load_face[0].area == pytest.approx(0.6, abs=0.05)

    def test_regions_that_never_touch_are_reported_not_ignored(
        self, project, space, tmp_path
    ) -> None:
        # mounting_face is at x=0 and load_face at x=120: there is no corner
        # between them anywhere on this part.
        nowhere = (
            EdgeFeature(
                name="nowhere",
                kind=FeatureKind.FILLET,
                between=("mounting_face", "load_face"),
                size=3.0,
            ),
        )
        with pytest.raises(EvaluationFailure) as caught:
            _build(_provider(project, nowhere), space, 3.0, tmp_path / "nowhere")
        assert caught.value.code is FailureCode.FEATURE_EDGES_NOT_FOUND
        assert caught.value.outcome is Outcome.INFEASIBLE
        assert "do not touch" in caught.value.message

    def test_a_variable_that_reaches_zero_is_refused_per_design(self, project, tmp_path) -> None:
        # A fixed size of zero is caught when the project file loads, because
        # it is wrong at every design point. A design variable whose range
        # runs down to zero is not: only the designs at that end are bad, and
        # each is refused as infeasible so the optimiser learns the boundary.
        reaches_zero = DesignSpace(
            (
                DesignVariable(
                    id="corner_radius",
                    type=VariableType.CONTINUOUS,
                    minimum=0.0,
                    maximum=10.0,
                    default=5.0,
                ),
            )
        )
        with pytest.raises(EvaluationFailure) as caught:
            _build(_provider(project), reaches_zero, 0.0, tmp_path / "zero")
        assert caught.value.code is FailureCode.INVALID_DESIGN_VARIABLES
        assert caught.value.outcome is Outcome.INFEASIBLE


class TestAddingAFilletRenumbersEveryFace:
    """The measurement that justifies never storing a face or edge number.

    This is not a hypothetical. Every face of the example bracket changed
    number when one fillet was added: the top of the arm went from 5 to 2,
    the loaded end from 7 to 5, the base from 8 to 7.
    """

    def test_the_faces_are_the_same_surfaces_under_different_numbers(
        self, project, space, tmp_path
    ) -> None:
        def faces(features, size, name):
            artifact = _build(_provider(project, features), space, size, tmp_path / name)
            with gmsh_session() as gmsh:
                gmsh.model.add(name)
                gmsh.model.occ.importShapes(str(artifact.brep_path))
                gmsh.model.occ.synchronize()
                return solid_face_signatures(gmsh, gmsh.model.getEntities(3)[0][1])

        plain = faces((), 6.0, "plain")
        rounded = faces(None, 6.0, "rounded")

        # The base face is 7200 mm2 and is untouched by the fillet, so it is
        # the same physical surface in both. Its number is not the same.
        def base_face(signatures):
            return next(s for s in signatures if s.normal is not None and s.normal[2] < -0.99)

        assert base_face(plain).area == pytest.approx(base_face(rounded).area)
        assert base_face(plain).tag != base_face(rounded).tag, (
            "if the numbers happen to line up on this shape, the test has "
            "stopped demonstrating anything -- the rule still holds"
        )
        assert len(rounded) == len(plain) + 1  # the new round face


class TestTheAddedFaceCanBeDescribed:
    """A feature creates a face, and that face has to be nameable.

    Otherwise you can add a fillet and then not put a mesh refinement or a
    stress exclusion on it, which is most of the reason to add one.
    """

    def test_the_new_round_face_gets_a_description_that_survives_the_radius_changing(
        self, project, space, tmp_path
    ) -> None:
        def measure(size, name):
            artifact = _build(_provider(project), space, size, tmp_path / name)
            with gmsh_session() as gmsh:
                gmsh.model.add(name)
                gmsh.model.occ.importShapes(str(artifact.brep_path))
                gmsh.model.occ.synchronize()
                signatures = solid_face_signatures(gmsh, gmsh.model.getEntities(3)[0][1])
            return signatures, artifact.bbox.diagonal

        small, small_scale = measure(3.0, "small")
        large, large_scale = measure(12.0, "large")

        target = [s for s in small if s.radius is not None and abs(s.radius - 3.0) < 0.01]
        assert len(target) == 1

        described = describe_faces(
            target,
            small,
            scale_length=small_scale,
            name="new_corner",
            alternatives=[BuildSample(large, large_scale, "the largest radius")],
        )
        # It must not lean on the radius: that is the thing being varied. The
        # describer works this out from the evidence rather than being told.
        assert "radius" not in described.filters_used
        assert described.checked_against == 1
        assert not described.warnings
