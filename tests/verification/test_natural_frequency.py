"""V14 — natural frequencies of a cantilever, against published beam theory.

**What this is for.** Every object has rates it likes to vibrate at. If
something drives a part at one of those rates, small pushes build into large
movements and the part can shake itself apart under a load it would carry all
day if the load were steady. A static analysis cannot see this at all. This
test says the frequencies OpenOptima reports are the real ones.

**Case.** Cantilever, 100 x 10 x 5 mm, steel (E = 210 GPa, nu = 0.3,
rho = 7850 kg/m^3), fully fixed at x = 0. A load is attached because the
project format requires every load case to carry one, and it plays no part in
the answer: a natural frequency comes from stiffness and mass alone. That is
not asserted, it is measured -- see ``TestTheLoadDoesNotChangeTheAnswer``,
which runs the same part at 1 N and at 5000 N and requires identical numbers.

The section is deliberately rectangular rather than square. A square one bends
identically two ways, so its first two modes land on the same frequency and a
test could not tell which it had been given -- nor notice if one went missing.
At 10 x 5 the easy way and the hard way are a clear factor of two apart.

**Reference.** Euler-Bernoulli beam theory, the standard published result:

.. math::

    f_i = \\frac{\\lambda_i^2}{2 \\pi L^2} \\sqrt{\\frac{E I}{\\rho A}}

with :math:`\\lambda_1 = 1.875104` and :math:`\\lambda_2 = 4.694091` for a
clamped-free beam. For this geometry that gives 417.8 Hz bending the easy way,
835.5 Hz bending the hard way, and 2618.0 Hz for the second easy-way mode.

**Expected agreement.** Measured at the time of writing, on the 11 815-node
mesh this test builds: **418.9, 831.5 and 2595.0 Hz**, which is **+0.27%,
-0.48% and -0.88%**.

That residual is not mesh error and does not shrink if the mesh is refined.
Measured across four mesh sizes from 4.0 mm down to 1.5 mm, the first mode
moved only from +0.34% to +0.26% and then stopped. What is left is the
difference between a real three-dimensional part and an ideal beam: beam
theory ignores shear deformation and rotary inertia, and fixing a whole end
face also stops the Poisson contraction an ideal clamp would allow. A 2% band
covers that with room to spare and is still far tighter than any real defect
would be -- a skipped first mode would read 832 Hz, which is 99% out.

The mode *ratios* are checked as well, and they are the sharper test. They
depend only on the shape, not on the material, the density or the unit system,
so they stay true even if every absolute number were scaled by a units error.

**One question this had to answer before it could be trusted at all.**
CalculiX silently skips the lowest *buckling* mode when the answer falls below
about 0.52, returning the second one -- nine times too high, in the unsafe
direction (see V9 and trap 7). Anything solving an eigenvalue problem in the
same code has to be suspected of the same thing. It was measured rather than
assumed: a long thin beam whose first frequency is 18.6 Hz was as accurate
(+0.20%) as this stubby one at 419 Hz (+0.27%), and the mode ratios held to
0.02% on the slender one. There is no magnitude-dependent defect in the
frequency solve.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass -- that is exactly the change a human has to review.
"""

from __future__ import annotations

import math

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode, outcome_for
from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    ModalSettings,
    SolverSpecification,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh

LENGTH, WIDTH, DEPTH = 100.0, 10.0, 5.0
MODULUS, POISSON, DENSITY = 210000.0, 0.3, 7850.0
MODES = 6

#: Agreement band against Euler-Bernoulli theory. See the module docstring: the
#: measured residual is beam-theory error, not mesh error, and does not shrink
#: with refinement.
FREQUENCY_TOLERANCE = 0.02
#: The mode ratios depend only on the shape, so they are held to the same band
#: while being immune to any error in the material or the unit conversion.
RATIO_TOLERANCE = 0.02

#: Clamped-free beam eigenvalues, from any standard vibration text.
LAMBDA_1 = 1.875104
LAMBDA_2 = 4.694091


