"""Unit handling.

Solvers such as CalculiX are unitless: they multiply whatever numbers you give
them.  Getting this wrong silently produces plausible-looking nonsense, so
OpenOptima never passes user numbers straight through.  Every quantity that
reaches a solver is converted into one explicit consistent system first.

The internal system is ``mm, N, MPa, t, s`` (the standard structural mm system):

===================  ==============================
Quantity             Internal unit
===================  ==============================
length               mm
force                N
stress / modulus     MPa (= N/mm^2)
mass                 t (tonne)
density              t/mm^3
time                 s
===================  ==============================

Users give input in engineering units (kg/m^3 for density, MPa for stress,
mm for length) and the conversions live here, in one place, with tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Density conversion: 1 kg/m^3 = 1e-12 t/mm^3.
KG_PER_M3_TO_T_PER_MM3: Final[float] = 1.0e-12

#: Mass conversion: 1 t = 1000 kg.
T_TO_KG: Final[float] = 1.0e3

#: Standard gravity in mm/s^2.
GRAVITY_MM_S2: Final[float] = 9806.65


@dataclass(frozen=True)
class UnitSystem:
    """A named consistent unit system.

    Only ``mm_N_MPa_t`` is supported today.  The name is written into every
    project file and result manifest so that a future second system cannot be
    mistaken for this one.
    """

    name: str
    length: str
    force: str
    stress: str
    mass: str

    def describe(self) -> str:
        return (
            f"{self.name}: length={self.length}, force={self.force}, "
            f"stress={self.stress}, mass={self.mass}"
        )


MM_N_MPA_T: Final[UnitSystem] = UnitSystem(
    name="mm_N_MPa_t",
    length="mm",
    force="N",
    stress="MPa",
    mass="t",
)

SUPPORTED_UNIT_SYSTEMS: Final[dict[str, UnitSystem]] = {MM_N_MPA_T.name: MM_N_MPA_T}


def get_unit_system(name: str) -> UnitSystem:
    try:
        return SUPPORTED_UNIT_SYSTEMS[name]
    except KeyError:
        supported = ", ".join(sorted(SUPPORTED_UNIT_SYSTEMS))
        raise ValueError(
            f"Unsupported unit system {name!r}. Supported systems: {supported}"
        ) from None


def density_kg_m3_to_internal(value: float) -> float:
    """Convert a density in kg/m^3 to t/mm^3."""
    return value * KG_PER_M3_TO_T_PER_MM3


def mass_internal_to_kg(value: float) -> float:
    """Convert a mass in tonnes to kilograms."""
    return value * T_TO_KG


def volume_mm3_to_m3(value: float) -> float:
    return value * 1.0e-9
