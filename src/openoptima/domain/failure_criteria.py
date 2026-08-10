"""Deciding whether a directional material has failed.

Von Mises stress answers one question: has this material yielded? It assumes
the material is equally strong in every direction, so one number is enough.
A printed part is not like that. It is weaker between its layers than along
them, so the *same* stress is safe in one direction and not in another. Von
Mises cannot express that, and using it on a printed part is unsafe in the
direction that matters -- it will call a part sound that is about to peel
apart along its layers.

This module holds two criteria that can express it. Both take the six stress
components **in the material's own axes** and answer two things: how close to
failure the material is, and by how much the load could be multiplied before
it fails.

**Which to use.** :class:`Hoffman` is the better criterion where it applies:
it accounts for combined stresses acting together, and for a material being
stronger in compression than in tension. But it cannot describe a material
whose through-layer strength is less than half its in-plane strength -- see
:class:`Hoffman` for exactly why, because the failure is silent and
optimistic. :class:`MaximumStress` always applies and never has that problem,
at the cost of ignoring how combined stresses interact.

Plain Python: no numpy, no solver. The field-wide version, which needs both,
lives in ``results/directional.py``.

Stress components are always ordered ``(s11, s22, s33, s12, s23, s31)``, the
same order used everywhere else in this project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .orthotropic import DirectionalStrength, InadmissibleMaterial

#: How far inside the admissible region a Hoffman envelope must sit before it
#: is accepted. Relative to the scale of the coefficients themselves, so it
#: does not depend on whether strengths are quoted in MPa or Pa. Exactly zero
#: is refused as well as negative: at zero the envelope is open in one
#: direction, which is the unsafe case this test exists to catch.
_ADMISSIBLE_MARGIN = 1.0e-9

#: A stress state below this, relative to the material's own strength, is
#: treated as no stress at all, and reported as an unbounded margin rather
#: than dividing by it.
_NEGLIGIBLE = 1.0e-30


@dataclass(frozen=True)
class CriterionResult:
    """How close to failure, and by how much the load may be multiplied."""

    #: 1.0 means exactly at the failure surface. Below 1 is safe.
    failure_index: float
    #: The multiple of the *whole applied load* at which failure is reached.
    #: This is the number an engineer calls a factor of safety.
    factor_of_safety: float


class FailureCriterion:
    """Common interface, so a caller need not know which criterion is in use."""

    name: str

    def evaluate(self, stress: tuple[float, float, float, float, float, float]) -> CriterionResult:
        raise NotImplementedError


@dataclass(frozen=True)
class MaximumStress(FailureCriterion):
    """Each stress component checked against its own allowable, separately.

    The simplest directional criterion, and the only one that always works.
    Failure is predicted when any single component reaches its own limit.

    **What it ignores.** Stresses acting together. A material carrying 90 per
    cent of its limit along the layers *and* 90 per cent across them at the
    same time is in more trouble than either number suggests, and this
    criterion will not say so. Where :class:`Hoffman` can be used, it is the
    better answer.

    **What it gets right.** It is always well posed, and its meaning never
    becomes ambiguous, whatever the strengths are.
    """

    strength: DirectionalStrength
    name: str = "maximum stress"

    def evaluate(self, stress: tuple[float, float, float, float, float, float]) -> CriterionResult:
        s11, s22, s33, s12, s23, s31 = stress
        tension = self.strength.tension
        compression = self.strength.compression
        # Shear is stored on planes 23, 13, 12; the tuple here is 12, 23, 31.
        shear = (self.strength.shear[2], self.strength.shear[0], self.strength.shear[1])

        ratios = []
        for value, index in ((s11, 0), (s22, 1), (s33, 2)):
            limit = tension[index] if value >= 0.0 else compression[index]
            ratios.append(abs(value) / limit)
        for value, limit in zip((s12, s23, s31), shear, strict=True):
            # Shear has no sign preference: a material is as weak in one
            # shear direction as the other.
            ratios.append(abs(value) / limit)

        worst = max(ratios)
        if worst <= _NEGLIGIBLE:
            return CriterionResult(failure_index=0.0, factor_of_safety=math.inf)
        # Every term is linear in stress, so the margin is the plain reciprocal.
        return CriterionResult(failure_index=worst, factor_of_safety=1.0 / worst)


@dataclass(frozen=True)
class Hoffman(FailureCriterion):
    """Hoffman's criterion: combined stresses, and unequal tension/compression.

    Hoffman (1967) extends the older Hill criterion by adding terms that are
    linear in stress. Those terms are what let it describe a material that is
    stronger in compression than in tension -- exactly the case for a printed
    part, whose layers press together well and pull apart easily.

    Build one with :meth:`from_strength`, which computes the nine coefficients
    and checks the result describes a closed failure surface.

    **The limitation that matters, stated plainly.** The criterion cannot
    describe a material whose through-layer strength is less than **half** its
    in-plane strength. Past that point the failure surface stops being closed:
    the criterion predicts that one particular combination of stresses -- pull
    along the layers while pressing across them -- never causes failure at all,
    at any magnitude. That is not a conservative error. It is an infinitely
    optimistic one, and nothing in the arithmetic reveals it.

    So :meth:`from_strength` refuses such a material rather than returning a
    criterion that would silently report an unbounded margin. Use
    :class:`MaximumStress` for those.

    Reference: O. Hoffman, "The Brittle Strength of Orthotropic Materials",
    *Journal of Composite Materials* 1(2), 1967, pp. 200-206.
    """

    #: Coefficients on the squared stress differences: (s22-s33)^2, (s33-s11)^2,
    #: (s11-s22)^2.
    quadratic: tuple[float, float, float]
    #: Coefficients on s11, s22, s33. Zero when tension and compression match.
    linear: tuple[float, float, float]
    #: Coefficients on s12^2, s23^2, s31^2.
    shear: tuple[float, float, float]
    name: str = "Hoffman"

    @classmethod
    def from_strength(cls, strength: DirectionalStrength) -> Hoffman:
        """Build the criterion from directional strengths, or refuse.

        Raises :class:`InadmissibleMaterial` when the strengths produce a
        failure surface that is not closed. The message says which material
        property is responsible and what to do instead.
        """
        xt, yt, zt = strength.tension
        xc, yc, zc = strength.compression
        s23, s13, s12 = strength.shear

        # Products of the tensile and compressive strength on each axis. These
        # appear only as products, which is what lets one criterion cover both
        # signs of stress.
        px, py, pz = xt * xc, yt * yc, zt * zc

        c1 = 0.5 * (1.0 / py + 1.0 / pz - 1.0 / px)
        c2 = 0.5 * (1.0 / pz + 1.0 / px - 1.0 / py)
        c3 = 0.5 * (1.0 / px + 1.0 / py - 1.0 / pz)

        cls._check_closed(c1, c2, c3, strength)

        return cls(
            quadratic=(c1, c2, c3),
            linear=(1.0 / xt - 1.0 / xc, 1.0 / yt - 1.0 / yc, 1.0 / zt - 1.0 / zc),
            shear=(1.0 / (s12 * s12), 1.0 / (s23 * s23), 1.0 / (s13 * s13)),
        )

    @staticmethod
    def _check_closed(c1: float, c2: float, c3: float, strength: DirectionalStrength) -> None:
        """Refuse a set of strengths whose failure surface is not closed.

        The squared terms form a 3x3 matrix that is always singular in the
        hydrostatic direction -- by design, since squeezing a material equally
        from all sides does not break it in this model. The surface is closed
        in every *other* direction only when the remaining conditions below
        hold. If they do not, some stress state costs nothing at all in the
        criterion, and the computed margin against it is infinite.

        The conditions are the principal minors of that matrix. Only the second
        one binds in practice, but all are checked, because a strength set that
        breaks the first is even further outside the model.
        """
        for pair, label in (((c1, c2), "1 and 2"), ((c2, c3), "2 and 3"), ((c3, c1), "3 and 1")):
            if pair[0] + pair[1] <= _ADMISSIBLE_MARGIN * (abs(pair[0]) + abs(pair[1])):
                raise InadmissibleMaterial(
                    f"Hoffman's criterion cannot describe these strengths: the "
                    f"failure surface is open when axes {label} are combined. "
                    f"Use the maximum stress criterion instead."
                )

        minor = c1 * c2 + c2 * c3 + c3 * c1
        scale = abs(c1 * c2) + abs(c2 * c3) + abs(c3 * c1)
        if minor <= _ADMISSIBLE_MARGIN * scale:
            weakest = min(strength.tension)
            strongest = max(strength.tension)
            raise InadmissibleMaterial(
                f"Hoffman's criterion cannot describe this material. Its "
                f"weakest direction ({weakest:g} MPa) is less than half its "
                f"strongest ({strongest:g} MPa), and past that point the "
                f"criterion stops predicting failure at all for one "
                f"combination of stresses -- pulling along one axis while "
                f"pressing across another. It would report an unlimited "
                f"margin there, which is wrong in the unsafe direction. "
                f"Use the maximum stress criterion for this material."
            )

    def evaluate(self, stress: tuple[float, float, float, float, float, float]) -> CriterionResult:
        """Failure index and factor of safety at one point.

        **The factor of safety is not one divided by the failure index, and it
        is not the square root of it either.** The criterion mixes squared and
        plain stress terms, so doubling the load does not double the index. The
        multiplier that reaches failure is found by solving the quadratic
        properly, below. Treating the index as if it scaled simply would be
        wrong by a factor that grows with how unequal tension and compression
        are -- and printed parts are exactly where they are most unequal.
        """
        quadratic, linear = self._parts(stress)
        index = quadratic + linear

        if quadratic <= _NEGLIGIBLE:
            # No squared contribution: either there is no stress at all, or it
            # lies along the one direction the criterion does not resist.
            if linear <= _NEGLIGIBLE:
                return CriterionResult(failure_index=index, factor_of_safety=math.inf)
            return CriterionResult(failure_index=index, factor_of_safety=1.0 / linear)

        # Failure when quadratic*R^2 + linear*R = 1. The positive root is the
        # multiplier that first reaches the failure surface.
        discriminant = linear * linear + 4.0 * quadratic
        factor = (-linear + math.sqrt(discriminant)) / (2.0 * quadratic)
        return CriterionResult(failure_index=index, factor_of_safety=factor)

    def _parts(
        self, stress: tuple[float, float, float, float, float, float]
    ) -> tuple[float, float]:
        """Split the index into the part that scales with the square of the
        load and the part that scales directly with it."""
        s11, s22, s33, s12, s23, s31 = stress
        c1, c2, c3 = self.quadratic
        h12, h23, h31 = self.shear

        quadratic = (
            c1 * (s22 - s33) ** 2
            + c2 * (s33 - s11) ** 2
            + c3 * (s11 - s22) ** 2
            + h12 * s12 * s12
            + h23 * s23 * s23
            + h31 * s31 * s31
        )
        l1, l2, l3 = self.linear
        return quadratic, l1 * s11 + l2 * s22 + l3 * s33


def criterion_for(name: str, strength: DirectionalStrength) -> FailureCriterion:
    """Build a criterion by name, as the project file spells it."""
    if name == "hoffman":
        return Hoffman.from_strength(strength)
    if name == "max_stress":
        return MaximumStress(strength=strength)
    raise ValueError(f"unknown failure criterion {name!r}; expected 'hoffman' or 'max_stress'")