def beam_frequency(lam: float, second_moment: float) -> float:
    """The published closed-form answer, in hertz."""
    area = WIDTH * DEPTH
    # Density arrives in kg/m^3 and the solver works in tonnes per cubic
    # millimetre. Converting here rather than reusing the internal number keeps
    # this reference independent of the code under test.
    density = DENSITY * 1.0e-12
    return (lam**2 / (2.0 * math.pi * LENGTH**2)) * math.sqrt(
        MODULUS * second_moment / (density * area)
    )


def published_frequencies() -> tuple[float, float, float]:
    easy = WIDTH * DEPTH**3 / 12.0  # bending the easy way, out of plane
    hard = DEPTH * WIDTH**3 / 12.0  # bending the hard way, in plane
    return (
        beam_frequency(LAMBDA_1, easy),
        beam_frequency(LAMBDA_1, hard),
        beam_frequency(LAMBDA_2, easy),
    )


def _build(directory, dofs=(1, 2, 3), modes=MODES, loads=(("held", 1000.0),)):
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
                surface_type=SurfaceType.PLANE,
                normal=(-1.0, 0.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
        SemanticRegion(
            "load_face",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(1.0, 0.0, 0.0),
                normal_tolerance_deg=2.0,
            ),
        ),
    )
    mesher = GmshMesher(MeshSpecification(global_size=4.0, minimum_size=1.5, element_order=2))
    mesh, _regions = mesher.generate(geometry, regions, directory / "mesh")

    def case(case_id: str, force: float) -> LoadCase:
        # A load case must carry a load, and the frequency step deliberately
        # carries none. The loads here therefore exist only to satisfy the
        # project format and to give the next test something to prove.
        return LoadCase(
            id=case_id,
            boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=dofs),),
            loads=(Load(kind=LoadKind.FORCE, region="load_face", vector=(0.0, 0.0, -force)),),
        )

    model = AnalysisModel(
        name="natural frequency verification",
        material=Material.from_engineering_units(
            name="Steel",
            elastic_modulus_mpa=MODULUS,
            poisson_ratio=POISSON,
            density_kg_m3=DENSITY,
            allowable_stress_mpa=250.0,
        ),
        load_cases=tuple(case(case_id, force) for case_id, force in loads),
        modal=ModalSettings(enabled=True, modes=modes),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=900))
    return model, mesh, solver, directory


@pytest.fixture(scope="module")
def cantilever_modes(tmp_path_factory):
    directory = tmp_path_factory.mktemp("modal")
    model, mesh, solver, directory = _build(directory)
    results = solver.solve(model, mesh, directory / "solver")
    return results.by_id("held")


