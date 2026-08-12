"""Resolving semantic region selectors against a concrete solid.

The contract this module upholds:

* A selector that matches nothing is an error, not an empty set.
* A ``SINGLE`` selector whose two best candidates are within
  ``ambiguity_margin`` is an error.  Guessing here means silently moving a load
  onto the wrong face and producing a converged, plausible, wrong answer.

Both are raised as ``EvaluationFailure`` with ERROR (not INFEASIBLE) outcomes:
an ambiguous selector is a problem with the *project setup*, not evidence that
the design is bad, so it must never be fed to the optimiser as a poor score.

There is one exception, and it is deliberately the other way round.  A region
can carry ``min_area_mm2``, the smallest area the engineer says it may shrink
to.  Falling below that *is* a fact about the design -- see :func:`_checked`.
"""

from __future__ import annotations

import math

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.regions import (
    FaceSignature,
    RegionMap,
    RegionMatch,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from .signature import angle_between


def passes_filters(signature: FaceSignature, selector: RegionSelector) -> tuple[bool, str]:
    """Hard filters.  Returns (passed, reason-if-not).

    Public because ``describe.py`` builds selectors by testing candidate
    filters against this exact function. Writing a second copy of this logic
    there would be the obvious way to do it and the wrong one: the two would
    drift, and a selector generated from a click would then match a different
    set of faces from the one that resolves it. They have to be the same code.
    """
    if (
        selector.surface_type is not SurfaceType.ANY
        and signature.surface_type is not selector.surface_type
    ):
        return False, f"type {signature.surface_type.value} != {selector.surface_type.value}"

    if selector.normal is not None:
        if signature.normal is None:
            return False, "face has no well-defined normal"
        angle = angle_between(signature.normal, selector.normal)
        if angle > selector.normal_tolerance_deg:
            return False, f"normal off by {angle:.1f}deg"

    if selector.within_box is not None and not selector.within_box.contains_point(
        signature.centroid
    ):
        return False, "centroid outside within_box"

    if selector.min_area is not None and signature.area < selector.min_area:
        return False, f"area {signature.area:.3g} below min_area"
    if selector.max_area is not None and signature.area > selector.max_area:
        return False, f"area {signature.area:.3g} above max_area"

    if selector.min_radius is not None and (
        signature.radius is None or signature.radius < selector.min_radius
    ):
        return False, "radius below min_radius or undefined"
    if selector.max_radius is not None and (
        signature.radius is None or signature.radius > selector.max_radius
    ):
        return False, "radius above max_radius or undefined"

    return True, ""


def _penalty(
    signature: FaceSignature,
    selector: RegionSelector,
    *,
    scale_length: float,
    max_area: float,
) -> float:
    """Dimensionless match penalty.  Lower is better; 0.0 is a perfect match.

    Normalised by the model's size so that ``ambiguity_margin`` means the same
    thing on a 10 mm part and a 1 m one.
    """
    penalty = 0.0

    if selector.centroid_near is not None:
        distance = math.dist(signature.centroid, selector.centroid_near)
        penalty += selector.centroid_weight * (distance / max(scale_length, 1e-12))

    if selector.area_near is not None and selector.area_near > 0:
        relative = abs(signature.area - selector.area_near) / selector.area_near
        penalty += selector.area_weight * relative

    if selector.prefer_largest and max_area > 0:
        penalty += 1.0 - (signature.area / max_area)

    return penalty


def resolve_region(
    region: SemanticRegion,
    signatures: list[FaceSignature],
    *,
    scale_length: float,
) -> RegionMatch:
    """Resolve one selector, or raise with a diagnosis of why it could not."""
    selector = region.selector
    passed: list[FaceSignature] = []
    rejections: list[str] = []
    for signature in signatures:
        ok, reason = passes_filters(signature, selector)
        if ok:
            passed.append(signature)
        else:
            rejections.append(f"face {signature.tag}: {reason}")

    if not passed:
        detail = "; ".join(rejections[:8]) or "model has no faces"
        raise EvaluationFailure(
            FailureCode.REGION_NOT_FOUND,
            f"Region {region.name!r} matched no faces. Selector: {selector.describe()}. "
            f"Nearest rejections: {detail}",
            detail={"region": region.name, "rejections": rejections[:32]},
        )

    max_area = max(s.area for s in passed)
    scored = sorted(
        ((_penalty(s, selector, scale_length=scale_length, max_area=max_area), s) for s in passed),
        key=lambda pair: pair[0],
    )

    if selector.mode is SelectionMode.ALL:
        return _checked(
            region,
            RegionMatch(
                name=region.name,
                face_tags=tuple(s.tag for _, s in scored),
                signatures=tuple(s for _, s in scored),
                score=float(sum(p for p, _ in scored) / len(scored)),
                margin=float("inf"),
                candidate_count=len(scored),
            ),
        )

    best_penalty, best = scored[0]
    if len(scored) == 1:
        margin = float("inf")
    else:
        margin = float(scored[1][0] - best_penalty)
        if margin < selector.ambiguity_margin:
            runner_up = scored[1][1]
            raise EvaluationFailure(
                FailureCode.REGION_AMBIGUOUS,
                f"Region {region.name!r} is ambiguous: faces {best.tag} and "
                f"{runner_up.tag} score within {margin:.4f} "
                f"(margin required: {selector.ambiguity_margin}). "
                f"Add centroid_near, within_box or an area filter to disambiguate.",
                detail={
                    "region": region.name,
                    "candidates": [s.to_dict() for _, s in scored[:4]],
                    "margin": margin,
                },
            )

    return _checked(
        region,
        RegionMatch(
            name=region.name,
            face_tags=(best.tag,),
            signatures=(best,),
            score=float(best_penalty),
            margin=margin,
            candidate_count=len(scored),
        ),
    )


def _checked(region: SemanticRegion, match: RegionMatch) -> RegionMatch:
    """Refuse a region that has shrunk below the engineer's stated floor.

    Note the failure code: this is the one thing in this module that describes
    the *design* rather than the project setup. A selector that finds nothing,
    or cannot choose between two faces, is a mistake in the project and reads
    the same at every design point. A face that has been shrunk to a sliver is
    a fact about the particular shape in front of us, and the optimiser should
    learn it and stay away. See ``domain/regions.py::SemanticRegion`` for the
    measurement that made this necessary.
    """
    floor = region.min_area_mm2
    if floor is None or match.total_area >= floor:
        return match
    raise EvaluationFailure(
        FailureCode.REGION_TOO_SMALL,
        f"Region {region.name!r} has shrunk to {match.total_area:.4g} mm2, below the "
        f"{floor:g} mm2 you set as its smallest useful size. A load or support "
        f"spread over an area this small does not represent what you described.",
        detail={
            "region": region.name,
            "area_mm2": match.total_area,
            "min_area_mm2": floor,
            "face_tags": list(match.face_tags),
        },
    )


def resolve_regions(
    regions: tuple[SemanticRegion, ...] | list[SemanticRegion],
    signatures: list[FaceSignature],
    *,
    scale_length: float,
) -> RegionMap:
    """Resolve every region, failing on the first that cannot be resolved."""
    matches: dict[str, RegionMatch] = {}
    for region in regions:
        matches[region.name] = resolve_region(region, signatures, scale_length=scale_length)
    return RegionMap(matches=matches)


def compare_region_maps(
    expected: RegionMap, actual: RegionMap, *, area_tolerance: float = 1e-3
) -> list[str]:
    """Check two resolutions of the same regions agree.

    Used to confirm that re-resolving selectors after a BREP round-trip picked
    the same physical surfaces as the geometry stage did.  Compares measured
    area, not tags, because tags are exactly what we do not trust.
    """
    problems: list[str] = []
    for name, expected_match in expected.matches.items():
        if name not in actual:
            problems.append(f"region {name!r} disappeared after reload")
            continue
        actual_match = actual[name]
        if len(actual_match.face_tags) != len(expected_match.face_tags):
            problems.append(
                f"region {name!r} face count changed: "
                f"{len(expected_match.face_tags)} -> {len(actual_match.face_tags)}"
            )
            continue
        reference = expected_match.total_area
        if reference <= 0:
            continue
        relative = abs(actual_match.total_area - reference) / reference
        if relative > area_tolerance:
            problems.append(
                f"region {name!r} area changed by {relative:.2%} after reload "
                f"({reference:.4g} -> {actual_match.total_area:.4g} mm2)"
            )
    return problems
