"""Parser for CalculiX ``.dat`` files.

Two things are extracted: reaction-force totals and buckling factors. Both are
tagged with the **step number** they came from, which matters more than it
sounds.

A deck has one step per load case when buckling is off, but *two* when it is on
(a static step and a ``*BUCKLE`` step). CalculiX writes reaction totals in both,
and the value it reports in a buckling step is an artefact of the eigenvalue
solve, not the load case's real reaction. Associating results with load cases by
dividing the record count by the number of load cases therefore silently sums a
real reaction with a meaningless one — which is exactly what happened the first
time buckling was switched on, and it showed up as a 100% equilibrium error on a
model that was perfectly fine.

So results carry their step number, and the caller asks for the step it wants.

Records parsed::

                            S T E P       1

     total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

           -7.163067E-09 -1.116377E-08  1.000000E+03

         B U C K L I N G   F A C T O R   O U T P U T

     MODE NO       BUCKLING
                    FACTOR

          1   0.1440865E+02
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TOTAL_FORCE = re.compile(
    r"total force \(fx,fy,fz\) for set (?P<set>\S+) and time\s+(?P<time>\S+)",
    re.IGNORECASE,
)

_TOTAL_ENERGY = re.compile(
    r"total internal energy for set (?P<set>\S+) and time\s+(?P<time>\S+)",
    re.IGNORECASE,
)

#: CalculiX spaces out its headings: "S T E P       1".
_STEP_HEADER = re.compile(r"^\s*S\s+T\s+E\s+P\s+(?P<step>\d+)\s*$", re.IGNORECASE)
_BUCKLING_HEADER = re.compile(r"B\s*U\s*C\s*K\s*L\s*I\s*N\s*G", re.IGNORECASE)
_BUCKLING_ROW = re.compile(r"^\s*(?P<mode>\d+)\s+(?P<factor>[-+0-9.EeDd]+)\s*$")


@dataclass(frozen=True)
class ReactionTotal:
    set_name: str
    time: float
    force: tuple[float, float, float]
    #: 1-based step number this total was reported in.
    step: int = 1


@dataclass(frozen=True)
class BucklingTable:
    """Eigenvalues from one ``*BUCKLE`` step, in the order CalculiX reported them."""

    factors: tuple[float, ...]
    step: int = 1

    @property
    def critical(self) -> float | None:
        """The smallest *positive* factor — the load multiple that buckles it.

        Negative eigenvalues are not failures and must not be treated as such.
        They mean the structure would only buckle if the load were *reversed*:
        a member in tension under this load case, for instance. Reporting a
        negative number as "the buckling factor" would make a perfectly safe
        tensile design look catastrophically unstable, and an optimiser would
        then avoid an entire region of the design space for no reason.

        Returns ``None`` when nothing buckles under this load in any extracted
        mode, which the metric layer reports as an infinite margin.
        """
        positive = [f for f in self.factors if f > 0.0]
        return min(positive) if positive else None

    def rescaled(self, scale: float) -> BucklingTable:
        """Every factor multiplied by ``scale``, keeping the order and sign.

        Used to undo the deliberate load scaling on the ``*BUCKLE`` step. The
        buckling eigenvalue is exactly inversely proportional to the reference
        load, so this conversion loses nothing.
        """
        return BucklingTable(
            factors=tuple(factor * scale for factor in self.factors), step=self.step
        )

    @property
    def has_close_pair(self) -> bool:
        """Two nearly equal lowest modes.

        Means a symmetric part with two equally likely buckling directions, so
        the real margin is thinner than a single mode suggests. Worth telling
        the user about.
        """
        positive = sorted(f for f in self.factors if f > 0.0)
        if len(positive) < 2 or positive[0] <= 0:
            return False
        return (positive[1] - positive[0]) / positive[0] < 0.05


def _step_of(lines: list[str], index: int) -> int:
    """The step number in force at a given line."""
    for backwards in range(index, -1, -1):
        match = _STEP_HEADER.match(lines[backwards])
        if match:
            return int(match.group("step"))
    return 1


def parse_dat(path: str | Path) -> list[ReactionTotal]:
    """Return every reaction total in the file, tagged with its step."""
    path = Path(path)
    if not path.exists():
        return []

    totals: list[ReactionTotal] = []
    lines = path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        match = _TOTAL_FORCE.search(line)
        if not match:
            continue
        for offset in range(1, 4):
            if index + offset >= len(lines):
                break
            candidate = lines[index + offset].split()
            if len(candidate) == 3:
                try:
                    force = tuple(float(value) for value in candidate)
                except ValueError:
                    continue
                try:
                    time = float(match.group("time"))
                except ValueError:
                    time = 0.0
                totals.append(
                    ReactionTotal(
                        set_name=match.group("set"),
                        time=time,
                        force=(force[0], force[1], force[2]),  # type: ignore[index]
                        step=_step_of(lines, index),
                    )
                )
                break
    return totals


def reactions_in_step(totals: list[ReactionTotal], step: int) -> list[ReactionTotal]:
    return [total for total in totals if total.step == step]


def parse_strain_energy(path: str | Path) -> dict[int, float]:
    """Total internal energy per step, in mJ, keyed by 1-based step number.

    Keyed by step for the same reason reactions are: a ``*BUCKLE`` step emits
    its own energy total, an artefact of the eigenvalue solve rather than the
    work done by the applied load. Taking totals in file order and pairing them
    with load cases by position would attribute a buckling artefact to the next
    static case.
    """
    path = Path(path)
    if not path.exists():
        return {}

    energies: dict[int, float] = {}
    lines = path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        if not _TOTAL_ENERGY.search(line):
            continue
        for offset in range(1, 4):
            if index + offset >= len(lines):
                break
            candidate = lines[index + offset].split()
            if len(candidate) != 1:
                continue
            try:
                value = float(candidate[0])
            except ValueError:
                continue
            # First total wins for a step: CalculiX repeats the block when more
            # than one output request is active, and they carry the same value.
            energies.setdefault(_step_of(lines, index), value)
            break
    return energies


def parse_buckling(path: str | Path) -> list[BucklingTable]:
    """Extract every buckling factor table, tagged with its step."""
    path = Path(path)
    if not path.exists():
        return []

    lines = path.read_text(errors="replace").splitlines()
    tables: list[BucklingTable] = []
    factors: list[float] = []
    collecting = False
    table_step = 1

    for index, line in enumerate(lines):
        if _BUCKLING_HEADER.search(line):
            if collecting and factors:
                tables.append(BucklingTable(tuple(factors), table_step))
            collecting, factors = True, []
            table_step = _step_of(lines, index)
            continue
        if not collecting:
            continue

        match = _BUCKLING_ROW.match(line)
        if match:
            raw = match.group("factor").replace("D", "E").replace("d", "e")
            try:
                factors.append(float(raw))
            except ValueError:
                continue
        elif factors and line.strip():
            # A non-numeric line after values have started ends the table,
            # unless it is one of the heading's own continuation lines.
            if "MODE" in line.upper() or "FACTOR" in line.upper():
                continue
            tables.append(BucklingTable(tuple(factors), table_step))
            collecting, factors = False, []

    if collecting and factors:
        tables.append(BucklingTable(tuple(factors), table_step))
    return tables


def totals_by_step(totals: list[ReactionTotal]) -> dict[int, list[ReactionTotal]]:
    grouped: dict[int, list[ReactionTotal]] = {}
    for total in totals:
        grouped.setdefault(total.step, []).append(total)
    return grouped
