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


#: The eigenvalue table a ``*FREQUENCY`` step writes. Three further tables
#: follow it -- participation factors, effective modal mass, total effective
#: mass -- and every one of them also starts its rows with a mode number, so
#: the end of the table has to be detected rather than assumed.
_EIGENVALUE_HEADER = re.compile(r"E\s*I\s*G\s*E\s*N\s*V\s*A\s*L\s*U\s*E", re.IGNORECASE)
#: mode, eigenvalue (rad^2/s^2), omega (rad/s), frequency (Hz), imaginary part.
_EIGENVALUE_ROW = re.compile(
    r"^\s*(?P<mode>\d+)\s+(?P<eigenvalue>[-+0-9.EeDd]+)\s+(?P<omega>[-+0-9.EeDd]+)"
    r"\s+(?P<hertz>[-+0-9.EeDd]+)\s+(?P<imaginary>[-+0-9.EeDd]+)\s*$"
)

#: A mode at or below this fraction of the highest frequency in the same solve
#: is the part drifting or spinning freely, not vibrating.
#:
#: Measured, and the margin either side is enormous. Held properly, the test
#: cantilever's modes were 419 to 7159 Hz -- the lowest sits at 5.9e-2 of the
#: highest. Free to slide in one direction, three modes came back at 0.0, 0.0
#: and 0.0012 Hz against a first real mode of 1821 Hz -- 2.1e-7 of the highest.
#: Anything between those two numbers separates them, which is five orders of
#: magnitude of room; 1e-4 sits in the middle of it.
#:
#: Comparing against the highest frequency rather than a fixed number of hertz
#: is what makes this hold for any size of part. A long thin beam whose first
#: real mode is 18.6 Hz was measured to be just as accurate as a stubby one at
#: 419 Hz, and a fixed threshold in hertz would eventually call one of those a
#: rigid body.
RIGID_BODY_FRACTION = 1.0e-4


@dataclass(frozen=True)
class FrequencyTable:
    """Natural frequencies from one ``*FREQUENCY`` step, lowest first, in Hz."""

    hertz: tuple[float, ...]
    step: int = 1

    @property
    def rigid_body_modes(self) -> tuple[float, ...]:
        """Modes that mean the part is not held still.

        A free body has six of these, at zero hertz: three ways to drift and
        three ways to spin. CalculiX reports them without complaint and exits
        successfully, so nothing else in the chain would notice.
        """
        if not self.hertz:
            return ()
        threshold = RIGID_BODY_FRACTION * max(self.hertz)
        return tuple(value for value in self.hertz if value <= threshold)

    @property
    def flexible(self) -> tuple[float, ...]:
        """The real modes: the part bending, twisting or stretching."""
        rigid = len(self.rigid_body_modes)
        return self.hertz[rigid:] if rigid else self.hertz

    @property
    def fundamental(self) -> float | None:
        """The lowest real frequency -- usually the one that matters.

        It is the easiest to excite and the one a part most often meets first.
        ``None`` when every extracted mode was a rigid-body one, which means
        the supports do not hold the part and there is no answer to give.
        """
        return self.flexible[0] if self.flexible else None


def parse_frequencies(path: str | Path) -> list[FrequencyTable]:
    """Extract every natural frequency table, tagged with its step.

    Reads the hertz column CalculiX prints rather than deriving it from the
    eigenvalue. The two agree: on the verification cantilever the eigenvalue
    6.937157e6 rad^2/s^2 gives 419.190 Hz through ``sqrt(v) / 2 pi``, against
    the 419.1900 Hz printed alongside it. Reading the printed column keeps this
    parser free of any assumption about the unit system reaching the solver.
    """
    path = Path(path)
    if not path.exists():
        return []

    lines = path.read_text(errors="replace").splitlines()
    tables: list[FrequencyTable] = []
    hertz: list[float] = []
    collecting = False
    table_step = 1

    for index, line in enumerate(lines):
        if _EIGENVALUE_HEADER.search(line):
            # CalculiX repeats the words "EIGENVALUE NUMBER" as a heading above
            # each mode's leftover output totals. That is not another table.
            if "NUMBER" in line.upper():
                continue
            if collecting and hertz:
                tables.append(FrequencyTable(tuple(hertz), table_step))
            collecting, hertz = True, []
            table_step = _step_of(lines, index)
            continue
        if not collecting:
            continue

        match = _EIGENVALUE_ROW.match(line)
        if match:
            raw = match.group("hertz").replace("D", "E").replace("d", "e")
            try:
                hertz.append(float(raw))
            except ValueError:
                continue
        elif hertz and line.strip():
            # Values have started and this line is not one of them, so the
            # table has ended -- the participation factor table is next.
            tables.append(FrequencyTable(tuple(hertz), table_step))
            collecting, hertz = False, []

    if collecting and hertz:
        tables.append(FrequencyTable(tuple(hertz), table_step))
    return tables


def frequencies_in_step(tables: list[FrequencyTable], step: int) -> FrequencyTable | None:
    for table in tables:
        if table.step == step:
            return table
    return None


def totals_by_step(totals: list[ReactionTotal]) -> dict[int, list[ReactionTotal]]:
    grouped: dict[int, list[ReactionTotal]] = {}
    for total in totals:
        grouped.setdefault(total.step, []).append(total)
    return grouped