@pytest.fixture(scope="module")
def two_load_cases(tmp_path_factory):
    """The same part, held identically, under two very different loads."""
    directory = tmp_path_factory.mktemp("modal_two")
    model, mesh, solver, directory = _build(directory, loads=(("gentle", 1.0), ("hard", 5000.0)))
    results = solver.solve(model, mesh, directory / "solver")
    return results


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestFrequenciesAgainstBeamTheory:
    def test_it_reports_the_requested_number_of_modes(self, cantilever_modes):
        assert len(cantilever_modes.natural_frequencies) == MODES

    def test_the_first_three_match_published_beam_theory(self, cantilever_modes):
        computed = cantilever_modes.natural_frequencies[:3]
        for index, (measured, published) in enumerate(
            zip(computed, published_frequencies(), strict=True), start=1
        ):
            error = abs(measured - published) / published
            assert error < FREQUENCY_TOLERANCE, (
                f"mode {index}: {measured:.2f} Hz against a published "
                f"{published:.2f} Hz is {error * 100:.2f}% out"
            )

    def test_the_lowest_mode_is_not_skipped(self, cantilever_modes):
        """The question trap 7 forces us to ask of any eigenvalue solve.

        If the frequency solve dropped the first mode the way the buckling
        solve drops it, the reported fundamental would be the *second* mode --
        bending the hard way, at twice the frequency. Checking the absolute
        value catches that, because 832 Hz is 99% away from the published 418.
        """
        fundamental = cantilever_modes.natural_frequencies[0]
        easy, hard, _second = published_frequencies()
        assert abs(fundamental - easy) / easy < FREQUENCY_TOLERANCE
        assert abs(fundamental - hard) / hard > 0.4

    def test_the_mode_ratios_match(self, cantilever_modes):
        """A shape check that survives any error in material or units.

        Bending the hard way is exactly four times stiffer here -- the section
        is twice as wide as it is deep, and stiffness goes with the cube -- so
        that frequency is exactly twice the first. The third follows from the
        published eigenvalues alone. Neither ratio knows anything about steel.
        """
        first, second, third = cantilever_modes.natural_frequencies[:3]
        assert abs(second / first - 2.0) / 2.0 < RATIO_TOLERANCE
        expected_third = (LAMBDA_2 / LAMBDA_1) ** 2
        assert abs(third / first - expected_third) / expected_third < RATIO_TOLERANCE

    def test_every_frequency_is_positive_and_ordered(self, cantilever_modes):
        values = cantilever_modes.natural_frequencies
        assert all(value > 0.0 for value in values)
        assert list(values) == sorted(values)

    def test_the_fundamental_is_the_lowest(self, cantilever_modes):
        assert cantilever_modes.fundamental_frequency == min(cantilever_modes.natural_frequencies)


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestTheLoadDoesNotChangeTheAnswer:
    """A natural frequency comes from stiffness and mass, and nothing else.

    This is the claim the deck writer rests on when it puts no loads at all in
    the frequency step, and it is why load cases holding the part the same way
    share one solve instead of paying for one each. Asserting it in a docstring
    would be worth nothing, so it is measured: the same part under 1 N and
    under 5000 N -- five thousand times the load -- must return the same
    frequencies to the last digit.
    """

    def test_a_five_thousandfold_load_change_moves_nothing(self, two_load_cases):
        gentle = two_load_cases.by_id("gentle").natural_frequencies
        hard = two_load_cases.by_id("hard").natural_frequencies
        assert gentle == hard

    def test_the_loads_really_were_different(self, two_load_cases):
        """Guards the test above from passing because nothing was applied."""
        gentle = float(two_load_cases.by_id("gentle").displacement_magnitude.max())
        hard = float(two_load_cases.by_id("hard").displacement_magnitude.max())
        assert hard > gentle * 1000.0

    def test_both_cases_still_get_a_real_answer(self, two_load_cases):
        for case_id in ("gentle", "hard"):
            frequencies = two_load_cases.by_id(case_id).natural_frequencies
            assert len(frequencies) == MODES
            assert frequencies[0] > 0.0


@requires_gmsh
@requires_calculix
@pytest.mark.verification
@pytest.mark.slow
class TestAPartThatIsNotHeldIsRefused:
    """A part free to drift or spin has no natural frequency to report.

    CalculiX answers this question with zeros and a successful exit code, so
    nothing downstream would notice. Measured on this beam held only against
    the load direction: four of six modes came back at exactly 0 Hz, with the
    first real one at 1821 Hz.
    """

    def test_a_partly_held_part_stops_the_evaluation(self, tmp_path):
        model, mesh, solver, directory = _build(tmp_path, dofs=(3,))
        with pytest.raises(EvaluationFailure) as raised:
            solver.solve(model, mesh, directory / "solver")
        assert raised.value.code is FailureCode.MODEL_NOT_HELD

    def test_the_message_says_what_to_fix(self, tmp_path):
        model, mesh, solver, directory = _build(tmp_path, dofs=(3,))
        with pytest.raises(EvaluationFailure) as raised:
            solver.solve(model, mesh, directory / "solver")
        message = str(raised.value).lower()
        assert "do not hold the part still" in message
        assert "supports" in message

    def test_it_is_an_error_and_never_a_bad_design(self, tmp_path):
        """The optimiser must learn nothing from it.

        A part nobody held is a setup mistake, and the same supports apply to
        every design in a study. Reporting it as a poor design would teach the
        search to avoid a region of the design space for no reason at all.
        """
        from openoptima.domain.failures import Outcome

        assert outcome_for(FailureCode.MODEL_NOT_HELD) is Outcome.ERROR
