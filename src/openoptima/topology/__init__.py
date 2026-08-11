"""Topology optimisation: deciding where material should exist at all.

The parametric workflow asks "I have a shape, what are the best dimensions?"
This asks "I have a lump of space, where should the material go?" Both are
legitimate and neither replaces the other.

The optimisation itself is not written here. It is ``beso``, an existing
LGPL-3.0 optimiser built around the same CalculiX this project already uses,
and it runs as a **separate program** rather than an imported library -- see
``docs/adr/0010-topology-optimisation-via-beso.md`` for why that isolation
matters. What is written here is everything around it: fetching it
reproducibly, describing the problem to it, classifying its failures, and
turning what it produces back into something this project can analyse.

**Nothing in this package produces a number anyone may act on.** A topology
run outputs a density field -- a fuzzy map of how much material belongs at
each point. It is an idea, not a part. It becomes a result only after it is
turned into a real solid and put back through the ordinary evaluation
pipeline on a body-fitted mesh.
"""

from __future__ import annotations

from .fetch import BESO_COMMIT, BesoFetchError, InstalledBeso, install, verify
from .runner import TopologyOutcome, run_topology
from .solidify import SolidResult, to_solid

__all__ = [
    "BESO_COMMIT",
    "BesoFetchError",
    "InstalledBeso",
    "SolidResult",
    "TopologyOutcome",
    "install",
    "run_topology",
    "to_solid",
    "verify",
]
