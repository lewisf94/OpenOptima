"""A closed-form stand-in solver.

**This is not a finite element solver and its numbers are not engineering
results.**  It exists so that the pipeline, scheduler, cache, DOE and optimiser
can be exercised in CI on machines with no CalculiX installed, and so that
optimisation logic can be tested against a landscape whose right answer is
known analytically.

It idealises whatever it is given as a cantilever beam derived from the mesh
bounding box.  The response is therefore smooth and monotonic in the section
dimensions, which is what makes it useful for testing an optimiser — and
completely unsuitable for anything else.  Every result it produces carries a
warning saying so, and ``openoptima doctor`` reports it as a non-physical
backend.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..domain.model import AnalysisModel, LoadKind, SolverSpecification
from ..meshing.base import MeshData
from .base import AnalysisResults, LoadCaseFields

_WARNING = (
    "ANALYTIC BACKEND: results come from a beam idealisation, not from a finite "
    "element solve. For testing the pipeline only — never for engineering decisions."
)


class AnalyticSolver:
    name = "analytic"

    def __init__(self, specification: SolverSpecification | None = None) -> None:
        self.specification = specification or SolverSpecification(name="analytic")

    def available(self) -> tuple[bool, str]:
        return True, "built-in analytic backend (not a finite element solver)"

    def solve(
        self,
        model: AnalysisModel,
        mesh: MeshData,
        working_directory: Path,
    ) -> AnalysisResults:
        working_directory.mkdir(parents=True, exist_ok=True)
        coordinates = mesh.coordinates
        lower = coordinates.min(axis=0)
        upper = coordinates.max(axis=0)
        extents = np.maximum(upper - lower, 1e-9)

        # Longest direction is the "span"; the other two form the section.
        span_axis = int(np.argmax(extents))
        section_axes = [axis for axis in range(3) if axis != span_axis]
        span = float(extents[span_axis])
        breadth = float(extents[section_axes[0]])
        depth = float(extents[section_axes[1]])

        material = model.material
        second_moment = max(breadth * depth**3 / 12.0, 1e-12)
        area = max(breadth * depth, 1e-12)

        fields: list[LoadCaseFields] = []
        for load_case in model.load_cases:
            resultant = np.zeros(3)
            for load in load_case.loads:
                if load.kind is LoadKind.FORCE:
                    resultant += np.array(load.vector, dtype=float)
                elif load.kind is LoadKind.PRESSURE:
                    resultant += np.array([0.0, 0.0, -load.magnitude * breadth * span])
                elif load.kind is LoadKind.ACCELERATION:
                    volume = float(mesh.coordinates.shape[0])  # proxy, testing only
                    resultant += np.array(load.vector, dtype=float) * material.density * volume

            magnitude = float(np.linalg.norm(resultant))
            tip_deflection = magnitude * span**3 / (3.0 * material.elastic_modulus * second_moment)
            bending_stress = magnitude * span * (depth / 2.0) / second_moment
            axial_stress = magnitude / area

            # Distribute along the span so the fields look plausible and the
            # maxima land where a real solve would put them.
            normalised = (coordinates[:, span_axis] - lower[span_axis]) / span
            displacement = np.zeros_like(coordinates)
            direction = resultant / magnitude if magnitude > 0 else np.zeros(3)
            shape = normalised**2 * (3.0 - normalised) / 2.0
            displacement += np.outer(shape, direction) * tip_deflection

            von_mises = np.abs(bending_stress) * (1.0 - normalised) + abs(axial_stress)

            # Euler buckling of the equivalent cantilever, so the analytic
            # backend can exercise buckling constraints in CI. Same caveat as
            # everything else here: not an engineering result.
            buckling: tuple[float, ...] = ()
            if model.buckling.enabled and magnitude > 0:
                critical = math.pi**2 * material.elastic_modulus * second_moment / (2.0 * span) ** 2
                buckling = tuple(
                    critical / magnitude * (2 * n - 1) ** 2
                    for n in range(1, model.buckling.modes + 1)
                )

            fields.append(
                LoadCaseFields(
                    load_case_id=load_case.id,
                    buckling_factors=buckling,
                    node_tags=mesh.node_tags,
                    displacement=displacement,
                    von_mises=von_mises,
                    reaction_force=(
                        -float(resultant[0]),
                        -float(resultant[1]),
                        -float(resultant[2]),
                    ),
                )
            )

        return AnalysisResults(
            load_cases=tuple(fields),
            solver_name=self.name,
            solver_version="analytic-1",
            warnings=(_WARNING,),
            metadata={"span_axis": span_axis, "span": span},
        )
