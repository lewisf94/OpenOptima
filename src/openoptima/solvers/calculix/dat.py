"""Parser for CalculiX ``.dat`` files.

Only reaction-force totals are extracted.  They matter out of proportion to
their size: comparing the reaction against the applied load is a free, global
check that the model was assembled and constrained as intended.  A deck with a
load applied to the wrong face, a missing constraint or a units slip usually
fails this check immediately, long before anyone notices a stress plot looks odd.

The relevant record looks like::

    total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

           -7.163067E-09 -1.116377E-08  1.000000E+03
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TOTAL_FORCE = re.compile(
    r"total force \(fx,fy,fz\) for set (?P<set>\S+) and time\s+(?P<time>\S+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReactionTotal:
    set_name: str
    time: float
    force: tuple[float, float, float]


def parse_dat(path: str | Path) -> list[ReactionTotal]:
    """Return every reaction total in the file, in order."""
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
                    )
                )
                break
    return totals


def totals_by_step(totals: list[ReactionTotal]) -> dict[float, list[ReactionTotal]]:
    grouped: dict[float, list[ReactionTotal]] = {}
    for total in totals:
        grouped.setdefault(total.time, []).append(total)
    return grouped
