"""Semantic region resolution: finding faces by what they are."""

from .matcher import compare_region_maps, resolve_region, resolve_regions
from .signature import (
    angle_between,
    face_signature,
    outward_normal_check,
    solid_face_signatures,
)

__all__ = [
    "angle_between",
    "compare_region_maps",
    "face_signature",
    "outward_normal_check",
    "resolve_region",
    "resolve_regions",
    "solid_face_signatures",
]
