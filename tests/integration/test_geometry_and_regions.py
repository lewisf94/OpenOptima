"""Geometry building and region resolution against the real CAD kernel."""

from __future__ import annotations

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, Outcome
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import (
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.gmsh_session import gmsh_session
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.regions.matcher import resolve_regions
from openoptima.regions.signature import outward_normal_check, solid_face_signatures

from ..conftest import requires_gmsh

pytestmark = [requires_gmsh, pytest.mark.gmsh]

SPACE = DesignSpace(
    (
        DesignVariable(id="thickness_h", minimum=5.0, maximum=20.0, default=10.0),
        DesignVariable(id="thickness_v", minimum=5.0, maximum=20.0, default=10.0),
        DesignVariable(id="fillet_radius", minimum=3.0, maximum=25.0, default=8.0),
    )
)

FIXED = {"length": 120.0, "height": 90.0, "width": 60.0, "bolt_diameter": 9.0, "bolt_inset": 15.0}


def provider() -> OccGeometryProvider:
    return OccGeometryProvider(
        GeometryDefinition(provider="occ", template="l_bracket", parameters=FIXED)
    )


def signatures_for(artifact):
    with gmsh_session() as gmsh:
        gmsh.model.add("t")
        gmsh.model.occ.importShapes(str(artifact.brep_path))
        gmsh.model.occ.synchronize()
        volume_tag = gmsh.model.getEntities(3)[0][1]
        return solid_face_signatures(gmsh, volume_tag)


class TestGeometryBuild:
    def test_builds_a_single_solid_with_positive_volume(self, tmp_path):
        artifact = provider().build(SPACE.defaults(), tmp_path)
        assert artifact.solid_count == 1
        assert artifact.volume > 0
        assert artifact.brep_path.exists()

    def test_volume_grows_with_thickness(self, tmp_path):
        thin = provider().build(
            SPACE.decode({"thickness_h": 6.0, "thickness_v": 6.0, "fillet_radius": 5.0}),
            tmp_path / "thin",
        )
        thick = provider().build(
            SPACE.decode({"thickness_h": 18.0, "thickness_v": 18.0, "fillet_radius": 5.0}),
            tmp_path / "thick",
        )
        assert thick.volume > thin.volume

    def test_geometry_is_deterministic(self, tmp_path):
        first = provider().build(SPACE.defaults(), tmp_path / "a")
        second = provider().build(SPACE.defaults(), tmp_path / "b")
        assert first.volume == pytest.approx(second.volume, rel=1e-12)

    def test_cantilever_volume_is_exact(self, tmp_path):
        box = OccGeometryProvider(
            GeometryDefinition(
                provider="occ",
                template="cantilever_box",
                parameters={"length": 100.0, "width": 10.0, "height": 20.0},
            )
        )
        space = DesignSpace(
            (DesignVariable(id="length", minimum=100.0, maximum=100.0, default=100.0),)
        )
        artifact = box.build(space.defaults(), tmp_path)
        assert artifact.volume == pytest.approx(100.0 * 10.0 * 20.0, rel=1e-9)


class TestImpossibleDesignsAreInfeasible:
    """A design that cannot exist is information for the optimiser, not a crash."""

    def test_oversized_fillet_is_rejected_before_meshing(self, tmp_path):
        """A fillet larger than the material available at the corner cannot exist.

        Rejected analytically, in microseconds, rather than by letting the CAD
        kernel fail somewhere inside a boolean operation.
        """
        # A small bracket: only 30 mm of material at the internal corner, so the
        # 0.8 x clearance rule caps the fillet at 24 mm.
        small = OccGeometryProvider(
            GeometryDefinition(
                provider="occ",
                template="l_bracket",
                parameters={**FIXED, "length": 40.0, "height": 40.0},
            )
        )
        design = SPACE.decode({"thickness_h": 10.0, "thickness_v": 10.0, "fillet_radius": 25.0})
        with pytest.raises(EvaluationFailure) as info:
            small.build(design, tmp_path)
        assert info.value.outcome is Outcome.INFEASIBLE
        assert info.value.code is FailureCode.MANUFACTURING_RULE_VIOLATED

    def test_the_same_fillet_is_accepted_when_there_is_room_for_it(self, tmp_path):
        """The rule must reject what cannot exist, not merely anything large."""
        design = SPACE.decode({"thickness_h": 10.0, "thickness_v": 10.0, "fillet_radius": 25.0})
        artifact = provider().build(design, tmp_path)
        assert artifact.volume > 0

    def test_thickness_swallowing_the_arm_is_rejected(self, tmp_path):
        wide = DesignSpace(
            (DesignVariable(id="thickness_v", minimum=5.0, maximum=200.0, default=150.0),)
        )
        with pytest.raises(EvaluationFailure) as info:
            provider().build(wide.defaults(), tmp_path)
        assert info.value.outcome is Outcome.INFEASIBLE

    def test_bolt_hole_breaking_out_is_rejected(self, tmp_path):
        bad = OccGeometryProvider(
            GeometryDefinition(
                provider="occ",
                template="l_bracket",
                parameters={**FIXED, "bolt_diameter": 40.0},
            )
        )
        with pytest.raises(EvaluationFailure) as info:
            bad.build(SPACE.defaults(), tmp_path)
        assert info.value.code is FailureCode.MANUFACTURING_RULE_VIOLATED


class TestRegionResolution:
    def regions(self):
        return (
            SemanticRegion(
                "mounting_face",
                RegionSelector(
                    surface_type=SurfaceType.PLANE,
                    normal=(-1.0, 0.0, 0.0),
                    normal_tolerance_deg=2.0,
                    prefer_largest=True,
                ),
            ),
            SemanticRegion(
                "bolt_holes",
                RegionSelector(
                    surface_type=SurfaceType.CYLINDER,
                    min_radius=4.0,
                    max_radius=5.0,
                    mode=SelectionMode.ALL,
                ),
            ),
        )

    def test_normals_point_out_of_the_solid(self, tmp_path):
        artifact = provider().build(SPACE.defaults(), tmp_path)
        outward, ratio = outward_normal_check(signatures_for(artifact), artifact.volume)
        assert outward
        assert ratio == pytest.approx(1.0, abs=0.1)

    def test_cylinder_radius_is_measured_accurately(self, tmp_path):
        """A partial cylinder — the fillet — must not confuse the fit."""
        artifact = provider().build(
            SPACE.decode({"thickness_h": 10.0, "thickness_v": 10.0, "fillet_radius": 8.0}),
            tmp_path,
        )
        radii = sorted(
            s.radius
            for s in signatures_for(artifact)
            if s.surface_type is SurfaceType.CYLINDER and s.radius
        )
        assert radii == pytest.approx([4.5, 4.5, 8.0], rel=1e-3)

    @pytest.mark.parametrize(
        "design",
        [
            {"thickness_h": 5.0, "thickness_v": 5.0, "fillet_radius": 3.0},
            {"thickness_h": 10.0, "thickness_v": 10.0, "fillet_radius": 8.0},
            {"thickness_h": 20.0, "thickness_v": 20.0, "fillet_radius": 25.0},
        ],
        ids=["minimum", "default", "maximum"],
    )
    def test_selectors_survive_the_whole_design_range(self, tmp_path, design):
        """The topological-naming problem, tested where it actually bites."""
        artifact = provider().build(SPACE.decode(design), tmp_path)
        region_map = resolve_regions(
            self.regions(), signatures_for(artifact), scale_length=artifact.bbox.diagonal
        )
        assert len(region_map["mounting_face"].face_tags) == 1
        assert len(region_map["bolt_holes"].face_tags) == 2

    def test_mounting_face_area_is_independent_of_thickness(self, tmp_path):
        """It is always the 60x90 wall minus two bolt holes, whatever the plates do."""
        expected = 60.0 * 90.0 - 2 * 3.14159265 * 4.5**2
        for index, design in enumerate(
            [
                {"thickness_h": 5.0, "thickness_v": 5.0, "fillet_radius": 3.0},
                {"thickness_h": 20.0, "thickness_v": 20.0, "fillet_radius": 25.0},
            ]
        ):
            artifact = provider().build(SPACE.decode(design), tmp_path / str(index))
            region_map = resolve_regions(
                self.regions(),
                signatures_for(artifact),
                scale_length=artifact.bbox.diagonal,
            )
            assert region_map["mounting_face"].total_area == pytest.approx(expected, rel=1e-3)

    def test_a_selector_matching_nothing_is_an_explicit_error(self, tmp_path):
        artifact = provider().build(SPACE.defaults(), tmp_path)
        impossible = (
            SemanticRegion(
                "nowhere",
                RegionSelector(surface_type=SurfaceType.PLANE, min_area=1.0e9),
            ),
        )
        with pytest.raises(EvaluationFailure) as info:
            resolve_regions(
                impossible, signatures_for(artifact), scale_length=artifact.bbox.diagonal
            )
        assert info.value.code is FailureCode.REGION_NOT_FOUND
        assert info.value.outcome is Outcome.ERROR

    def test_an_ambiguous_selector_stops_rather_than_guessing(self, tmp_path):
        """Two equally good candidates must never be silently resolved."""
        artifact = provider().build(SPACE.defaults(), tmp_path)
        ambiguous = (
            SemanticRegion(
                "some_bolt_hole",
                # Both bores match identically and nothing distinguishes them.
                RegionSelector(
                    surface_type=SurfaceType.CYLINDER,
                    min_radius=4.0,
                    max_radius=5.0,
                    mode=SelectionMode.SINGLE,
                ),
            ),
        )
        with pytest.raises(EvaluationFailure) as info:
            resolve_regions(
                ambiguous, signatures_for(artifact), scale_length=artifact.bbox.diagonal
            )
        assert info.value.code is FailureCode.REGION_AMBIGUOUS
        assert "disambiguate" in info.value.message

    def test_position_disambiguates_what_shape_alone_cannot(self, tmp_path):
        from openoptima.domain.regions import BoundingBox

        artifact = provider().build(SPACE.defaults(), tmp_path)
        specific = (
            SemanticRegion(
                "lower_bolt_hole",
                RegionSelector(
                    surface_type=SurfaceType.CYLINDER,
                    min_radius=4.0,
                    max_radius=5.0,
                    within_box=BoundingBox(-1.0, -1.0, 50.0, 46.0, 30.0, 91.0),
                ),
            ),
        )
        region_map = resolve_regions(
            specific, signatures_for(artifact), scale_length=artifact.bbox.diagonal
        )
        assert len(region_map["lower_bolt_hole"].face_tags) == 1
