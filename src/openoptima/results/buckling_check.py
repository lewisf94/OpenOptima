"""An independent sanity check on computed buckling factors.

**Why this exists.** During development the buckling implementation was verified
against Euler's formula on a 20 mm square column at three different lengths and
matched to better than 1% every time, with the mode series in the correct
1 : 9 : 25 ratio. On an 8 mm square column of the same slenderness it disagreed
by a factor of nine, the mode series came out 1 : 1.95 : 3.20 — nothing like a
column series — and refining the mesh moved the answer around without
converging. The returned eigenvalues simply did not contain the true lowest
global mode.

That error is in the **unsafe** direction: it reports a buckling factor several
times higher than reality, so a strut that would fold up under load looks
comfortably stable. A silent optimistic answer about buckling is worse than no
answer at all, because the user will believe it.

So every buckling result is cross-checked against beam theory computed
independently from the mesh's own geometry, in exactly the spirit of the
reaction-versus-applied-load equilibrium check. When the two disagree beyond
what end conditions could possibly explain, the result is flagged rather than
quietly reported.

The estimate assumes a roughly prismatic, column-like part, which is what
buckling analysis is usually about. For a part where that assumption does not
hold the check widens its own tolerance rather than producing false alarms —
see :func:`estimate_column_properties`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.model import AnyMaterial
from ..domain.orthotropic import OrthotropicMaterial
from ..meshing.base import MeshData

#: Stiffest end condition physically achievable (both ends fully built in).
#: Nothing that is genuinely free to buckle can beat this.
_STIFFEST_EFFECTIVE_LENGTH_FACTOR = 0.5

#: Allow this much margin above the fixed-fixed bound before complaining, to
#: absorb the estimate's own error on parts that are not perfectly prismatic.
_IMPLAUSIBILITY_MARGIN = 1.5

#: Below this the part is compact and does not buckle in the Euler sense, so a
#: beam-theory comparison would be meaningless.
_COLUMN_LIKE_SLENDERNESS = 30.0


@dataclass(frozen=True)
class ColumnEstimate:
    """Beam-theory properties derived from the mesh itself."""

    length: float
    area: float
    second_moment_min: float
    radius_of_gyration: float
    prismatic_quality: float
    """How prismatic the part is, 0 to 1. Cross-section area consistency along
    the dominant axis; 1.0 is a perfect prism."""

    def slenderness(self, effective_length_factor: float = 2.0) -> float:
        if self.radius_of_gyration <= 0:
            return float("inf")
        return effective_length_factor * self.length / self.radius_of_gyration

    def critical_load(self, modulus: float, effective_length_factor: float) -> float:
        effective = effective_length_factor * self.length
        if effective <= 0:
            return float("inf")
        return math.pi**2 * modulus * self.second_moment_min / effective**2


def estimate_column_properties(mesh: MeshData, slices: int = 12) -> ColumnEstimate | None:
    """Derive length, area and second moment from the mesh.

    Uses tetrahedron volumes and centroids: for a prismatic bar along *x*,
    ``∫z² dV = L ∫z² dA = L·I_y``, so dividing the solid's second moment of
    volume by the length recovers the section's second moment of area.

    Returns ``None`` when the mesh is unusable (degenerate or empty).
    """
    if mesh.element_count == 0:
        return None

    corners = mesh.connectivity[:, :4]
    try:
        points = np.stack([mesh.coordinates_of(corners[:, i]) for i in range(4)], axis=1)
    except KeyError:  # pragma: no cover - defensive
        return None

    a, b, c, d = points[:, 0], points[:, 1], points[:, 2], points[:, 3]
    volumes = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    total_volume = float(volumes.sum())
    if total_volume <= 0:
        return None

    centroids = points.mean(axis=1)
    centre = (centroids * volumes[:, None]).sum(axis=0) / total_volume

    extents = mesh.coordinates.max(axis=0) - mesh.coordinates.min(axis=0)
    axis = int(np.argmax(extents))
    length = float(extents[axis])
    if length <= 0:
        return None

    transverse = [i for i in range(3) if i != axis]
    # Exact second moment of a tetrahedron about a plane:
    #     integral of z^2 dV = V * (sum(z_i)^2 + sum(z_i^2)) / 20
    # over its four vertices. Using element centroids instead underestimates it
    # badly on a coarse mesh, which would inflate the apparent slenderness and
    # refuse results that are perfectly sound.
    vertex_offsets = points - centre
    second_moments = []
    for i in transverse:
        z = vertex_offsets[:, :, i]
        total = z.sum(axis=1)
        squares = (z**2).sum(axis=1)
        second_moments.append(float((volumes * (total**2 + squares) / 20.0).sum()) / length)
    second_moment_min = min(second_moments)
    area = total_volume / length
    if area <= 0 or second_moment_min <= 0:
        return None

    # How prismatic is it? Compare the volume of equal slices along the axis;
    # a true prism gives identical slices. A bracket does not, and the check
    # loosens itself accordingly rather than crying wolf.
    positions = centroids[:, axis]
    lower, upper = positions.min(), positions.max()
    edges = np.linspace(lower, upper, slices + 1)
    slice_volumes = np.array(
        [volumes[(positions >= edges[i]) & (positions < edges[i + 1])].sum() for i in range(slices)]
    )
    populated = slice_volumes[slice_volumes > 0]
    if len(populated) < 3:
        quality = 0.0
    else:
        quality = float(max(0.0, 1.0 - populated.std() / max(populated.mean(), 1e-12)))

    return ColumnEstimate(
        length=length,
        area=area,
        second_moment_min=second_moment_min,
        radius_of_gyration=math.sqrt(second_moment_min / area),
        prismatic_quality=quality,
    )


def check_buckling_plausibility(
    mesh: MeshData,
    material: AnyMaterial,
    load_case_id: str,
    buckling_factor: float | None,
    applied_load: float,
    slenderness_limit: float = 150.0,
) -> list[str]:
    """Validate a computed buckling factor, or refuse to report it.

    ``applied_load`` is the magnitude of the load the factor multiplies, in N.

    Raises :class:`EvaluationFailure` with ``RESULT_UNRELIABLE`` when the number
    cannot be trusted. That is deliberately a **failure, not a warning**: the
    error runs in the optimistic direction, and an optimiser handed an
    optimistic buckling factor will happily select the unsafe design and report
    it as the winner. A warning attached to a number the optimiser still acts on
    protects nobody.

    Returns advisory warnings for results that are trustworthy but worth a
    second look.
    """
    warnings: list[str] = []
    if buckling_factor is None or applied_load <= 0:
        return warnings

    if isinstance(material, OrthotropicMaterial):
        # The whole check below rests on one stiffness for the member. A
        # printed part has a different one along its layers and through them,
        # and which one governs buckling depends on the direction it happens
        # to fold in -- so there is no single number to compare against.
        # Refused rather than checked against a guess, for the same reason
        # every other branch here refuses: the error would run in the
        # optimistic direction, and the optimiser acts on the number.
        raise EvaluationFailure(
            FailureCode.RESULT_UNRELIABLE,
            f"load case {load_case_id!r}: {material.name!r} is printed, so it is "
            f"stiffer along its layers than through them. The check that decides "
            f"whether a buckling factor can be trusted assumes one stiffness in "
            f"every direction, so the computed factor of {buckling_factor:.3g} "
            f"cannot be checked and is not reported. Turn buckling off for this "
            f"material.",
            detail={"load_case": load_case_id, "material": material.name},
        )

    estimate = estimate_column_properties(mesh)
    if estimate is None:
        return warnings

    slenderness = estimate.slenderness()
    if slenderness < _COLUMN_LIKE_SLENDERNESS:
        # Compact: does not buckle as a column, so beam theory says nothing.
        return warnings

    if slenderness > slenderness_limit:
        raise EvaluationFailure(
            FailureCode.RESULT_UNRELIABLE,
            f"load case {load_case_id!r}: this member is more slender than the "
            f"range this software has been verified over (ratio about "
            f"{slenderness:.0f}, limit {slenderness_limit:.0f}), so the computed "
            f"factor of {buckling_factor:.3g} is not reported. Use beam elements "
            f"or a hand Euler calculation for members this slender, or raise "
            f"buckling.slenderness_limit if you have independently verified this "
            f"range. Note this limit is deliberately conservative: it was set "
            f"against a solver defect that has since been fixed at its root (see "
            f"V9 in docs/verification-plan.md), and columns up to a ratio of 433 "
            f"now measure within 0.15% of Euler. Widening it is an engineering "
            f"decision, so the default has been left where it was.",
            detail={
                "load_case": load_case_id,
                "slenderness": slenderness,
                "limit": slenderness_limit,
                "rejected_factor": buckling_factor,
            },
        )

    critical = buckling_factor * applied_load
    stiffest = estimate.critical_load(material.elastic_modulus, _STIFFEST_EFFECTIVE_LENGTH_FACTOR)
    bound = stiffest * _IMPLAUSIBILITY_MARGIN / max(estimate.prismatic_quality, 0.25)

    if critical > bound:
        raise EvaluationFailure(
            FailureCode.RESULT_UNRELIABLE,
            f"load case {load_case_id!r}: the computed buckling load "
            f"({critical:,.0f} N) exceeds what beam theory permits even for a "
            f"fully built-in column of this section ({stiffest:,.0f} N). The "
            f"eigenvalue solve has missed the lowest mode, so the factor of "
            f"{buckling_factor:.3g} is optimistic and is not reported.",
            detail={
                "load_case": load_case_id,
                "computed_critical_load": critical,
                "beam_theory_bound": stiffest,
            },
        )

    if slenderness > 0.7 * slenderness_limit:
        warnings.append(
            f"load case {load_case_id!r}: slenderness is about {slenderness:.0f}, "
            f"approaching the {slenderness_limit:.0f} limit where solid-element "
            f"buckling stops being reliable. The factor of {buckling_factor:.3g} "
            f"is worth cross-checking by hand."
        )
    return warnings
