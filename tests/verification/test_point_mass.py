"""V15 — something heavy bolted to the part really is carried.

A natural frequency comes from stiffness and mass. Until this landed
OpenOptima counted only the mass of the part itself, so a motor on the end of
an arm, a camera on a mount or a battery on a tray simply was not there. That
is not a small correction on a part whose whole job is to carry something:
**measured here, 0.2 kg on the end of a 39 g cantilever took the first mode
from 418.88 Hz to 89.33 Hz, a factor of 4.69.** The error ran in the
reassuring direction, so a search told to keep a part clear of a driving rate
would have chosen one sitting on top of it.

**The case.** The same 100 x 10 x 5 mm steel cantilever as V14, so the bare
answer is already verified against beam theory, carrying 0.2 kg on its free
end. Against the closed form for a cantilever with a tip mass::

    f = (1 / 2 pi) sqrt( 3 E I / (L^3 (M + 0.2235 m_beam)) )

The 0.2235 is the standard share of a cantilever's own mass that acts at the
tip. It is small here by design -- the tip mass is five times the beam -- so
the test mostly measures the carried mass rather than that correction.

**Measured:**

    bare cantilever          418.88 Hz   (V14, verified against beam theory)
    carrying 0.2 kg           89.332 Hz  hand calculation 89.232   +0.11%
    ratio between them         4.689

The second mode is the same beam bending the stiff way. Its section is 10 x 5,
so the second moment of area differs by exactly 4 and the frequency by exactly
2. Measured 177.779 against 89.332, a ratio of 1.9901 — a shape check that
cannot be fooled by an error in the material, the units or the mass itself.

**The other half of this test is gravity, and it is the half that would have
failed silently.** A CalculiX ``MASS`` element is not in the ``Eall`` element
set, so a ``GRAV`` load applied to ``Eall`` alone does not reach it. Measured:
the same beam and mass gives a support reaction of 0.3843 N that way -- the
beam's own weight, with the carried 0.2 kg contributing nothing and nothing in
the solver output to say so. A part sized for an acceleration case would have
been sized without the thing it was carrying. See
``deck.py::_write_gravity``.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    ModalSettings,
    PointMass,
    SolverSpecification,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

pytestmark = [requires_gmsh, requires_calculix]

LENGTH, WIDTH, DEPTH = 100.0, 10.0, 5.0
MODULUS, POISSON, DENSITY = 210000.0, 0.3, 7850.0

#: The carried mass, five times the beam's own, so it dominates the answer.
TIP_MASS_KG = 0.200

#: Standard gravity in the internal mm/s^2.
GRAVITY = 9810.0

#: Same band as V14. The residual is beam-theory error, not mesh error.
FREQUENCY_TOLERANCE = 0.02

#: The share of a cantilever's own mass that acts at its tip, from any
#: standard vibration text.
BEAM_MASS_SHARE = 0.2235


def _beam_mass_t() -> float:
    return LENGTH * WIDTH * DEPTH * (DENSITY * 1.0e-12)


def _predicted_hz(tip_mass_kg: float) -> float:
    """Closed form for a cantilever with a mass on its end.

    ``I`` is taken about the easy bending axis: the section is 10 wide and 5
    deep, so it bends most readily through its depth.
    """
    second_moment = WIDTH * DEPTH**3 / 12.0
    effective = tip_mass_kg * 1.0e-3 + BEAM_MASS_SHARE * _beam_mass_t()
    return math.sqrt(3.0 * MODULUS * second_moment / (LENGTH**3 * effective)) / (2.0 * math.pi)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """One mesh, reused. Every comparison below must change only the mass."""
    directory = tmp_path_factory.mktemp("point_mass")
    provider = OccGeometryProvider(
        GeometryDefinition(
            provider="occ",
            template="cantilever_box",
            parameters={"length": LENGTH, "width": WIDTH, "height": DEPTH},
        )
    )
    space = DesignSpace(
        (DesignVariable(id="length", minimum=LENGTH, maximum=LENGTH, default=LENGTH),)
    )
    geometry = provider.build(space.defaults(), directory / "geometry")
    regions = (
        SemanticRegion(
            "fixed_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(-1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
        SemanticRegion(
            "load_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE, normal=(1.0, 0.0, 0.0), normal_tolerance_deg=2.0
            ),
        ),
    )
    mesher = GmshMesher(MeshSpecification(global_size=4.0, minimum_size=1.5, element_order=2))
    mesh, _regions = mesher.generate(geometry, regions, directory / "mesh")
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=900))
    return mesh, solver, directory


def _material() -> Material:
    return Material.from_engineering_units(
        name="Steel",
        elastic_modulus_mpa=MODULUS,
        poisson_ratio=POISSON,
        density_kg_m3=DENSITY,
        allowable_stress_mpa=250.0,
    )


def _solve(rig, point_masses, label, *, load, modal=True):
    mesh, solver, directory = rig
    model = AnalysisModel(
        name="point mass verification",
        material=_material(),
        load_cases=(
            LoadCase(
                id="held",
                boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=(1, 2, 3)),),
                loads=(load,),
            ),
        ),
        point_masses=point_masses,
        modal=ModalSettings(enabled=modal, modes=4),
    )
    return solver.solve(model, mesh, directory / label).by_id("held")


def _tip_load() -> Load:
    """A token force. The frequency step carries no load; this exists only
    because a load case must have one."""
    return Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -1.0))


def _weight_load() -> Load:
    return Load(kind=LoadKind.ACCELERATION, region=None, vector=(0.0, 0.0, -GRAVITY))


@pytest.fixture(scope="module")
def carrying(rig):
    mass = PointMass.from_engineering_units(name="tip", region="load_face", mass_kg=TIP_MASS_KG)
    return _solve(rig, (mass,), "carrying", load=_tip_load())


@pytest.fixture(scope="module")
def bare(rig):
    return _solve(rig, (), "bare", load=_tip_load())


def test_the_first_mode_matches_the_closed_form(carrying) -> None:
    measured = carrying.natural_frequencies[0]
    predicted = _predicted_hz(TIP_MASS_KG)
    assert measured == pytest.approx(predicted, rel=FREQUENCY_TOLERANCE), (
        f"first mode {measured:.3f} Hz against {predicted:.3f} Hz predicted"
    )


def test_the_carried_mass_dominates_the_answer(bare, carrying) -> None:
    """Sanity, and the number that makes the case for this feature existing.

    If a carried mass were quietly ignored these two would agree, and the
    reported frequency would be nearly five times the real one.
    """
    assert carrying.natural_frequencies[0] < bare.natural_frequencies[0]
    ratio = bare.natural_frequencies[0] / carrying.natural_frequencies[0]
    assert ratio == pytest.approx(4.69, rel=0.02)


def test_the_mode_ratio_is_the_shape_and_nothing_else(carrying) -> None:
    """The second mode bends the stiff way, and the section fixes the ratio.

    The section is 10 x 5, so the second moment of area about the two axes
    differs by exactly (10/5)^2 = 4 and the frequency by exactly 2. This holds
    whatever the material, the units or the mass are, so it catches an error
    in any of them that a single absolute frequency could not.
    """
    first, second = carrying.natural_frequencies[0], carrying.natural_frequencies[1]
    assert second / first == pytest.approx(2.0, rel=FREQUENCY_TOLERANCE)


def test_a_heavier_mass_lowers_the_frequency_by_the_predicted_amount(rig) -> None:
    """Not just "lower" — lower by the right factor.

    Frequency goes as one over the square root of mass, so quadrupling the
    carried mass must halve it, once the beam's own share is accounted for.
    A mass written into the deck at the wrong scale would still move the
    answer the right way, and only this catches that.
    """
    heavy = PointMass.from_engineering_units(
        name="tip", region="load_face", mass_kg=4.0 * TIP_MASS_KG
    )
    result = _solve(rig, (heavy,), "heavy", load=_tip_load())
    assert result.natural_frequencies[0] == pytest.approx(
        _predicted_hz(4.0 * TIP_MASS_KG), rel=FREQUENCY_TOLERANCE
    )


def test_gravity_reaches_the_carried_mass(rig) -> None:
    """The half that would have failed in silence.

    A ``MASS`` element is not in ``Eall``, so a ``GRAV`` load naming only
    ``Eall`` leaves it weightless. Measured that way the reaction is 0.3843 N,
    the beam's own weight alone, with exit code 0 and nothing in the log. The
    deck therefore names every mass element set as well.
    """
    mass = PointMass.from_engineering_units(name="tip", region="load_face", mass_kg=TIP_MASS_KG)
    result = _solve(rig, (mass,), "gravity", load=_weight_load(), modal=False)

    beam_weight = _beam_mass_t() * GRAVITY
    carried_weight = TIP_MASS_KG * 1.0e-3 * GRAVITY
    expected = beam_weight + carried_weight

    reaction = float(abs(result.reaction_force[2]))
    assert reaction == pytest.approx(expected, rel=0.01), (
        f"reaction {reaction:.4f} N against {expected:.4f} N expected "
        f"(beam {beam_weight:.4f} + carried {carried_weight:.4f}). A reaction "
        f"near {beam_weight:.4f} N means the carried mass has no weight."
    )
    # Stated separately and deliberately: the carried weight is five times the
    # beam's, so a result that dropped it would still look like a plausible
    # reaction for a beam of this size.
    assert reaction > 1.5 * beam_weight
