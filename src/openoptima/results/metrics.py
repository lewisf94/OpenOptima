"""Turning solver fields into the scalar metrics an optimiser can act on.

The stress measure deserves the attention it gets here.  Raw peak von Mises
stress is a seductive but poor optimisation target: at a re-entrant corner or a
point support the true elastic stress is unbounded, so the "peak" simply grows
with every mesh refinement.  An optimiser handed that number learns to chase
mesh artefacts rather than design quality, and a mesh-convergence study of the
winning design then contradicts the optimisation that produced it.

So the default is a high percentile of the nodal field with user-nominated
singular regions excluded, and the raw peak is *always* reported alongside so
nothing is hidden.  ``docs/engineering-assumptions.md`` states this in the terms
a reviewer needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..domain.failure_criteria import criterion_for
from ..domain.model import AnalysisModel, Material, StressEvaluation
from ..domain.orthotropic import InadmissibleMaterial, OrthotropicMaterial
from ..domain.regions import RegionMap
from ..domain.results import LoadCaseResult
from ..meshing.base import MeshData
from ..solvers.base import AnalysisResults, LoadCaseFields
from .buckling_check import check_buckling_plausibility
from .directional import DirectionalResult, directional_margin


@dataclass(frozen=True)
class StressResult:
    value: float
    raw_max: float
    measure_name: str
    excluded_nodes: int


def excluded_node_mask(
    mesh: MeshData,
    regions: RegionMap,
    evaluation: StressEvaluation,
    node_tags: np.ndarray,
) -> np.ndarray:
    """Boolean mask of nodes to drop from the stress measure.

    Nodes on an excluded region are always dropped.  If ``exclusion_radius`` is
    set, nodes within that distance of one are dropped too — a singularity
    contaminates a small neighbourhood, not just the node itself.
    """
    mask = np.zeros(len(node_tags), dtype=bool)
    if not evaluation.excluded_regions:
        return mask

    tag_to_row = {int(tag): row for row, tag in enumerate(node_tags)}
    excluded_tags: set[int] = set()
    for region_name in evaluation.excluded_regions:
        if region_name in mesh.surface_nodes:
            excluded_tags.update(int(t) for t in mesh.surface_nodes[region_name])
        elif region_name in regions:
            continue

    for tag in excluded_tags:
        row = tag_to_row.get(tag)
        if row is not None:
            mask[row] = True

    if evaluation.exclusion_radius > 0 and excluded_tags:
        seed_rows = [tag_to_row[t] for t in excluded_tags if t in tag_to_row]
        if seed_rows:
            seeds = mesh.coordinates[[mesh.index_of(int(node_tags[r])) for r in seed_rows]]
            points = mesh.coordinates[[mesh.index_of(int(tag)) for tag in node_tags]]
            radius = evaluation.exclusion_radius
            for seed in seeds:
                distances = np.linalg.norm(points - seed, axis=1)
                mask |= distances <= radius
    return mask


def evaluate_stress(
    field: np.ndarray,
    evaluation: StressEvaluation,
    mask: np.ndarray | None = None,
) -> StressResult:
    """Reduce a nodal von Mises field to one number, per the configured measure."""
    raw_max = float(np.max(field)) if field.size else 0.0

    working = field
    excluded = 0
    if mask is not None and mask.any():
        working = field[~mask]
        excluded = int(mask.sum())
    if working.size == 0:
        working = field

    measure = evaluation.measure
    if measure == "raw_max":
        value = float(np.max(working))
        name = "raw maximum"
    elif measure == "percentile":
        value = float(np.percentile(working, evaluation.percentile))
        name = f"p{evaluation.percentile:g} percentile"
    elif measure == "pnorm":
        exponent = evaluation.pnorm_exponent
        scale = float(np.max(working)) or 1.0
        normalised = working / scale
        value = float(scale * (np.sum(normalised**exponent) / working.size) ** (1.0 / exponent))
        name = f"p-norm (p={exponent:g})"
    elif measure == "region_max":
        value = float(np.max(working))
        name = "maximum outside excluded regions"
    else:  # pragma: no cover - schema restricts this
        raise ValueError(f"unknown stress measure {measure!r}")

    return StressResult(value=value, raw_max=raw_max, measure_name=name, excluded_nodes=excluded)


def mass_kg(volume_mm3: float, material: Material) -> float:
    """Mass in kg from a volume in mm^3 and a density in t/mm^3."""
    return volume_mm3 * material.density * 1.0e3


def load_case_metrics(
    fields: LoadCaseFields,
    mesh: MeshData,
    regions: RegionMap,
    evaluation: StressEvaluation,
) -> LoadCaseResult:
    magnitude = fields.displacement_magnitude
    peak_index = int(np.argmax(magnitude)) if magnitude.size else 0
    mask = excluded_node_mask(mesh, regions, evaluation, fields.node_tags)
    stress = evaluate_stress(fields.von_mises, evaluation, mask)

    critical = fields.critical_buckling_factor
    return LoadCaseResult(
        load_case_id=fields.load_case_id,
        buckling_factor=critical,
        buckling_modes=fields.buckling_factors,
        displacement_max=float(magnitude[peak_index]) if magnitude.size else 0.0,
        displacement_node=int(fields.node_tags[peak_index]) if magnitude.size else None,
        stress_measure=stress.value,
        stress_raw_max=stress.raw_max,
        stress_measure_name=stress.measure_name,
        reaction_force=fields.reaction_force,
        strain_energy=fields.strain_energy,
    )


def directional_margins(
    model: AnalysisModel,
    results: AnalysisResults,
    mesh: MeshData,
    regions: RegionMap,
    evaluation: StressEvaluation,
) -> tuple[dict[str, DirectionalResult], list[str]]:
    """Factor of safety per load case for a material with directional strength.

    Returns an empty result, and a warning saying why, in every case where the
    answer cannot be trusted: no directional strengths given, no stress tensor
    from the solver, or a criterion that cannot describe this material. None of
    those is guessed at.
    """
    material = model.material
    if not isinstance(material, OrthotropicMaterial):
        return {}, [
            "this material has no allowable stress and is not a directional "
            "material, so no factor of safety can be computed. Stresses and "
            "displacements are unaffected."
        ]
    if material.strength is None:
        return {}, [
            "this material is stronger in some directions than others, so a "
            "single factor of safety cannot be computed from von Mises stress "
            "-- it assumes equal strength in every direction. Stresses and "
            "displacements below are correct. Supply directional strengths to "
            "get a factor of safety."
        ]

    try:
        criterion = criterion_for(model.failure_criterion, material.strength)
    except InadmissibleMaterial as exc:
        # The criterion cannot bound this material. Refused rather than
        # reported, because the number it would return is an unbounded margin
        # in the unsafe direction -- see domain/failure_criteria.py.
        return {}, [f"no factor of safety: {exc}"]

    margins: dict[str, DirectionalResult] = {}
    warnings: list[str] = []
    for fields in results.load_cases:
        if fields.stress_tensor is None:
            warnings.append(
                f"load case {fields.load_case_id!r} produced no stress tensor, "
                f"so no factor of safety could be computed for it"
            )
            continue
        mask = excluded_node_mask(mesh, regions, evaluation, fields.node_tags)
        margins[fields.load_case_id] = directional_margin(
            criterion, material, fields.stress_tensor, evaluation, mask
        )
    return margins, warnings


def collect_metrics(
    results: AnalysisResults,
    model: AnalysisModel,
    mesh: MeshData,
    regions: RegionMap,
    volume_mm3: float,
) -> tuple[dict[str, float], tuple[LoadCaseResult, ...], list[str]]:
    """Produce the metric dictionary the objectives and constraints refer to.

    Multi-case metrics are **envelopes**, never averages: the governing case is
    the one that matters, and averaging a failing case against a passing one
    hides the failure.
    """
    per_case = tuple(
        load_case_metrics(fields, mesh, regions, model.stress_evaluation)
        for fields in results.load_cases
    )

    # Cross-check every buckling factor against beam theory computed from the
    # mesh itself. A buckling result that is wrong in the optimistic direction
    # is the most dangerous output this software can produce.
    warnings: list[str] = []
    if model.buckling.enabled:
        for case, fields in zip(per_case, results.load_cases, strict=False):
            warnings.extend(
                check_buckling_plausibility(
                    mesh,
                    model.material,
                    case.load_case_id,
                    case.buckling_factor,
                    float(np.linalg.norm(fields.reaction_force)),
                    model.buckling.slenderness_limit,
                )
            )

    mass = mass_kg(volume_mm3, model.material)

    # A directional material has no single allowable stress, and von Mises is
    # the wrong failure measure for one: it assumes equal strength in every
    # direction, which is exactly what such a material is not. Where
    # directional strengths were supplied, a proper criterion answers it
    # instead. Where they were not, the factor of safety is withheld rather
    # than guessed at, and the reason is said plainly.
    allowable: float | None = getattr(model.material, "allowable_stress", None)
    directional: dict[str, DirectionalResult] = {}
    if allowable is None:
        directional, directional_warnings = directional_margins(
            model, results, mesh, regions, model.stress_evaluation
        )
        warnings.extend(directional_warnings)

    # Buckling: the governing case is the one with the *lowest* factor, and a
    # case with no positive factor simply does not buckle under its load, so it
    # is excluded rather than counted as zero.
    buckling_values = [
        case.buckling_factor for case in per_case if case.buckling_factor is not None
    ]
    worst_buckling = min(buckling_values) if buckling_values else None

    # Strain energy is the work the load did on the part. Enveloped upwards
    # like everything else: the case that stores the most energy is the one
    # working the structure hardest.
    energies = [case.strain_energy for case in per_case if case.strain_energy is not None]
    worst_energy = max(energies) if energies else None

    worst_stress = max((case.stress_measure for case in per_case), default=0.0)
    worst_raw = max((case.stress_raw_max for case in per_case), default=0.0)
    worst_displacement = max((case.displacement_max for case in per_case), default=0.0)
    factor_of_safety = (
        (allowable / worst_stress if worst_stress > 0 else float("inf"))
        if allowable is not None
        else None
    )

    metrics: dict[str, float] = {
        "mass_kg": mass,
        "volume_mm3": volume_mm3,
        "displacement_max_mm": worst_displacement,
        "stress_max_mpa": worst_stress,
        "stress_raw_max_mpa": worst_raw,
        "stiffness_n_per_mm": 0.0,
    }
    if factor_of_safety is not None:
        metrics["factor_of_safety"] = factor_of_safety
    elif directional:
        # Enveloped like everything else: the governing case is the one with
        # the least margin left, never an average across cases.
        metrics["factor_of_safety"] = min(r.factor_of_safety for r in directional.values())
        metrics["failure_index"] = max(r.failure_index for r in directional.values())
        metrics["failure_index_raw_max"] = max(
            r.failure_index_raw_max for r in directional.values()
        )
        for case_id, result in directional.items():
            metrics[f"factor_of_safety.{case_id}"] = result.factor_of_safety
            metrics[f"failure_index.{case_id}"] = result.failure_index

    if worst_energy is not None:
        metrics["strain_energy_mj"] = worst_energy

    if worst_buckling is not None:
        metrics["buckling_factor"] = worst_buckling
    elif any(case.buckling_modes for case in per_case):
        # Buckling was analysed and nothing buckles: infinite margin, not zero.
        metrics["buckling_factor"] = float("inf")

    total_load = 0.0
    for case in per_case:
        total_load = max(total_load, float(np.linalg.norm(case.reaction_force)))
    if worst_displacement > 0 and total_load > 0:
        metrics["stiffness_n_per_mm"] = total_load / worst_displacement

    for case in per_case:
        metrics[f"displacement_max_mm.{case.load_case_id}"] = case.displacement_max
        metrics[f"stress_max_mpa.{case.load_case_id}"] = case.stress_measure
        if allowable is not None and case.stress_measure > 0:
            metrics[f"factor_of_safety.{case.load_case_id}"] = allowable / case.stress_measure
        if case.strain_energy is not None:
            metrics[f"strain_energy_mj.{case.load_case_id}"] = case.strain_energy
        if case.buckling_factor is not None:
            metrics[f"buckling_factor.{case.load_case_id}"] = case.buckling_factor

    return metrics, per_case, warnings
