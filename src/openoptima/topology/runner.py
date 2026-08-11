"""Running ``beso`` as a separate program, and classifying what comes back.

**beso is never imported.** It owns a solver loop and OpenOptima owns a solver
loop; merging the two would let beso reach around the evaluation cache, the
failure classification, the region resolution and the buckling load-scaling
fix, which between them are most of what makes a number from this project
trustworthy. As a separate process it is a tool we hand a deck to and collect
files from -- the same relationship this project already has with CalculiX
itself. See ``docs/adr/0010-topology-optimisation-via-beso.md``.

Two consequences of beso's own design shape everything here:

- It reads its settings from ``beso_conf.py`` **in its own directory**, so
  every run gets a private copy of beso's scripts. Two runs sharing one copy
  would overwrite each other's settings, and the second would silently solve
  the first one's problem.
- It starts the solver in a way that breaks on a path containing a space, so
  the working directory is chosen by :mod:`openoptima.topology.workspace`
  rather than being wherever the project happens to live.

**Nothing here returns a result anyone may act on.** What comes back is a
density field: a fuzzy map of how much material belongs where. It is an idea,
not a part, and it has not been analysed on a body-fitted mesh.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.failures import EvaluationFailure, FailureCode
from ..domain.model import Material
from ..domain.topology import TopologySettings
from .config import render_config
from .fetch import InstalledBeso
from .workspace import workspace

#: How long one topology run may take before it is killed. A run is a few
#: hundred solver calls, so this is generously large -- but not unbounded,
#: because a wedged run would otherwise hold a study open indefinitely.
DEFAULT_TIMEOUT_SECONDS = 4 * 60 * 60

#: Solve on one core, so the same input gives the same shape every time.
#:
#: **This is not a performance oversight.** Measured: the identical problem run
#: twice on all cores produced two different shapes; run twice on one core it
#: produced bit-identical output. CalculiX's threaded solve differs in the last
#: bits of its arithmetic depending on how the work lands on threads, and a
#: topology run turns those bits into decisions -- an element sitting on the
#: boundary between keep and remove goes one way in one run and the other way
#: in the next. Over seventy rounds that compounds into a genuinely different
#: part.
#:
#: A design that cannot be reproduced from its own inputs cannot be defended,
#: cached, or verified, so speed loses this argument. Passing more cores is
#: allowed and warned about.
REPRODUCIBLE_CPU_CORES = 1


@dataclass(frozen=True)
class TopologyOutcome:
    """What one topology run produced.

    ``result_meshes`` are beso's own output meshes, in CalculiX ``.inp`` form.
    They are **proposals**. Turning one into a solid that can be re-analysed is
    :mod:`openoptima.topology.solidify`, and only after that does anything here
    become a number.
    """

    result_meshes: tuple[Path, ...]
    log: str
    iterations: int
    output_directory: Path
    beso_commit: str
    #: Share of the starting material left at the end, from beso's own record.
    #: None when it kept no usable log.
    mass_fraction: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def solid_mesh(self) -> Path:
        """The shape that matters: the highest material state.

        State 0 is the void material, so the last file is the solid one.
        """
        if not self.result_meshes:  # pragma: no cover - runner refuses this earlier
            raise ValueError("this run produced no meshes")
        return self.result_meshes[-1]


def _interpreter() -> str:
    """The Python that will run beso.

    ``sys.executable`` is right when OpenOptima is installed normally. In a
    frozen build it is the application itself rather than an interpreter, which
    would re-launch the app instead of running beso -- so that case is refused
    with an explanation rather than doing something surprising.
    """
    if getattr(sys, "frozen", False):
        raise EvaluationFailure(
            FailureCode.SOLVER_NOT_FOUND,
            "topology optimisation needs a Python interpreter to run the "
            "optimiser, and the installed build of OpenOptima does not include "
            "one. Run topology optimisation from a normal Python installation "
            "for now.",
        )
    return sys.executable


def _classify(returncode: int, log: str, meshes: tuple[Path, ...]) -> None:
    """Turn beso's exit into either nothing, or the right kind of failure.

    The distinction this project cares about most is a bad *design* against a
    broken *run*. A topology run that removes too much material and falls apart
    is telling us something about the problem. A missing solver is telling us
    nothing, and must never be fed back as though it were a result.
    """
    lowered = log.lower()

    if "cannot open inp file" in lowered or "exit status 201" in lowered:
        raise EvaluationFailure(
            FailureCode.SOLVER_CRASH,
            "the topology optimiser could not open the analysis file it was "
            "given. This is a setup problem, not a bad design.",
        )
    if "invalid path_calculix" in lowered or "exit status 1" in lowered:
        raise EvaluationFailure(
            FailureCode.SOLVER_NOT_FOUND,
            "the topology optimiser could not start the CalculiX solver. Check "
            "the solver path in OpenOptima's settings.",
        )
    if "filter failed due to division by 0" in lowered:
        # The smoothing filter averages each element with its neighbours inside
        # the filter radius. When the radius is smaller than the gap between
        # element centres, an element has no neighbours at all and there is
        # nothing to average. Measured: a 2 mm mesh with a 2 mm radius fails.
        # Which way to fix it is an engineering choice -- a finer mesh keeps the
        # feature size, a larger feature size keeps the mesh -- so it is not
        # decided here.
        raise EvaluationFailure(
            FailureCode.MESH_QUALITY_FAILED,
            "the mesh is too coarse for the smallest feature asked for. The "
            "smoothing filter had no neighbouring elements to work with, so the "
            "run stopped. Either refine the mesh, or raise the minimum feature "
            "size. As a rule the elements need to be smaller than the filter "
            "radius, which is half the minimum feature size by default.",
        )
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        missing = "a Python package the optimiser needs is not installed"
        if "matplotlib" in lowered:
            missing = (
                "the optimiser imports matplotlib and it is not installed. "
                "Install it with 'pip install matplotlib'."
            )
        raise EvaluationFailure(FailureCode.SOLVER_NOT_FOUND, missing)

    if returncode != 0:
        raise EvaluationFailure(
            FailureCode.SOLVER_CRASH,
            f"the topology optimiser stopped with exit code {returncode}. Its log "
            f"is in the run directory.",
        )
    if not meshes:
        raise EvaluationFailure(
            FailureCode.RESULT_PARSE_FAILED,
            "the topology optimiser finished without producing a result mesh. "
            "Nothing can be reported from this run.",
        )


def _collect_meshes(directory: Path) -> tuple[Path, ...]:
    """beso's resulting meshes from its final round, in state order.

    beso writes ``file<NNN>_state<S>.inp``, where ``NNN`` is the round number
    and ``S`` is the material state. State 0 is the void material and the
    highest state is the solid one, so the last entry returned is the shape
    that matters.

    Only the final round is returned. Earlier rounds are present when
    ``save_iteration_results`` is on, and treating one of those as the answer
    would report a shape the optimiser had already moved on from.
    """
    by_round: dict[str, list[Path]] = {}
    for path in directory.glob("file*_state*.inp"):
        prefix = path.name.split("_state", 1)[0]
        by_round.setdefault(prefix, []).append(path)
    if not by_round:
        return ()
    final = max(by_round)  # zero-padded, so lexical order is numeric order
    return tuple(sorted(by_round[final]))


def _mass_history(directory: Path, deck_name: str) -> list[float]:
    """The mass beso recorded after each round, as a fraction of where it began.

    beso keeps its own table in ``<deck>.log``, one row per round, starting with
    the round number and then the mass. Reading its own record is better than
    counting elements in the result: element counts are only a fair measure of
    material when every element is the same size.
    """
    log = directory / f"{Path(deck_name).stem}.log"
    if not log.is_file():
        return []

    masses: list[float] = []
    for line in log.read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            round_number = int(parts[0])
            mass = float(parts[1])
        except ValueError:
            continue
        if round_number == len(masses):
            masses.append(mass)
    if not masses:
        return []
    start = masses[0]
    return [m / start for m in masses] if start else []


def _check_target_reached(
    masses: list[float], settings: TopologySettings, rounds: int
) -> str | None:
    """Say so when the run stopped before it got where it was going.

    This is the quiet failure worth guarding. A run that hits its round limit
    still writes a shape, and that shape looks exactly like a finished result.
    It is not one -- it is a block the optimiser was part way through eroding,
    and reporting it as an optimised design would be wrong in a way nothing in
    the file reveals.
    """
    if not masses:
        return None
    achieved = masses[-1]
    target = settings.volume_fraction
    # A little over target is ordinary: material comes away in steps, so the
    # last step usually overshoots or undershoots slightly.
    if achieved <= target * 1.05:
        return None

    needed = ""
    if settings.evolution_rate > 0:
        # beso's own estimate, from its "auto" limit: the net removal each
        # round is the removal rate less the addition rate, which is half of it.
        net = settings.evolution_rate * 0.5
        needed = (
            f" It looks like about {int((1.0 - target) / net) + 25} rounds are "
            f"needed at this removal rate."
        )
    return (
        f"This run stopped after {rounds} rounds with {achieved:.0%} of the "
        f"material still there, but it was asked to reach {target:.0%}. The shape "
        f"it produced is part way through being optimised, not a finished "
        f"result, and must not be treated as one. Raise the round limit and run "
        f"it again.{needed}"
    )


def _count_rounds(meshes: tuple[Path, ...]) -> int:
    """How many rounds the optimiser took, from the file it finished on.

    Read from the file name rather than counted in the log: beso writes
    ``file<NNN>_state<S>.inp`` and ``NNN`` is the round it stopped at, which is
    the number regardless of how its log happens to be worded.
    """
    for path in meshes:
        prefix = path.name.split("_state", 1)[0]
        digits = prefix[len("file") :]
        if digits.isdigit():
            return int(digits)
    return 0


def run_topology(
    *,
    settings: TopologySettings,
    material: Material,
    deck: Path,
    beso: InstalledBeso,
    solver_executable: Path | str,
    output_directory: Path,
    objective: str = "stiffness",
    allowable_stress_mpa: float | None = None,
    cpu_cores: int = REPRODUCIBLE_CPU_CORES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    keep_on_failure: bool = True,
) -> TopologyOutcome:
    """Run one topology optimisation and collect what it produced.

    ``deck`` is an ordinary CalculiX linear static analysis file -- exactly
    what ``solvers/calculix/deck.py`` already writes. It is copied into the
    working directory; the original is never touched.

    beso runs in a private, space-free directory that is deleted afterwards, so
    everything worth keeping is copied into ``output_directory`` first. That
    directory is the caller's and may live anywhere, including a path with
    spaces in it -- beso has finished by then.
    """
    warnings = list(settings.feature_size_warnings())
    if cpu_cores != REPRODUCIBLE_CPU_CORES:
        warnings.append(
            f"This run used {cpu_cores if cpu_cores else 'all'} processor cores "
            f"rather than one, so it may not be reproducible. Running the same "
            f"problem again can produce a different shape, because the solver's "
            f"arithmetic differs slightly between threads and this optimiser "
            f"turns those differences into keep-or-remove decisions. Use one "
            f"core for anything that has to be defended or repeated."
        )
    interpreter = _interpreter()
    output_directory.mkdir(parents=True, exist_ok=True)
    keep = False

    with workspace(keep=False) as directory:
        try:
            # A private copy of beso, because it reads its settings from its own
            # directory. Sharing one copy between runs would mean the second run
            # silently solving the first one's problem.
            scripts = directory / "beso"
            shutil.copytree(beso.directory, scripts)

            deck_name = deck.name
            shutil.copyfile(deck, directory / deck_name)

            (scripts / "beso_conf.py").write_text(
                render_config(
                    settings=settings,
                    material=material,
                    solver_executable=solver_executable,
                    working_directory=directory,
                    deck_name=deck_name,
                    objective=objective,
                    allowable_stress_mpa=allowable_stress_mpa,
                    cpu_cores=cpu_cores,
                ),
                encoding="utf-8",
            )

            # An argument list, never a shell string: a path may contain a
            # space or a semicolon. This is the project's own rule and it holds
            # here even though the tool being started breaks it internally.
            command = [interpreter, str(scripts / "beso_main.py"), deck_name]
            try:
                completed = subprocess.run(  # noqa: S603 - fixed argument list
                    command,
                    cwd=str(directory),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise EvaluationFailure(
                    FailureCode.SOLVER_TIMEOUT,
                    f"the topology optimiser did not finish within "
                    f"{timeout_seconds / 3600:.1f} hours and was stopped.",
                ) from exc

            log = (completed.stdout or "") + (completed.stderr or "")
            (output_directory / "beso-run.log").write_text(log, encoding="utf-8")

            produced = _collect_meshes(directory)
            _classify(completed.returncode, log, produced)

            rounds = _count_rounds(produced)
            masses = _mass_history(directory, deck_name)
            unfinished = _check_target_reached(masses, settings, rounds)
            if unfinished:
                # An ERROR, not a warning. A partially eroded block is
                # indistinguishable from a finished result once it is in a file,
                # and a warning attached to a number is no protection when
                # something downstream reads the number and not the warning.
                raise EvaluationFailure(FailureCode.RESULT_UNRELIABLE, unfinished)

            beso_log = directory / f"{Path(deck_name).stem}.log"
            if beso_log.is_file():
                shutil.copyfile(beso_log, output_directory / beso_log.name)

            # Copy the results out before the private directory is removed.
            # Returning paths into a directory this function is about to delete
            # would hand the caller files that no longer exist.
            meshes = []
            for source in produced:
                destination = output_directory / source.name
                shutil.copyfile(source, destination)
                meshes.append(destination)

            return TopologyOutcome(
                result_meshes=tuple(meshes),
                log=log,
                iterations=rounds,
                output_directory=output_directory,
                beso_commit=beso.commit,
                mass_fraction=masses[-1] if masses else None,
                warnings=tuple(warnings),
            )
        except Exception:
            # Keep the evidence. A failed run's deck, log and partial output are
            # what anyone diagnosing it will need, and they are inside a
            # directory that would otherwise be deleted on the way out.
            keep = keep_on_failure
            raise
        finally:
            if keep:
                kept = Path(str(directory) + "-failed")
                shutil.rmtree(kept, ignore_errors=True)
                shutil.copytree(directory, kept, dirs_exist_ok=True)
