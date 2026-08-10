"""Applying a directional failure criterion across a whole mesh.

Two things have to happen before a criterion from ``domain/failure_criteria``
can be used on a solver result, and both are easy to get silently wrong.

**The stress has to be rotated into the material's own axes.** A criterion for
a printed part asks "how hard is this being pulled *across the layers*", and
that question cannot be answered from stress written in the model's global
axes. CalculiX writes the ``.frd`` file in the global system even when the
material has a local orientation attached, so the rotation must happen here.
(Its ``.dat`` file does the opposite and writes the local system. The two
disagree, and reading one while assuming the other is a 45-degree error that
looks entirely plausible. ``docs/engineering-assumptions.md`` records this.)

**The result has to be reduced the same way stress is.** A factor of safety
taken at the single worst node has exactly the problem that made raw peak
stress unusable: at a sharp corner it is a property of the mesh, not the part.
So the same measure configured for stress is applied to the failure index, and
the raw worst is always reported alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..domain.failure_criteria import FailureCriterion, Hoffman, MaximumStress
from ..domain.model import StressEvaluation
from ..domain.orthotropic import OrthotropicMaterial, local_axes


@dataclass(frozen=True)
class DirectionalResult:
    """The governing margin, and the raw worst, over one load case."""

    #: Failure index at the governing node. 1.0 is exactly at failure.
    failure_index: float
    #: Factor of safety at that same node.
    factor_of_safety: float
    #: The single worst node in the whole field, whatever the measure says.
    failure_index_raw_max: float
    factor_of_safety_raw_min: float
    criterion_name: str
    measure_name: str
    excluded_nodes: int


def rotation_to_material_axes(material: OrthotropicMaterial) -> np.ndarray:
    """Rows are the material's axes 1, 2 and 3, written in global coordinates.

    Axis 3 is the build direction -- normal to the print layers, the weak one.
    Axes 1 and 2 lie in the layer plane. This is the same triad the solver deck
    writes as its ``*ORIENTATION``, taken from the same function, so the
    criterion is evaluated in exactly the axes the stiffness was defined in.
    """
    first, second = local_axes(material.build_direction)
    normal = material.normalised_build_direction
    return np.array([first, second, normal], dtype=np.float64)


def to_material_axes(stress: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Rotate an (N, 6) global stress field into the material axes.

    Input and output are both ordered ``sxx, syy, szz, sxy, syz, szx``.
    """
    count = stress.shape[0]
    tensor = np.empty((count, 3, 3), dtype=np.float64)
    tensor[:, 0, 0] = stress[:, 0]
    tensor[:, 1, 1] = stress[:, 1]
    tensor[:, 2, 2] = stress[:, 2]
    tensor[:, 0, 1] = tensor[:, 1, 0] = stress[:, 3]
    tensor[:, 1, 2] = tensor[:, 2, 1] = stress[:, 4]
    tensor[:, 2, 0] = tensor[:, 0, 2] = stress[:, 5]

    rotated = np.einsum("ip,npq,jq->nij", rotation, tensor, rotation, optimize=True)

    out = np.empty_like(stress[:, :6])
    out[:, 0] = rotated[:, 0, 0]
    out[:, 1] = rotated[:, 1, 1]
    out[:, 2] = rotated[:, 2, 2]
    out[:, 3] = rotated[:, 0, 1]
    out[:, 4] = rotated[:, 1, 2]
    out[:, 5] = rotated[:, 2, 0]
    return out


