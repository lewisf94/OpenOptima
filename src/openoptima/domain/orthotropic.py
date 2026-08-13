"""Materials that are not equally strong in every direction.

A 3D-printed part is made from stacked layers that are fused together rather
than continuous. It is markedly weaker *between* those layers than *along*
them, commonly by 30 to 50 per cent. Treating it as one number in every
direction can report a part as safe that will peel apart along its layers
under a load it appeared to survive.

This module holds the material description and the arithmetic that turns
engineering constants into the stiffness numbers a solver wants. It is plain
Python: no solver, no numpy, so it can be reasoned about and tested without a
CAE stack.

**Two levels of description.** Full orthotropy needs nine independent
constants and three material axes. A print rarely justifies that: within a
layer it is roughly the same in every direction, and only the through-layer
direction differs. That case is *transverse isotropy*, needs five constants,
and has a useful practical consequence -- the two in-plane axes can point
anywhere in the layer plane, so only the build direction has to be specified.
:meth:`OrthotropicMaterial.transversely_isotropic` builds that case, and it is
the one most users want.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .units import density_kg_m3_to_internal

#: Below this length, a build direction is treated as having no direction at
#: all. Dimensionless, because the vector is normalised.
_SINGULAR = 1.0e-12

#: How degenerate a compliance matrix may be before it is refused, *relative*
#: to the scale of its own entries. It has to be relative: compliance scales as
#: 1/E, so a determinant of 1.7e-15 is perfectly ordinary for a metal and
#: an absolute floor near that value would reject aluminium.
_RELATIVE_SINGULAR = 1.0e-10


#: The three ways a part can be laid on a printer bed, named by the axis the
#: layers stack along. A part printed flat stacks upward, which is ``z``.
#:
#: Only these three, deliberately. They are the orientations a part is actually
#: printed in without re-fixturing it, and naming them keeps the choice
#: readable in a result table -- "printed along z" rather than a vector. A part
#: that genuinely needs an angled orientation states a fixed vector instead.
BUILD_AXES: dict[str, tuple[float, float, float]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


class InadmissibleMaterial(ValueError):
    """The constants given do not describe a physically possible material.

    Raised rather than warned about, because an inadmissible set does not make
    a solver fail. It makes it converge on nonsense, which is the failure mode
    this project exists to prevent.
    """


@dataclass(frozen=True)
class DirectionalStrength:
    """Allowable stress along each material axis, and in shear.

    Like the isotropic allowable, these are **design decisions**, not material
    properties. For a printed part the through-layer tensile value is usually
    the one that governs, and it is often far below the in-plane value.

    Tension and compression are separate because the difference matters for a
    print: layers pressed together carry compression well and pull apart much
    more easily.
    """

    tension: tuple[float, float, float]  # MPa, along axes 1, 2, 3
    compression: tuple[float, float, float]  # MPa, positive magnitudes
    shear: tuple[float, float, float]  # MPa, on planes 23, 13, 12
    basis: str = "unspecified"

    def __post_init__(self) -> None:
        for label, values in (
            ("tension", self.tension),
            ("compression", self.compression),
            ("shear", self.shear),
        ):
            for index, value in enumerate(values):
                if value <= 0:
                    raise ValueError(
                        f"{label} strength on axis {index + 1} must be positive, "
                        f"received {value:g}. Compression is given as a positive "
                        f"magnitude, not a negative stress."
                    )

    @property
    def weakest(self) -> float:
        """The lowest allowable of all, for a deliberately conservative check."""
        return min(*self.tension, *self.compression, *self.shear)


@dataclass(frozen=True)
class OrthotropicMaterial:
    """Nine elastic constants, plus the direction the layers were laid down.

    ``poisson_12`` is the contraction along axis 2 caused by a stretch along
    axis 1, and likewise for the others. These are the *major* ratios; the
    minor ones follow from symmetry and are not given separately.

    ``build_direction`` is the axis normal to the print layers -- the weak
    one. It is required: "between layers" means nothing without it.
    """

    name: str
    #: Young's modulus along each material axis, MPa.
    modulus: tuple[float, float, float]
    #: Major Poisson ratios: nu12, nu13, nu23.
    poisson: tuple[float, float, float]
    #: Shear modulus on planes 23, 13, 12, MPa.
    shear_modulus: tuple[float, float, float]
    density: float  # t/mm^3
    #: Unit vector normal to the print layers, in model coordinates.
    build_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    strength: DirectionalStrength | None = None

    def __post_init__(self) -> None:
        for index, value in enumerate(self.modulus):
            if value <= 0:
                raise InadmissibleMaterial(
                    f"{self.name!r}: modulus on axis {index + 1} must be positive"
                )
        for index, value in enumerate(self.shear_modulus):
            if value <= 0:
                raise InadmissibleMaterial(
                    f"{self.name!r}: shear modulus {index + 1} must be positive"
                )
        if self.density <= 0:
            raise InadmissibleMaterial(f"{self.name!r}: density must be positive")

        length = math.sqrt(sum(component**2 for component in self.build_direction))
        if length < _SINGULAR:
            raise InadmissibleMaterial(
                f"{self.name!r}: build direction has no length. It must be a "
                f"direction normal to the print layers, such as (0, 0, 1)."
            )

        self._check_admissible()

    # -- physical admissibility ---------------------------------------------
    def _check_admissible(self) -> None:
        """Refuse a set of constants that cannot describe a real material.

        Nine numbers chosen independently usually do not. The compliance
        matrix has to be positive definite, which is the statement that
        deforming the material always costs energy. A set that violates it
        makes the solver return an answer rather than an error -- an answer
        with negative stiffness somewhere in it.

        Checked through the leading principal minors of the compliance matrix,
        which is equivalent to positive definiteness and needs no numpy.
        """
        e1, e2, e3 = self.modulus
        nu12, nu13, nu23 = self.poisson

        # Individual bounds first, because they give a far clearer message
        # than a determinant that has come out negative.
        for label, ratio, ratio_bound in (
            ("nu12", nu12, math.sqrt(e1 / e2)),
            ("nu13", nu13, math.sqrt(e1 / e3)),
            ("nu23", nu23, math.sqrt(e2 / e3)),
        ):
            if abs(ratio) >= ratio_bound:
                raise InadmissibleMaterial(
                    f"{self.name!r}: {label} = {ratio:g} is outside the range a "
                    f"real material allows. With these moduli it must satisfy "
                    f"|{label}| < {ratio_bound:.4f}. A Poisson ratio larger than "
                    f"this means the material would release energy when "
                    f"deformed."
                )

        s11, s22, s33 = 1.0 / e1, 1.0 / e2, 1.0 / e3
        s12, s13, s23 = -nu12 / e1, -nu13 / e1, -nu23 / e2

        # These thresholds must be *relative*, not absolute. Compliance scales
        # as 1/E, so its 2x2 minor scales as 1/E^2 and its determinant as
        # 1/E^3. For a metal at 70 GPa that determinant is around 1.7e-15 for a
        # perfectly ordinary material -- an absolute floor anywhere near that
        # would reject aluminium. Comparing against the product of the diagonal
        # entries makes the test independent of the units and stiffness scale.
        minor_2 = s11 * s22 - s12 * s12
        if minor_2 <= _RELATIVE_SINGULAR * s11 * s22:
            raise InadmissibleMaterial(
                f"{self.name!r}: the constants for axes 1 and 2 are not "
                f"physically possible together."
            )

        determinant = (
            s11 * (s22 * s33 - s23 * s23)
            - s12 * (s12 * s33 - s23 * s13)
            + s13 * (s12 * s23 - s22 * s13)
        )
        if determinant <= _RELATIVE_SINGULAR * s11 * s22 * s33:
            raise InadmissibleMaterial(
                f"{self.name!r}: these nine constants do not describe a "
                f"physically possible material. The combination of moduli and "
                f"Poisson ratios would let the material release energy when "
                f"deformed. Check the Poisson ratios first -- they are the "
                f"usual cause."
            )

    # -- constructors --------------------------------------------------------
    @classmethod
    def transversely_isotropic(
        cls,
        *,
        name: str,
        in_plane_modulus_mpa: float,
        through_layer_modulus_mpa: float,
        in_plane_poisson: float,
        through_layer_poisson: float,
        through_layer_shear_mpa: float,
        density_kg_m3: float,
        build_direction: tuple[float, float, float] = (0.0, 0.0, 1.0),
        strength: DirectionalStrength | None = None,
    ) -> OrthotropicMaterial:
        """The case a printed part usually needs: five constants, not nine.

        Within a layer the material is treated as the same in every direction,
        and only the through-layer direction differs. Material axes 1 and 2 lie
        in the layer plane and axis 3 is normal to it, along the build
        direction.

        The in-plane shear modulus follows from the in-plane constants exactly,
        as it does for any isotropic plane, so it is not asked for.
        """
        in_plane_shear = in_plane_modulus_mpa / (2.0 * (1.0 + in_plane_poisson))
        return cls(
            name=name,
            modulus=(in_plane_modulus_mpa, in_plane_modulus_mpa, through_layer_modulus_mpa),
            poisson=(in_plane_poisson, through_layer_poisson, through_layer_poisson),
            shear_modulus=(through_layer_shear_mpa, through_layer_shear_mpa, in_plane_shear),
            density=density_kg_m3_to_internal(density_kg_m3),
            build_direction=build_direction,
            strength=strength,
        )

    # -- derived quantities --------------------------------------------------
    @property
    def density_kg_m3(self) -> float:
        return self.density / 1.0e-12

    @property
    def normalised_build_direction(self) -> tuple[float, float, float]:
        length = math.sqrt(sum(component**2 for component in self.build_direction))
        return tuple(component / length for component in self.build_direction)  # type: ignore[return-value]

    def stiffness_matrix(self) -> tuple[float, ...]:
        """The nine stiffness constants, in the order CalculiX expects.

        Returned as ``D1111, D1122, D2222, D1133, D2233, D3333, D1212, D1313,
        D2323``. Note that a solver wants *stiffness*, while an engineer quotes
        *compliance* -- moduli and Poisson ratios. Handing the engineering
        constants over directly would be silently wrong, so they are inverted
        here.

        The shear terms pass through unchanged; only the three normal
        directions are coupled and need the inversion.
        """
        e1, e2, e3 = self.modulus
        nu12, nu13, nu23 = self.poisson
        g23, g13, g12 = self.shear_modulus

        s11, s22, s33 = 1.0 / e1, 1.0 / e2, 1.0 / e3
        s12, s13, s23 = -nu12 / e1, -nu13 / e1, -nu23 / e2

        determinant = (
            s11 * (s22 * s33 - s23 * s23)
            - s12 * (s12 * s33 - s23 * s13)
            + s13 * (s12 * s23 - s22 * s13)
        )

        d1111 = (s22 * s33 - s23 * s23) / determinant
        d1122 = (s13 * s23 - s12 * s33) / determinant
        d2222 = (s11 * s33 - s13 * s13) / determinant
        d1133 = (s12 * s23 - s13 * s22) / determinant
        d2233 = (s12 * s13 - s11 * s23) / determinant
        d3333 = (s11 * s22 - s12 * s12) / determinant

        return (d1111, d1122, d2222, d1133, d2233, d3333, g12, g13, g23)

    def is_effectively_isotropic(self, tolerance: float = 1.0e-9) -> bool:
        """True when the nine constants collapse to one isotropic material.

        Used to keep an isotropic project producing exactly what it did
        before, so nothing already verified moves when this feature lands.
        """
        e1, e2, e3 = self.modulus
        nu12, nu13, nu23 = self.poisson
        g23, g13, g12 = self.shear_modulus
        if abs(e1 - e2) > tolerance * e1 or abs(e1 - e3) > tolerance * e1:
            return False
        if abs(nu12 - nu13) > tolerance or abs(nu12 - nu23) > tolerance:
            return False
        expected_shear = e1 / (2.0 * (1.0 + nu12))
        return all(
            abs(value - expected_shear) <= tolerance * expected_shear for value in (g23, g13, g12)
        )


def local_axes(
    build_direction: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Two axes lying in the layer plane, perpendicular to the build direction.

    For a transversely isotropic material any pair will do, since the material
    is the same in every in-plane direction. The pair is still chosen
    deterministically, so that a rebuilt project produces an identical deck and
    a cached result stays valid.

    The seed axis is whichever global axis the build direction is least aligned
    with. Picking a fixed one would give a near-zero cross product whenever the
    part happened to be built along it.
    """
    length = math.sqrt(sum(component**2 for component in build_direction))
    normal = tuple(component / length for component in build_direction)

    seed_index = min(range(3), key=lambda index: abs(normal[index]))
    seed = tuple(1.0 if index == seed_index else 0.0 for index in range(3))

    first = (
        seed[1] * normal[2] - seed[2] * normal[1],
        seed[2] * normal[0] - seed[0] * normal[2],
        seed[0] * normal[1] - seed[1] * normal[0],
    )
    magnitude = math.sqrt(sum(component**2 for component in first))
    first = (first[0] / magnitude, first[1] / magnitude, first[2] / magnitude)

    second = (
        normal[1] * first[2] - normal[2] * first[1],
        normal[2] * first[0] - normal[0] * first[2],
        normal[0] * first[1] - normal[1] * first[0],
    )
    return first, second  # type: ignore[return-value]
