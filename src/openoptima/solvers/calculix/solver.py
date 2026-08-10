"""The CalculiX structural solver adapter."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...domain.failures import EvaluationFailure, FailureCode
from ...domain.model import AnalysisModel, LoadCase, SolverSpecification
from ...meshing.base import MeshData
from ..base import AnalysisResults, LoadCaseFields, von_mises_from_tensor
from .dat import parse_buckling, parse_dat, parse_strain_energy, reactions_in_step
from .deck import BUCKLING_LOAD_SCALE, _set_name, write_deck
from .frd import blocks_named, parse_frd
from .runner import find_executable, installation_hint, run_calculix, solver_version

#: Relative tolerance on the reaction-force equilibrium check.
_EQUILIBRIUM_TOLERANCE = 0.01


def _restrained_directions(load_case: LoadCase | None) -> dict[str, set[int]]:
    """Which directions each reaction set actually restrains.

    Keyed by the node-set name the deck writer used, so a total read back from
    the solver can be matched to the boundary condition that produced it. Two
    conditions on one region contribute the union of their directions.
    """
    if load_case is None:
        return {}
    restrained: dict[str, set[int]] = {}
    for condition in load_case.boundary_conditions:
        restrained.setdefault(_set_name(condition.region), set()).update(condition.dofs)
    return restrained


class CalculiXSolver:
    name = "calculix"

    def __init__(self, specification: SolverSpecification | None = None) -> None:
        self.specification = specification or SolverSpecification()

    def available(self) -> tuple[bool, str]:
        executable = find_executable(self.specification)
        if executable is None:
            return False, installation_hint()
        return True, f"{executable} ({solver_version(executable) or 'version unknown'})"

    def solve(
        self,
        model: AnalysisModel,
        mesh: MeshData,
        working_directory: Path,
    ) -> AnalysisResults:
        working_directory.mkdir(parents=True, exist_ok=True)
        deck = write_deck(model, mesh, working_directory, job_name="job")
        run = run_calculix(self.specification, deck.job_name, working_directory)

        try:
            blocks = parse_frd(run.frd_path)
        except Exception as exc:
            raise EvaluationFailure(
                FailureCode.RESULT_PARSE_FAILED,
                f"could not read {run.frd_path.name}: {exc}",
            ) from exc

        displacement_blocks = blocks_named(blocks, "DISP")
        stress_blocks = blocks_named(blocks, "STRESS")
        expected = len(model.load_cases)
        if len(displacement_blocks) < expected:
            raise EvaluationFailure(
                FailureCode.RESULT_PARSE_FAILED,
                f"expected {expected} displacement block(s) in the FRD file, "
                f"found {len(displacement_blocks)}",
            )

        reactions = parse_dat(run.dat_path)
        strain_energies = parse_strain_energy(run.dat_path)
        buckling_tables = parse_buckling(run.dat_path) if model.buckling.enabled else []
        if model.buckling.enabled and len(buckling_tables) < expected:
            raise EvaluationFailure(
                FailureCode.RESULT_PARSE_FAILED,
                f"buckling was requested but only {len(buckling_tables)} of {expected} "
                f"buckling factor table(s) were found in {run.dat_path.name}",
            )
        warnings: list[str] = []
        fields: list[LoadCaseFields] = []

        # Deck step layout: one static step per load case, plus a *BUCKLE step
        # after each when buckling is on. Step numbers are 1-based.
        steps_per_case = 2 if model.buckling.enabled else 1

        def static_step_of(case_index: int) -> int:
            return case_index * steps_per_case + 1

        for index, load_case in enumerate(model.load_cases):
            displacement_block = displacement_blocks[index]
            node_tags = displacement_block.node_tags
            displacement = displacement_block.as_array(node_tags)[:, :3]

            if index < len(stress_blocks):
                stress = stress_blocks[index].as_array(node_tags)
                if stress.shape[1] < 6:
                    raise EvaluationFailure(
                        FailureCode.RESULT_PARSE_FAILED,
                        f"stress block for {load_case.id!r} has {stress.shape[1]} "
                        f"components, expected 6",
                    )
                tensor = stress[:, :6]
                von_mises = von_mises_from_tensor(tensor)
            else:
                tensor = None
                von_mises = np.zeros(len(node_tags))
                warnings.append(f"no stress output for load case {load_case.id!r}")

            factors: tuple[float, ...] = ()
            if index < len(buckling_tables):
                table = buckling_tables[index]
                # The buckle step was written with its loads divided by
                # BUCKLING_LOAD_SCALE, to keep the eigenvalues clear of the
                # range where CalculiX silently drops the lowest mode. Undo
                # that here, so every factor above this line is against the
                # load the user actually applied. See deck.py for why.
                table = table.rescaled(1.0 / BUCKLING_LOAD_SCALE)
                factors = table.factors
                if table.critical is None:
                    warnings.append(
                        f"load case {load_case.id!r} does not buckle under this load "
                        f"in any of the {len(factors)} extracted mode(s); the load "
                        f"would have to reverse"
                    )
                elif table.has_close_pair:
                    warnings.append(
                        f"load case {load_case.id!r} has two nearly equal buckling "
                        f"modes ({factors[0]:.4g}, {factors[1]:.4g}); the part is "
                        f"symmetric and can buckle in either of two directions"
                    )

            reaction = self._reaction_for_step(reactions, static_step_of(index), load_case)
            applied = deck.applied_force.get(load_case.id, (0.0, 0.0, 0.0))
            message = self._check_equilibrium(load_case.id, applied, reaction)
            if message:
                warnings.append(message)

            fields.append(
                LoadCaseFields(
                    load_case_id=load_case.id,
                    node_tags=node_tags,
                    displacement=displacement,
                    von_mises=von_mises,
                    stress_tensor=tensor,
                    reaction_force=reaction,
                    strain_energy=strain_energies.get(static_step_of(index)),
                    buckling_factors=factors,
                )
            )

        return AnalysisResults(
            load_cases=tuple(fields),
            solver_name=self.name,
            solver_version=run.version,
            warnings=tuple(warnings),
            metadata={
                "wall_time_s": run.wall_time,
                "deck": str(deck.main_file),
                "frd": str(run.frd_path),
            },
        )

    @staticmethod
    def _reaction_for_step(
        reactions: list, step: int, load_case: LoadCase | None = None
    ) -> tuple[float, float, float]:
        """Total support reaction for one step, assembled direction by direction.

        Selected by step number rather than by dividing the record count among
        load cases. A ``*BUCKLE`` step also emits a reaction total -- an
        artefact of the eigenvalue solve, not a real reaction -- and averaging
        or summing it with the static one produced a 100% equilibrium error on
        a perfectly sound model the first time buckling was enabled.

        **Each direction is summed only over the sets that restrain it.**
        CalculiX reports a full ``(fx, fy, fz)`` total for every set asked for,
        including the directions that set leaves free. Those free-direction
        figures are not reactions and must not be added in.

        This matters as soon as a model uses symmetry. A quarter of a
        pressurised cylinder restrains x on one cut face and y on the other. In
        that model the x-symmetry set reports its true fx of -100 001 N against
        an exact -100 000 N, and alongside it a spurious fy of +1 560 N. Adding
        every component of every set gave a total 1.7% short, and the
        equilibrium check then reported a 1.7% error -- telling the user not to
        trust an analysis that was in fact correct to one part in 100 000.
        Summing per direction gives the exact answer.
        """
        chunk = reactions_in_step(reactions, step)
        if not chunk:
            return (0.0, 0.0, 0.0)

        restrained = _restrained_directions(load_case)
        total = np.zeros(3)
        for reaction in chunk:
            force = np.array(reaction.force, dtype=float)
            dofs = restrained.get(reaction.set_name)
            if dofs is None:
                # A set with no boundary condition we can identify. Keep every
                # component, which is what this did before per-direction
                # assembly existed. Dropping it silently could hide a real
                # missing reaction, and a wrong total raises a warning a human
                # then looks at -- the safe direction for an unknown.
                total += force
                continue
            for axis in range(3):
                if axis + 1 in dofs:
                    total[axis] += force[axis]
        return (float(total[0]), float(total[1]), float(total[2]))

    @staticmethod
    def _check_equilibrium(
        load_case_id: str,
        applied: tuple[float, float, float],
        reaction: tuple[float, float, float],
    ) -> str | None:
        """Applied load and support reaction must sum to zero.

        A free global check that costs nothing and catches load-on-wrong-face,
        missing-constraint and unit mistakes that would otherwise pass silently.
        """
        applied_vector = np.array(applied, dtype=float)
        reaction_vector = np.array(reaction, dtype=float)
        scale = float(np.linalg.norm(applied_vector))
        if scale < 1e-9:
            return None
        residual = float(np.linalg.norm(applied_vector + reaction_vector))
        if residual / scale > _EQUILIBRIUM_TOLERANCE:
            return (
                f"load case {load_case_id!r} is not in equilibrium: applied "
                f"{applied_vector.round(3).tolist()} N but reaction "
                f"{reaction_vector.round(3).tolist()} N "
                f"(residual {residual / scale:.2%} of the applied load)"
            )
        return None