def _hoffman_parts(criterion: Hoffman, stress: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised twin of ``Hoffman._parts``.

    Kept deliberately close to the scalar version, line for line, and pinned to
    it by a test that compares the two on random stress states. Two copies of
    one formula is a real risk; an unchecked copy is a worse one.
    """
    s11, s22, s33, s12, s23, s31 = (stress[:, index] for index in range(6))
    c1, c2, c3 = criterion.quadratic
    h12, h23, h31 = criterion.shear
    l1, l2, l3 = criterion.linear

    quadratic = (
        c1 * (s22 - s33) ** 2
        + c2 * (s33 - s11) ** 2
        + c3 * (s11 - s22) ** 2
        + h12 * s12**2
        + h23 * s23**2
        + h31 * s31**2
    )
    linear = l1 * s11 + l2 * s22 + l3 * s33
    return quadratic, linear


def _max_stress_index(criterion: MaximumStress, stress: np.ndarray) -> np.ndarray:
    tension = criterion.strength.tension
    compression = criterion.strength.compression
    shear = criterion.strength.shear

    ratios: list[np.ndarray] = []
    for column, index in ((0, 0), (1, 1), (2, 2)):
        value = stress[:, column]
        limit = np.where(value >= 0.0, tension[index], compression[index])
        ratios.append(np.abs(value) / limit)
    # Columns 3, 4, 5 are s12, s23, s31; strengths are stored as 23, 13, 12.
    for column, shear_limit in ((3, shear[2]), (4, shear[0]), (5, shear[1])):
        ratios.append(np.abs(stress[:, column]) / shear_limit)
    return np.maximum.reduce(ratios)


def evaluate_field(
    criterion: FailureCriterion,
    stress_material_axes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Failure index and factor of safety at every node.

    Stress must already be in the material axes.
    """
    if isinstance(criterion, Hoffman):
        quadratic, linear = _hoffman_parts(criterion, stress_material_axes)
        index = quadratic + linear
        # Solve quadratic*R^2 + linear*R = 1 for the positive root, elementwise.
        # Where there is no quadratic part the equation is linear, and where
        # there is no stress at all the margin is unbounded.
        safe_quadratic = np.where(quadratic > 0.0, quadratic, 1.0)
        factor = (-linear + np.sqrt(linear**2 + 4.0 * safe_quadratic)) / (2.0 * safe_quadratic)
        linear_only = np.where(linear > 0.0, 1.0 / np.where(linear > 0.0, linear, 1.0), np.inf)
        factor = np.where(quadratic > 0.0, factor, linear_only)
        return index, factor

    if isinstance(criterion, MaximumStress):
        index = _max_stress_index(criterion, stress_material_axes)
        factor = np.where(index > 0.0, 1.0 / np.where(index > 0.0, index, 1.0), np.inf)
        return index, factor

    raise TypeError(f"no vectorised form for {type(criterion).__name__}")


def directional_margin(
    criterion: FailureCriterion,
    material: OrthotropicMaterial,
    stress_global: np.ndarray,
    evaluation: StressEvaluation,
    mask: np.ndarray | None = None,
) -> DirectionalResult:
    """Reduce a whole stress field to one governing margin.

    ``mask`` marks nodes to leave out, and is the same mask used for the stress
    measure -- a node sitting on a singularity is no more meaningful here than
    it is there.
    """
    material_axes = to_material_axes(stress_global, rotation_to_material_axes(material))
    index_field, factor_field = evaluate_field(criterion, material_axes)

    raw_max = float(np.max(index_field)) if index_field.size else 0.0
    raw_min_factor = float(np.min(factor_field)) if factor_field.size else float("inf")

    working_index = index_field
    working_factor = factor_field
    excluded = 0
    if mask is not None and mask.any() and not mask.all():
        working_index = index_field[~mask]
        working_factor = factor_field[~mask]
        excluded = int(mask.sum())

    if working_index.size == 0:
        return DirectionalResult(
            failure_index=0.0,
            factor_of_safety=float("inf"),
            failure_index_raw_max=raw_max,
            factor_of_safety_raw_min=raw_min_factor,
            criterion_name=criterion.name,
            measure_name="no nodes",
            excluded_nodes=excluded,
        )

    measure = evaluation.measure
    if measure == "percentile":
        # "nearest" rather than an interpolated percentile, deliberately: the
        # reported index and factor must both belong to one real node, so that
        # the pair stays consistent with each other.
        governing = int(
            np.argmin(
                np.abs(
                    working_index
                    - np.percentile(working_index, evaluation.percentile, method="nearest")
                )
            )
        )
        name = f"p{evaluation.percentile:g} percentile"
    else:
        governing = int(np.argmax(working_index))
        name = "worst node" if measure == "raw_max" else "worst node outside excluded regions"

    return DirectionalResult(
        failure_index=float(working_index[governing]),
        factor_of_safety=float(working_factor[governing]),
        failure_index_raw_max=raw_max,
        factor_of_safety_raw_min=raw_min_factor,
        criterion_name=criterion.name,
        measure_name=name,
        excluded_nodes=excluded,
    )
