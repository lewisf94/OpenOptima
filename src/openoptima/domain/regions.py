"""Semantic regions: naming a face by what it *is*, not by its index.

The hardest problem in automated parametric FEA is not meshing or solving.  It
is that ``Face12`` after a fillet change may be a completely different surface
from ``Face12`` before it.  A load silently migrating to the wrong face
produces a converged, plausible, wrong answer.

OpenOptima therefore never stores raw face indices in a project.  A region is a
*selector*: a set of required properties plus a scoring rule.  It is re-resolved
against the real geometry on every single evaluation, and if two faces score
within ``ambiguity_margin`` of each other the evaluation stops rather than
guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SurfaceType(str, Enum):
    ANY = "any"
    PLANE = "plane"
    CYLINDER = "cylinder"
    SPHERE = "sphere"
    CONE = "cone"
    TORUS = "torus"
    OTHER = "other"


class SelectionMode(str, Enum):
    #: Exactly one face; ambiguity is an error.
    SINGLE = "single"
    #: Every face passing the required filters; at least one must match.
    ALL = "all"


@dataclass(frozen=True)
class BoundingBox:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    def contains_point(self, point: tuple[float, float, float], tol: float = 1e-6) -> bool:
        x, y, z = point
        return (
            self.xmin - tol <= x <= self.xmax + tol
            and self.ymin - tol <= y <= self.ymax + tol
            and self.zmin - tol <= z <= self.zmax + tol
        )

    @property
    def diagonal(self) -> float:
        dx = self.xmax - self.xmin
        dy = self.ymax - self.ymin
        dz = self.zmax - self.zmin
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.xmin, self.ymin, self.zmin, self.xmax, self.ymax, self.zmax)


@dataclass(frozen=True)
class FaceSignature:
    """Geometry-derived fingerprint of one face.

    Everything here survives a rebuild of the model; the ``tag`` does not, which
    is precisely why the rest of the fields exist.
    """

    tag: int
    surface_type: SurfaceType
    area: float
    centroid: tuple[float, float, float]
    normal: tuple[float, float, float] | None
    bbox: BoundingBox
    radius: float | None = None
    axis: tuple[float, float, float] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "surface_type": self.surface_type.value,
            "area": self.area,
            "centroid": list(self.centroid),
            "normal": list(self.normal) if self.normal else None,
            "bbox": list(self.bbox.as_tuple()),
            "radius": self.radius,
            "axis": list(self.axis) if self.axis else None,
        }


@dataclass(frozen=True)
class RegionSelector:
    """Rules that identify one or more faces.

    Required filters are hard: a face failing any of them is discarded.  The
    remaining faces are ranked by the ``*_near`` scoring terms.
    """

    surface_type: SurfaceType = SurfaceType.ANY
    #: Outward normal the face must have, within ``normal_tolerance_deg``.
    normal: tuple[float, float, float] | None = None
    normal_tolerance_deg: float = 5.0
    #: Face centroid must lie inside this box.
    within_box: BoundingBox | None = None
    min_area: float | None = None
    max_area: float | None = None
    min_radius: float | None = None
    max_radius: float | None = None
    #: Scoring: prefer faces whose centroid is near this point.
    centroid_near: tuple[float, float, float] | None = None
    centroid_weight: float = 1.0
    #: Scoring: prefer faces whose area is near this value.
    area_near: float | None = None
    area_weight: float = 1.0
    #: Scoring: prefer larger faces (useful for "the big mounting pad").
    prefer_largest: bool = False
    mode: SelectionMode = SelectionMode.SINGLE
    #: Two candidates scoring within this fraction of each other are ambiguous.
    ambiguity_margin: float = 0.05

    def describe(self) -> str:
        bits = [f"type={self.surface_type.value}"]
        if self.normal:
            bits.append(f"normal={self.normal}±{self.normal_tolerance_deg}deg")
        if self.within_box:
            bits.append("within_box")
        if self.min_area is not None:
            bits.append(f"min_area={self.min_area}")
        if self.prefer_largest:
            bits.append("prefer_largest")
        return ", ".join(bits)


@dataclass(frozen=True)
class SemanticRegion:
    """A named region of the model that boundary conditions attach to."""

    name: str
    selector: RegionSelector
    description: str = ""


@dataclass(frozen=True)
class RegionMatch:
    """Result of resolving one selector against a concrete model."""

    name: str
    face_tags: tuple[int, ...]
    signatures: tuple[FaceSignature, ...]
    score: float
    #: Gap to the next-best rejected candidate, as a fraction. ``inf`` if unique.
    margin: float
    candidate_count: int

    @property
    def total_area(self) -> float:
        return sum(s.area for s in self.signatures)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "face_tags": list(self.face_tags),
            "score": self.score,
            "margin": self.margin if self.margin != float("inf") else None,
            "candidate_count": self.candidate_count,
            "total_area": self.total_area,
            "signatures": [s.to_dict() for s in self.signatures],
        }


@dataclass(frozen=True)
class RegionMap:
    matches: dict[str, RegionMatch] = field(default_factory=dict)

    def __getitem__(self, name: str) -> RegionMatch:
        return self.matches[name]

    def __contains__(self, name: str) -> bool:
        return name in self.matches

    def face_tags(self, name: str) -> tuple[int, ...]:
        return self.matches[name].face_tags

    def to_dict(self) -> dict[str, object]:
        return {name: match.to_dict() for name, match in self.matches.items()}
