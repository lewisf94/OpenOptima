"""Settings for topology optimisation, and the rules they must obey.

Topology optimisation answers a different question from the rest of this
project. The parametric workflow asks "I have a shape, what are the best
dimensions?" This asks "I have a lump of space to work in, where should the
material go?" Both are legitimate and neither replaces the other.

Plain domain data and rules, like everything else in this package: no numpy, no
solver, no mesher. What actually runs the optimisation lives in ``topology/``
and is a separate program -- see
``docs/adr/0010-topology-optimisation-via-beso.md``.

**Nothing here produces a number anyone may act on.** The output of a topology
run is a density field: a fuzzy map of how much material belongs at each point
in space. It is not a shape, and it has not been analysed. It becomes a result
only after it is turned into a real solid and put back through the ordinary
evaluation pipeline on a body-fitted mesh. :class:`TopologySettings` therefore
describes a *proposal stage*, not an answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TopologySettings:
    """What the user is asking a topology run to do.

    These are the settings every density-based method needs, whichever program
    does the work.
    """

    #: How much of the starting lump may remain, as a fraction. 0.3 means "use
    #: at most 30% of the material you started with".
    volume_fraction: float = 0.3

    #: The thinnest feature the result is allowed to contain, in millimetres.
    #: Not a nicety: without it the optimiser is free to invent members thinner
    #: than anything that could be machined, cast or printed, and the answer is
    #: then optimal and unmakeable. It also stops the classic checkerboard,
    #: where material alternates element by element and the stiffness is an
    #: artefact of the mesh rather than a real structure.
    minimum_feature_size_mm: float = 2.0

    #: How far the smoothing filter reaches, in millimetres. Left unset it is
    #: derived from the minimum feature size, which is the relationship that
    #: makes that size actually hold.
    filter_radius_mm: float | None = None

    #: Give up after this many rounds. A run that has not settled by then has
    #: not converged, and saying so is better than returning the last shape it
    #: happened to be holding.
    maximum_iterations: int = 100

    #: How aggressively material is removed each round, as a fraction of what
    #: remains. Small is slow but stable; large can delete a load path before
    #: the optimiser discovers it needed it.
    evolution_rate: float = 0.02

    def __post_init__(self) -> None:
        if not 0.0 < self.volume_fraction < 1.0:
            raise ValueError(
                f"volume_fraction must be between 0 and 1 (exclusive), got "
                f"{self.volume_fraction}. It is the share of the starting "
                f"material allowed to remain."
            )
        if self.minimum_feature_size_mm <= 0:
            raise ValueError(
                f"minimum_feature_size_mm must be positive, got "
                f"{self.minimum_feature_size_mm}. Without it the result can "
                f"contain members too thin to manufacture."
            )
        if self.filter_radius_mm is not None and self.filter_radius_mm <= 0:
            raise ValueError(f"filter_radius_mm must be positive, got {self.filter_radius_mm}")
        if self.maximum_iterations < 1:
            raise ValueError(
                f"maximum_iterations must be at least 1, got {self.maximum_iterations}"
            )
        if not 0.0 < self.evolution_rate < 1.0:
            raise ValueError(
                f"evolution_rate must be between 0 and 1 (exclusive), got {self.evolution_rate}"
            )

    @property
    def effective_filter_radius_mm(self) -> float:
        """The smoothing radius actually used.

        Defaults to half the minimum feature size, because that is what makes
        the feature size mean anything: the filter blurs material over its own
        radius, so a feature narrower than twice that radius cannot survive it.
        Setting the radius by hand overrides this, and
        :meth:`feature_size_warnings` says so if the two then disagree.
        """
        if self.filter_radius_mm is not None:
            return self.filter_radius_mm
        return 0.5 * self.minimum_feature_size_mm

    def required_element_size_mm(self, elements_across_a_feature: int = 3) -> float:
        """How fine the mesh has to be for the minimum feature size to hold.

        A feature cannot be represented by fewer elements than it takes to draw
        one. Three across is the usual minimum; below that the "feature" is a
        line of single elements and its stiffness is a mesh artefact.
        """
        if elements_across_a_feature < 1:
            raise ValueError("elements_across_a_feature must be at least 1")
        return self.minimum_feature_size_mm / elements_across_a_feature

    def feature_size_warnings(self, element_size_mm: float | None = None) -> list[str]:
        """Everything about these settings that should be said out loud.

        Returned rather than raised: none of these makes a run impossible, and
        the engineer is the one who decides whether to accept them. Silence
        would be the wrong answer though -- each one changes how much the
        result can be trusted.
        """
        warnings: list[str] = []

        radius = self.effective_filter_radius_mm
        if self.filter_radius_mm is not None and radius < 0.5 * self.minimum_feature_size_mm:
            warnings.append(
                f"The smoothing radius ({radius:g} mm) is too small to hold the "
                f"minimum feature size of {self.minimum_feature_size_mm:g} mm. "
                f"Features thinner than that can survive, so the result may not "
                f"be manufacturable. Use at least "
                f"{0.5 * self.minimum_feature_size_mm:g} mm."
            )

        if element_size_mm is not None:
            needed = self.required_element_size_mm()
            if element_size_mm > needed:
                warnings.append(
                    f"The mesh is too coarse for the minimum feature size. "
                    f"Elements are {element_size_mm:g} mm and a "
                    f"{self.minimum_feature_size_mm:g} mm feature needs about "
                    f"{needed:g} mm to be drawn at all. Thin members in the "
                    f"result will be a single element wide, and their stiffness "
                    f"will be an artefact of the mesh rather than real."
                )

        if self.volume_fraction < 0.1:
            warnings.append(
                f"Keeping only {self.volume_fraction:.0%} of the material is very "
                f"aggressive. The optimiser may remove a load path before it "
                f"discovers it was needed, and the result can be a disconnected "
                f"set of islands rather than a part."
            )

        if self.evolution_rate > 0.1:
            warnings.append(
                f"Removing {self.evolution_rate:.0%} of the remaining material "
                f"each round is fast but unstable. A load path can be deleted "
                f"before its value shows up."
            )

        return warnings

    def digest_fields(self) -> dict[str, float | int]:
        """Everything here that can change a result.

        Feeds the evaluation hash. Anything added to this class that affects
        the answer must be added here in the same change, or a stale result
        will be served as a fresh one.
        """
        return {
            "volume_fraction": self.volume_fraction,
            "minimum_feature_size_mm": self.minimum_feature_size_mm,
            "filter_radius_mm": self.effective_filter_radius_mm,
            "maximum_iterations": self.maximum_iterations,
            "evolution_rate": self.evolution_rate,
        }


def is_converged(history: list[float], tolerance: float = 0.001, window: int = 5) -> bool:
    """Has the objective stopped moving?

    Compares the last few rounds against the few before them, which is the
    standard test for these methods: a single quiet round proves nothing,
    because the material removal happens in steps and one step can happen to be
    small.

    Returns False on too little history. Not converged is the safe answer when
    there is nothing to judge -- claiming convergence early would stop a run
    that had not settled and report the shape it was holding at the time.
    """
    if window < 1:
        raise ValueError("window must be at least 1")
    if len(history) < 2 * window:
        return False
    recent = history[-window:]
    previous = history[-2 * window : -window]
    if any(not math.isfinite(value) for value in recent + previous):
        return False
    scale = sum(abs(value) for value in recent) / window
    if scale == 0.0:
        return all(value == 0.0 for value in previous)
    change = abs(sum(recent) - sum(previous)) / window
    return change / scale <= tolerance
