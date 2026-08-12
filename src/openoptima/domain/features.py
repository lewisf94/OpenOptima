"""Features OpenOptima adds on top of a shape, and can vary.

An imported CAD file is a finished shape. The numbers whoever drew it typed
in are not in the file -- only the surfaces those numbers produced. So there
is nothing inside a STEP file to optimise. See
``geometry/step_provider.py``.

A feature is how a shape like that gets something to vary. OpenOptima adds
its own rounded or cut-back corner on top of the imported shape, and *that*
size becomes a design variable. The imported part stays exactly as drawn;
the feature is the only thing that moves.

Two kinds exist so far, both applied to edges:

``fillet``
    A rounded corner. ``size`` is the radius of the round.

``chamfer``
    A corner cut off flat, at 45 degrees. ``size`` is how far back the cut
    reaches from the original edge, the same on both sides.

## Which edge, and why it is never an edge number

The same rule that governs faces governs edges, only more so. Edge numbers
move when a shape is rebuilt, and they move when an *earlier* feature is
applied. Measured on the example bracket: adding one fillet renumbered every
single face of the part -- the top of the arm went from face 5 to face 2, the
loaded end from 7 to 5, the base from 8 to 7. Nothing kept its number.

So a feature never names an edge. It names **the two regions the edge lies
between**, and the edges are whatever the two have in common on the shape as
it stands at that moment. Regions are already resolved from what a face looks
like rather than from its number, so this adds no new naming machinery at
all -- it reuses :func:`~openoptima.regions.matcher.resolve_region` exactly
as the loads and supports do.

On the example bracket every edge is uniquely fixed by the pair of faces it
joins, which is what makes this work. Where two regions share several edges,
all of them are treated together: "the edges around both bolt holes" is a
useful thing to round off in one go.

## The order matters, and so does what a region means at each point

Features are applied one after another, and each sees the shape the one
before it left. A region named in ``between`` therefore has to be findable on
the shape *as it is just before that feature is applied* -- not on the
finished part. Regions carrying loads and supports, by contrast, are resolved
on the finished part.

Most regions have to work on both, and ``openoptima doctor`` checks exactly
that, at every stage, at the extremes of the design range. It is not
belt-and-braces: a description that finds the right face on the finished part
but the wrong one on the unfeatured shape would put the fillet on the wrong
corner, and the part would still mesh, still solve, and still return a
plausible number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class FeatureKind(str, Enum):
    #: A rounded corner. ``size`` is the radius.
    FILLET = "fillet"
    #: A corner cut off flat. ``size`` is how far back the cut reaches.
    CHAMFER = "chamfer"

    @property
    def size_meaning(self) -> str:
        """What ``size`` means for this kind, in words a non-specialist reads."""
        if self is FeatureKind.FILLET:
            return "the radius of the rounded corner"
        return "how far back the flat cut reaches from the corner"


@dataclass(frozen=True)
class EdgeFeature:
    """A rounded or cut-back corner, added where two named regions meet."""

    name: str
    kind: FeatureKind
    #: The two region names whose shared edges this feature is applied to.
    between: tuple[str, str]
    #: Millimetres, or the id of a design variable that supplies the number.
    size: float | str
    description: str = ""

    @property
    def driven_by(self) -> str | None:
        """The design variable that sets this feature's size, if any."""
        return self.size if isinstance(self.size, str) else None

    def size_in_mm(self, values: Mapping[str, Any]) -> float:
        """Resolve ``size`` against a design vector plus any fixed parameters.

        Raises :class:`KeyError` when it names something that does not exist.
        The caller turns that into a located, classified failure -- this layer
        holds no failure codes because it holds no dependencies at all.
        """
        if not isinstance(self.size, str):
            return float(self.size)
        return float(values[self.size])

    def describe(self) -> str:
        """One line, for a report or a check that a human reads."""
        shape = "rounds off" if self.kind is FeatureKind.FILLET else "cuts back"
        size = f"{self.size} mm" if self.driven_by is None else f"a size set by {self.size!r}"
        return (
            f"{self.name}: {shape} the edges where {self.between[0]} meets "
            f"{self.between[1]}, by {size}"
        )
