"""Applying OpenOptima's own features to a shape, with OpenCASCADE.

The rules live in ``domain/features.py``; this is the part that talks to the
kernel. Read that module first -- it explains why a feature names the two
regions an edge lies between rather than the edge itself, and what that costs.

Three things here are load-bearing.

**An edge is found, never numbered.** The edges a feature applies to are the
ones the two named regions have in common, worked out fresh from the shape in
front of us on every single build. Measured on the example bracket: applying
one fillet renumbered every face of the part, so anything cached from a
previous build would be pointing at the wrong surface.

**A feature that will not build is a bad design, not a broken run.** Asking
for a 19 mm round on a 19 mm tall face is refused by the kernel, and that
refusal is information: the optimiser should learn that corner of the design
space is not available and search elsewhere. It is emphatically not an
infrastructure problem, and feeding it back as one would either waste the
whole evaluation budget on retries or -- far worse -- teach the search to
avoid a region of the design space for a reason that has nothing to do with
the designs in it. See ``domain/failures.py``.

**A feature is applied to the shape the previous feature left.** They run in
the order written. So the regions naming feature *n*'s edges must be findable
on the shape after features 1..n-1, which is not the same shape the loads and
supports are resolved against at the end. Both are checked, and a failure at
either says which: see :func:`_face_tags`. "It works on the finished part" is
not enough on its own, because a description that finds the wrong face on the
earlier shape puts the corner in the wrong place, and the part still meshes,
still solves and still returns a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.features import EdgeFeature, FeatureKind
from ..domain.regions import BoundingBox, SemanticRegion
from ..regions.matcher import resolve_region
from ..regions.signature import solid_face_signatures
from .gmsh_session import drain_log, suppress_native_output


@dataclass(frozen=True)
class FeatureRecord:
    """What one feature actually did, for the run manifest and for `doctor`."""

    name: str
    kind: str
    size_mm: float
    #: How many edges it was applied to. Worth recording: a selector that
    #: quietly starts matching a second face doubles this, and the shape
    #: changes in a way nobody asked for.
    edge_count: int
    volume_before_mm3: float
    volume_after_mm3: float

    @property
    def volume_removed_mm3(self) -> float:
        return self.volume_before_mm3 - self.volume_after_mm3

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "size_mm": self.size_mm,
            "edge_count": self.edge_count,
            "volume_before_mm3": self.volume_before_mm3,
            "volume_after_mm3": self.volume_after_mm3,
        }


def _scale_length(gmsh: Any, volume_tag: int) -> float:
    bounds = gmsh.model.occ.getBoundingBox(3, volume_tag)
    return BoundingBox(*(float(b) for b in bounds)).diagonal


def _region_by_name(regions: tuple[SemanticRegion, ...], name: str) -> SemanticRegion:
    for region in regions:
        if region.name == name:
            return region
    known = ", ".join(r.name for r in regions) or "<none>"
    raise EvaluationFailure(
        FailureCode.INTERNAL_ERROR,
        f"No region named {name!r} to attach a feature to. Defined regions: {known}.",
    )


def _face_tags(
    gmsh: Any,
    region: SemanticRegion,
    signatures: list[Any],
    *,
    scale_length: float,
    feature_name: str,
) -> tuple[int, ...]:
    try:
        return resolve_region(region, list(signatures), scale_length=scale_length).face_tags
    except EvaluationFailure as exc:
        # Same code, better message. Which shape a region failed on is the
        # whole diagnosis: failing here means it could not be found on the
        # part as it stands *before* this feature, which is a different fault
        # from failing on the finished part, and points at a different fix.
        raise EvaluationFailure(
            exc.code,
            f"Feature {feature_name!r} needs region {region.name!r} on the shape as it "
            f"stands before the feature is applied, and could not find it there. "
            f"{exc.message}",
            detail={**exc.detail, "feature": feature_name, "stage": "before feature"},
        ) from exc


def _edges_of(gmsh: Any, face_tag: int) -> set[int]:
    boundary = gmsh.model.getBoundary([(2, face_tag)], combined=False, oriented=False)
    return {abs(int(tag)) for _dim, tag in boundary}


def shared_edges(gmsh: Any, faces_a: tuple[int, ...], faces_b: tuple[int, ...]) -> list[int]:
    """Edges lying between any face of one group and any face of the other.

    Returned sorted, so the order a feature is applied in does not depend on
    the order gmsh happened to report faces. A build that is not reproducible
    from its own inputs cannot be defended -- see the topology runner for what
    that costs when it goes wrong.
    """
    edges_a: set[int] = set()
    for tag in faces_a:
        edges_a |= _edges_of(gmsh, tag)
    edges_b: set[int] = set()
    for tag in faces_b:
        edges_b |= _edges_of(gmsh, tag)
    return sorted(edges_a & edges_b)


def apply_feature(
    gmsh: Any,
    volume_tag: int,
    feature: EdgeFeature,
    regions: tuple[SemanticRegion, ...],
    values: dict[str, Any],
) -> tuple[int, FeatureRecord]:
    """Apply one feature. Returns the new volume tag and what it did."""
    try:
        size = feature.size_in_mm(values)
    except (KeyError, TypeError, ValueError) as exc:
        known = ", ".join(sorted(str(k) for k in values)) or "<none>"
        raise EvaluationFailure(
            FailureCode.INTERNAL_ERROR,
            f"Feature {feature.name!r} takes its size from {feature.size!r}, which is "
            f"neither a number nor one of the design variables or fixed parameters "
            f"in this project. Available: {known}.",
        ) from exc

    if size <= 0.0:
        raise EvaluationFailure(
            FailureCode.INVALID_DESIGN_VARIABLES,
            f"Feature {feature.name!r} was given a size of {size:g} mm. "
            f"{feature.kind.size_meaning.capitalize()} must be greater than zero.",
        )

    scale = _scale_length(gmsh, volume_tag)
    signatures = solid_face_signatures(gmsh, volume_tag)
    groups = [
        _face_tags(
            gmsh,
            _region_by_name(regions, name),
            signatures,
            scale_length=scale,
            feature_name=feature.name,
        )
        for name in feature.between
    ]
    edges = shared_edges(gmsh, groups[0], groups[1])
    if not edges:
        raise EvaluationFailure(
            FailureCode.FEATURE_EDGES_NOT_FOUND,
            f"Feature {feature.name!r} sits where {feature.between[0]} meets "
            f"{feature.between[1]}, and on this shape those two do not touch, so "
            f"there is no corner to work on. If they never touch at any size, the "
            f"project names the wrong pair of faces; `openoptima doctor` checks "
            f"that across the whole design range.",
            detail={"feature": feature.name, "between": list(feature.between)},
        )

    volume_before = float(gmsh.model.occ.getMass(3, volume_tag))
    try:
        with suppress_native_output():
            if feature.kind is FeatureKind.FILLET:
                out = gmsh.model.occ.fillet([volume_tag], edges, [size])
            else:
                # One surface per edge, to measure the cut from. With a single
                # distance the cut is symmetric, so which of the two adjacent
                # faces is named does not change the result -- but the kernel
                # still requires one, so the first region's face is used.
                surfaces = [_surface_for_edge(gmsh, edge, groups[0]) for edge in edges]
                out = gmsh.model.occ.chamfer([volume_tag], edges, surfaces, [size])
        gmsh.model.occ.synchronize()
    except Exception as exc:
        messages = drain_log(gmsh)
        raise EvaluationFailure(
            FailureCode.FEATURE_FAILED,
            f"Feature {feature.name!r} could not be built at {size:g} mm "
            f"({feature.kind.size_meaning}). The usual cause is asking for more "
            f"than the material around that corner allows.",
            detail={
                "feature": feature.name,
                "size_mm": size,
                "edges": edges,
                "gmsh_log": messages[-20:],
                "error": str(exc),
            },
        ) from exc

    solids = [tag for dim, tag in out if dim == 3]
    if len(solids) != 1:
        raise EvaluationFailure(
            FailureCode.FEATURE_FAILED,
            f"Feature {feature.name!r} at {size:g} mm left {len(solids)} separate "
            f"pieces instead of one part.",
            detail={"feature": feature.name, "size_mm": size},
        )

    new_tag = int(solids[0])
    return new_tag, FeatureRecord(
        name=feature.name,
        kind=feature.kind.value,
        size_mm=size,
        edge_count=len(edges),
        volume_before_mm3=volume_before,
        volume_after_mm3=float(gmsh.model.occ.getMass(3, new_tag)),
    )


def _surface_for_edge(gmsh: Any, edge: int, candidates: tuple[int, ...]) -> int:
    """One adjacent face of *edge*, preferring one the caller already named."""
    for tag in candidates:
        if edge in _edges_of(gmsh, tag):
            return int(tag)
    adjacent = gmsh.model.getAdjacencies(1, edge)[0]
    if len(adjacent) == 0:  # pragma: no cover - an edge with no face
        raise EvaluationFailure(FailureCode.INTERNAL_ERROR, f"edge {edge} belongs to no face")
    return int(adjacent[0])


def apply_features(
    gmsh: Any,
    volume_tag: int,
    features: tuple[EdgeFeature, ...],
    regions: tuple[SemanticRegion, ...],
    values: dict[str, Any],
) -> tuple[int, list[FeatureRecord]]:
    """Apply every feature in order. Returns the final volume tag and a record."""
    records: list[FeatureRecord] = []
    for feature in features:
        volume_tag, record = apply_feature(gmsh, volume_tag, feature, regions, values)
        records.append(record)
    return volume_tag, records
