"""V16 -- a carried item has a size, and the size changes the answer.

V15 proved that something bolted to the part is carried at all. It was still
carried *flat*: spread over the face it bolts to, with its middle taken to lie
in that face and no resistance to being turned. A real motor's middle sits
above its pad, and it is a solid object that resists turning. Both make the
part vibrate more slowly than the flat model says.

**Why this is worth a benchmark rather than a footnote.** The error only ever
runs one way -- the reported frequency is too high -- and an optimiser
converges onto a constraint boundary by construction, so every design it
hands back sits exactly where that error bites. Measured on
``examples/drone_arm``, a 35 g motor 28 mm across and 32 mm tall reads
169.8 Hz flat and 165.5 Hz where it really sits, across the 170 Hz limit that
example holds the arm to.

**The case.** The same 100 x 10 x 5 mm steel cantilever as V14 and V15,
carrying 0.2 kg on its free end, now with a shape and a height.

**The closed form.** Two freedoms at the tip: how far it moves and how far it
tilts. Standard cantilever flexibility by the unit-load method::

    w = F L^3/3EI + M L^2/2EI
    t = F L^2/2EI + M L  /EI

An item whose middle sits ``e`` beyond the tip, resisting turning by ``J``
about its own middle, moves by ``w + e t``, so in those two freedoms it
weighs::

    [[ m    ,  m e      ],
     [ m e  ,  m e^2 + J]]

The lowest natural frequency is then the largest eigenvalue of flexibility
times mass. The beam's own mass is added as the standard 0.2235 share at the
tip, and that share is the only approximation here -- it is why every measured
value below sits about 0.2% above the prediction rather than on it.

**Measured:**

    carried item                    measured   closed form   error
    no size (V15 again)             89.3320     89.2315     +0.11%
    cylinder 4 across, 10 tall      83.2489     83.1035     +0.17%
    cylinder 4 across, 20 tall      77.7067     77.5513     +0.20%
    cylinder 4 across, 40 tall      68.1554     67.9979     +0.23%
    box 8 x 8 x 20                  77.6771     77.5217     +0.20%

**A published cross-check that needs no eigenvalue at all.** A slender item
standing on the face has total resistance to turning *about that face* of
exactly ``m h^2 / 3`` -- the textbook value for a uniform rod about its end.
That figure is the sum of two separate pieces of this code, the parallel-axis
shift and the item's own inertia, so it catches an error in either.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openoptima.domain.carried import CarriedShape, CarriedSize
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
TIP_MASS_T = 0.200e-3
BEAM_MASS_SHARE = 0.2235

#: The measured residual against the closed form is 0.23% at its worst, and it
#: is the beam's own mass share rather than anything this code does. 1% catches
#: a real error with room to spare.
TOLERANCE = 0.01


def _beam_mass_t() -> float:
    return LENGTH * WIDTH * DEPTH * DENSITY * 1.0e-12


def _predicted_hz(mass: float, centre_offset: float, own_inertia: float) -> float:
    second_moment = WIDTH * DEPTH**3 / 12.0
    flexibility = np.array([[LENGTH**3 / 3.0, LENGTH**2 / 2.0], [LENGTH**2 / 2.0, LENGTH]]) / (
        MODULUS * second_moment
    )
    inertia = np.array(
        [
            [mass + BEAM_MASS_SHARE * _beam_mass_t(), mass * centre_offset],
            [mass * centre_offset, mass * centre_offset**2 + own_inertia],
        ]
    )
    largest = max(np.linalg.eigvals(flexibility @ inertia).real)
    return 1.0 / (2.0 * math.pi * math.sqrt(largest))


def _predicted_for(size: CarriedSize | None) -> float:
    if size is None:
        return _predicted_hz(TIP_MASS_T, 0.0, 0.0)
    # The face normal is +x and the beam bends in z, so the transverse
    # inertia -- the first entry -- is the one that resists this mode.
    return _predicted_hz(
        TIP_MASS_T, size.effective_centre_height, size.principal_inertia(TIP_MASS_T)[0]
    )


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """One mesh, reused. Every comparison must change only the carried item."""
    directory = tmp_path_factory.mktemp("carried_size")
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
    mesh, _ = mesher.generate(geometry, regions, directory / "mesh")
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=900))
    return mesh, solver, directory


def _solve(rig, size: CarriedSize | None, label: str):
    mesh, solver, directory = rig
    model = AnalysisModel(
        name="carried size verification",
        material=Material.from_engineering_units(
            name="Steel",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=DENSITY,
            allowable_stress_mpa=250.0,
        ),
        load_cases=(
            LoadCase(
                id="held",
                boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=(1, 2, 3)),),
                loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -1.0)),),
            ),
        ),
        point_masses=(PointMass(name="tip", region="load_face", mass=TIP_MASS_T, size=size),),
        modal=ModalSettings(enabled=True, modes=4),
    )
    return solver.solve(model, mesh, directory / label).by_id("held")


CYLINDERS = [
    pytest.param(CarriedSize(CarriedShape.CYLINDER, 4.0, 0.0, 10.0), id="cylinder-10-tall"),
    pytest.param(CarriedSize(CarriedShape.CYLINDER, 4.0, 0.0, 20.0), id="cylinder-20-tall"),
    pytest.param(CarriedSize(CarriedShape.CYLINDER, 4.0, 0.0, 40.0), id="cylinder-40-tall"),
    pytest.param(CarriedSize(CarriedShape.BOX, 8.0, 8.0, 20.0), id="box-20-tall"),
]


@pytest.mark.parametrize("size", CYLINDERS)
def test_a_sized_item_matches_the_closed_form(rig, size) -> None:
    label = f"{size.shape.value}_{size.height:g}"
    measured = _solve(rig, size, label).natural_frequencies[0]
    predicted = _predicted_for(size)
    assert measured == pytest.approx(predicted, rel=TOLERANCE), (
        f"{size.shape.value} {size.across:g} x {size.height:g} mm: "
        f"{measured:.4f} Hz against {predicted:.4f} Hz predicted"
    )


def test_giving_an_item_a_size_always_lowers_the_frequency(rig) -> None:
    """The direction is the whole point, and it is never the other way.

    A model that treated a carried item as flat when it is not reports a
    frequency that is too high -- the reassuring direction. If this ever comes
    out the other way round, the offset has been applied into the part rather
    than out of it, which would look entirely plausible in the output.
    """
    flat = _solve(rig, None, "flat").natural_frequencies[0]
    previous = flat
    for height in (10.0, 20.0, 40.0):
        size = CarriedSize(CarriedShape.CYLINDER, 4.0, 0.0, height)
        measured = _solve(rig, size, f"ordered_{height:g}").natural_frequencies[0]
        assert measured < previous, (
            f"a {height:g} mm tall item read {measured:.3f} Hz, not below the "
            f"{previous:.3f} Hz of the shorter one before it"
        )
        previous = measured
    assert previous < 0.8 * flat


def test_the_flat_answer_is_unchanged(rig) -> None:
    """An item with no size must behave exactly as it did before V16 existed.

    Sizes are opt-in precisely so that no existing project's numbers move. If
    this drifts, every cached result in every workspace became wrong quietly.
    """
    measured = _solve(rig, None, "unchanged").natural_frequencies[0]
    assert measured == pytest.approx(89.332, rel=0.005)


@pytest.mark.parametrize("height", [10.0, 20.0, 40.0])
def test_a_slender_item_standing_on_the_face_is_a_rod_about_its_end(height) -> None:
    """A published figure that needs no solver at all.

    A uniform rod of length ``h`` resists turning about its end by ``m h^2/3``.
    An item standing on the face is exactly that, and the total here is the sum
    of two separate pieces of code -- the shift of the middle away from the
    face, and the item's own resistance about that middle. Either one wrong
    and this fails.

    The diameter is small so the rod idealisation holds; the ``3 r^2`` term is
    subtracted rather than ignored, so the check stays exact.
    """
    diameter = 0.5
    size = CarriedSize(CarriedShape.CYLINDER, diameter, 0.0, height)
    own = size.principal_inertia(TIP_MASS_T)[0]
    girth = TIP_MASS_T * 3.0 * (diameter / 2.0) ** 2 / 12.0
    about_the_face = TIP_MASS_T * size.effective_centre_height**2 + own - girth
    assert about_the_face == pytest.approx(TIP_MASS_T * height**2 / 3.0, rel=1e-12)
