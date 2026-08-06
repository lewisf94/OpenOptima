"""Parser for CalculiX ``.frd`` result files.

The format is fixed-width FORTRAN output.  Record types used here:

===========  ==================================================================
``  -4``     start of a result block, e.g. ``DISP`` or ``STRESS``
``  -5``     one component declaration within the current block
``  -1``     one data record: node id then values
``  -2``     continuation of the previous data record (blocks with >6 columns)
``  -3``     end of block
===========  ==================================================================

Values are 12 characters wide.  We slice by column rather than splitting on
whitespace because CalculiX happily writes ``-1.23456E+05-9.87654E+04`` with no
separator when a value fills its field, and a naive ``split()`` silently merges
two numbers into one.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_NODE_ID_START = 3
_NODE_ID_END = 13
_VALUE_WIDTH = 12


@dataclass
class ResultBlock:
    name: str
    components: list[str] = field(default_factory=list)
    values: dict[int, list[float]] = field(default_factory=dict)

    @property
    def node_tags(self) -> np.ndarray:
        return np.array(sorted(self.values), dtype=np.int64)

    def as_array(self, node_tags: Iterable[int] | None = None) -> np.ndarray:
        tags = list(node_tags) if node_tags is not None else sorted(self.values)
        width = max((len(v) for v in self.values.values()), default=0)
        out = np.zeros((len(tags), width), dtype=np.float64)
        for row, tag in enumerate(tags):
            entry = self.values.get(int(tag))
            if entry is not None:
                out[row, : len(entry)] = entry
        return out


def _parse_values(line: str, start: int) -> list[float]:
    values: list[float] = []
    position = start
    while position + _VALUE_WIDTH <= len(line):
        chunk = line[position : position + _VALUE_WIDTH].strip()
        position += _VALUE_WIDTH
        if not chunk:
            continue
        try:
            values.append(float(chunk))
        except ValueError:
            break
    # Trailing partial field (CalculiX sometimes trims the final blanks).
    remainder = line[position:].strip()
    if remainder:
        with contextlib.suppress(ValueError):
            values.append(float(remainder))
    return values


def parse_frd(path: str | Path) -> list[ResultBlock]:
    """Read every result block from an FRD file, in file order.

    Blocks are returned in order, so the *n*-th ``DISP`` block belongs to the
    *n*-th solved step.
    """
    path = Path(path)
    blocks: list[ResultBlock] = []
    current: ResultBlock | None = None
    last_node: int | None = None

    with path.open("r", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith(" -4"):
                name = line[5:].split()[0] if len(line) > 5 else "UNKNOWN"
                current = ResultBlock(name=name)
                blocks.append(current)
                last_node = None
            elif line.startswith(" -5") and current is not None:
                parts = line[5:].split()
                if parts:
                    current.components.append(parts[0])
            elif line.startswith(" -1") and current is not None:
                try:
                    node = int(line[_NODE_ID_START:_NODE_ID_END])
                except ValueError:
                    continue
                current.values[node] = _parse_values(line, _NODE_ID_END)
                last_node = node
            elif line.startswith(" -2") and current is not None and last_node is not None:
                current.values[last_node].extend(_parse_values(line, _NODE_ID_END))
            elif line.startswith(" -3"):
                current = None
                last_node = None
    return blocks


def blocks_named(blocks: list[ResultBlock], name: str) -> list[ResultBlock]:
    return [block for block in blocks if block.name.upper() == name.upper()]
