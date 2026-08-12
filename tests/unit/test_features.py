"""Features: the data, the project-file checks, and the failure classification.

Nothing here needs gmsh. The geometry itself is exercised in
``tests/integration/test_features.py``, where the volumes a fillet and a
chamfer remove are checked against the closed-form answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from openoptima.domain.failures import INFEASIBLE_CODES, FailureCode, Outcome, outcome_for
from openoptima.domain.features import EdgeFeature, FeatureKind
from openoptima.domain.regions import (
    BoundingBox,
    FaceSignature,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.regions.matcher import resolve_region
from openoptima.schema.loader import ProjectLoadError, load_project_dict

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "l_bracket" / "project.yaml"


def example_project_dict() -> dict[str, Any]:
    """The bundled example, as a plain dict, ready to be mutated by a test."""
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


class TestTheSizeCanBeANumberOrAVariable:
    def test_a_plain_number_is_used_as_it_stands(self) -> None:
        feature = EdgeFeature("r", FeatureKind.FILLET, ("a", "b"), 4.5)
        assert feature.size_in_mm({}) == 4.5
        assert feature.driven_by is None

    def test_a_name_is_looked_up_in_the_design_vector(self) -> None:
        feature = EdgeFeature("r", FeatureKind.FILLET, ("a", "b"), "corner")
        assert feature.size_in_mm({"corner": 7.0}) == 7.0
        assert feature.driven_by == "corner"

    def test_a_name_that_is_not_there_raises_rather_than_defaulting(self) -> None:
        feature = EdgeFeature("r", FeatureKind.FILLET, ("a", "b"), "corner")
        with pytest.raises(KeyError):
            feature.size_in_mm({"something_else": 7.0})

    def test_each_kind_explains_what_its_size_means(self) -> None:
        # The word "size" alone tells a non-specialist nothing, so both kinds
        # carry a plain-English reading that error messages use.
        assert "radius" in FeatureKind.FILLET.size_meaning
        assert "flat cut" in FeatureKind.CHAMFER.size_meaning


class TestAFeatureFailureIsAlwaysADesignFailure:
    """A feature depends on the design vector, so its failures describe the design.

    The distinction is the most important one in the codebase: an
    infrastructure error tells the optimiser nothing and must never be fed
    back as a poor result, while an infeasible design is exactly what the
    optimiser needs to learn.
    """

    @pytest.mark.parametrize(
        "code",
        [
            FailureCode.FEATURE_FAILED,
            FailureCode.FEATURE_EDGES_NOT_FOUND,
            FailureCode.REGION_TOO_SMALL,
        ],
    )
    def test_classified_as_infeasible(self, code: FailureCode) -> None:
        assert code in INFEASIBLE_CODES
        assert outcome_for(code) is Outcome.INFEASIBLE

    def test_a_selector_problem_is_still_an_error(self) -> None:
        # The counterpart: these read identically at every design point, so
        # they stay infrastructure errors and never reach the optimiser.
        assert outcome_for(FailureCode.REGION_NOT_FOUND) is Outcome.ERROR
        assert outcome_for(FailureCode.REGION_AMBIGUOUS) is Outcome.ERROR


def _face(tag: int, area: float) -> FaceSignature:
    return FaceSignature(
        tag=tag,
        surface_type=SurfaceType.PLANE,
        area=area,
        centroid=(0.0, 0.0, float(tag)),
        normal=(0.0, 0.0, 1.0),
        bbox=BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
    )


class TestAFaceShrunkToASliverIsRefused:
    """The measured defect this guard exists for.

    On the example bracket, rounding the corner above the loaded end face
    left that face at 1140, 240, 6.0 and finally 0.6 mm2 as the radius grew.
    The selector resolved happily at every one of those, so a 2.5 kN load
    would have landed on a strip 1900 times smaller than the face that was
    picked, with nothing anywhere to say so.
    """

    def test_a_region_over_its_floor_resolves_normally(self) -> None:
        region = SemanticRegion(
            name="load_face",
            selector=RegionSelector(surface_type=SurfaceType.PLANE),
            min_area_mm2=300.0,
        )
        match = resolve_region(region, [_face(1, 780.0)], scale_length=100.0)
        assert match.total_area == 780.0

    def test_a_region_under_its_floor_is_an_infeasible_design(self) -> None:
        region = SemanticRegion(
            name="load_face",
            selector=RegionSelector(surface_type=SurfaceType.PLANE),
            min_area_mm2=300.0,
        )
        with pytest.raises(Exception) as caught:
            resolve_region(region, [_face(1, 0.6)], scale_length=100.0)
        failure = caught.value
        assert failure.code is FailureCode.REGION_TOO_SMALL  # type: ignore[attr-defined]
        assert failure.outcome is Outcome.INFEASIBLE  # type: ignore[attr-defined]
        # The message has to carry both numbers: a user who sees only "too
        # small" cannot tell a near miss from a collapse.
        assert "0.6" in failure.message  # type: ignore[attr-defined]
        assert "300" in failure.message  # type: ignore[attr-defined]

    def test_the_floor_covers_every_face_of_an_all_mode_region(self) -> None:
        # `mode: all` sums its faces, so two 200 mm2 faces clear a 300 mm2
        # floor together. Checking them one at a time would reject a region
        # that is perfectly big enough.
        region = SemanticRegion(
            name="both_pads",
            selector=RegionSelector(surface_type=SurfaceType.PLANE, mode=SelectionMode.ALL),
            min_area_mm2=300.0,
        )
        match = resolve_region(region, [_face(1, 200.0), _face(2, 200.0)], scale_length=100.0)
        assert match.total_area == 400.0

    def test_no_floor_means_no_check(self) -> None:
        region = SemanticRegion(
            name="anything", selector=RegionSelector(surface_type=SurfaceType.PLANE)
        )
        assert resolve_region(region, [_face(1, 1e-6)], scale_length=100.0).total_area == 1e-6


class TestTheProjectFileCatchesATypoBeforeAnythingIsBuilt:
    """A feature naming something that does not exist is caught at load time.

    Ten seconds, not two hours into a study.
    """

    def _project(self, **geometry: object) -> dict:
        raw = example_project_dict()
        raw["geometry"].update(geometry)
        return raw

    def test_a_good_feature_loads(self) -> None:
        project = load_project_dict(
            self._project(
                variables=[{"id": "corner", "minimum": 1.0, "maximum": 5.0, "default": 2.0}],
                features=[
                    {
                        "name": "round_it",
                        "kind": "fillet",
                        "between": ["mounting_face", "load_face"],
                        "size": "corner",
                    }
                ],
            )
        )
        assert len(project.geometry.features) == 1
        assert project.geometry.features[0].kind is FeatureKind.FILLET

    def test_an_unknown_region_is_rejected_and_says_what_is_available(self) -> None:
        with pytest.raises(ProjectLoadError) as caught:
            load_project_dict(
                self._project(
                    features=[
                        {
                            "name": "round_it",
                            "kind": "fillet",
                            "between": ["mounting_face", "typo_face"],
                            "size": 2.0,
                        }
                    ]
                )
            )
        assert "typo_face" in str(caught.value)
        assert "load_face" in str(caught.value)  # the list of real ones

    def test_an_unknown_size_variable_is_rejected(self) -> None:
        with pytest.raises(ProjectLoadError) as caught:
            load_project_dict(
                self._project(
                    features=[
                        {
                            "name": "round_it",
                            "kind": "fillet",
                            "between": ["mounting_face", "load_face"],
                            "size": "no_such_variable",
                        }
                    ]
                )
            )
        assert "no_such_variable" in str(caught.value)

    def test_a_fixed_parameter_counts_as_a_size_source(self) -> None:
        project = load_project_dict(
            self._project(
                parameters={"corner": 3.0},
                features=[
                    {
                        "name": "round_it",
                        "kind": "fillet",
                        "between": ["mounting_face", "load_face"],
                        "size": "corner",
                    }
                ],
            )
        )
        assert project.geometry.features[0].size == "corner"

    def test_the_same_region_on_both_sides_is_rejected(self) -> None:
        with pytest.raises(ProjectLoadError) as caught:
            load_project_dict(
                self._project(
                    features=[
                        {
                            "name": "round_it",
                            "kind": "fillet",
                            "between": ["load_face", "load_face"],
                            "size": 2.0,
                        }
                    ]
                )
            )
        assert "both sides" in str(caught.value)

    def test_a_fixed_size_of_zero_is_rejected(self) -> None:
        # Wrong at every design point, so it belongs at load time. A design
        # variable that can reach zero is refused per evaluation instead.
        with pytest.raises(ProjectLoadError) as caught:
            load_project_dict(
                self._project(
                    features=[
                        {
                            "name": "round_it",
                            "kind": "fillet",
                            "between": ["mounting_face", "load_face"],
                            "size": 0.0,
                        }
                    ]
                )
            )
        assert "greater than zero" in str(caught.value)

    def test_two_features_cannot_share_a_name(self) -> None:
        with pytest.raises(ProjectLoadError) as caught:
            load_project_dict(
                self._project(
                    features=[
                        {
                            "name": "twice",
                            "kind": "fillet",
                            "between": ["mounting_face", "load_face"],
                            "size": 2.0,
                        },
                        {
                            "name": "twice",
                            "kind": "chamfer",
                            "between": ["mounting_face", "load_face"],
                            "size": 1.0,
                        },
                    ]
                )
            )
        assert "twice" in str(caught.value)


class TestAddingAFeatureInvalidatesCachedResults:
    """A feature changes the shape, so it changes every number from it.

    Serving a result computed before the feature existed is not a cache hit.
    It is a wrong answer, delivered quickly.
    """

    def test_the_setup_digest_changes(self) -> None:
        without = load_project_dict(example_project_dict())
        raw = example_project_dict()
        raw["geometry"]["features"] = [
            {
                "name": "round_it",
                "kind": "fillet",
                "between": ["mounting_face", "load_face"],
                "size": 2.0,
            }
        ]
        assert load_project_dict(raw).setup_digest() != without.setup_digest()

    def test_changing_a_feature_size_changes_the_digest(self) -> None:
        digests = set()
        for size in (2.0, 3.0):
            raw = example_project_dict()
            raw["geometry"]["features"] = [
                {
                    "name": "round_it",
                    "kind": "fillet",
                    "between": ["mounting_face", "load_face"],
                    "size": size,
                }
            ]
            digests.add(load_project_dict(raw).setup_digest())
        assert len(digests) == 2

    def test_setting_an_area_floor_changes_the_digest(self) -> None:
        # It decides which designs are feasible, so it decides which results
        # exist at all.
        without = load_project_dict(example_project_dict())
        raw = example_project_dict()
        raw["regions"][1]["min_area_mm2"] = 50.0
        assert load_project_dict(raw).setup_digest() != without.setup_digest()
