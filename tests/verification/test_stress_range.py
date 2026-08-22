"""V18 -- how far the stress swings, against a case whose answer is exact.

Fatigue is driven by how far stress **swings** each cycle, not by how high it
gets. A part cycling between nothing and 100 MPa is in a different situation
from one sitting between 45 and 55, even though the peak is nearly the same.

**The defect this benchmark exists to catch.** The obvious way to get that
swing out of what OpenOptima already reports is to subtract one load case's
von Mises stress from another's. Von Mises keeps the *size* of a stress state
and throws away its *direction*, and that is fatal here. Measured on the
example L-bracket, with the top of the cycle at full load:

    bottom of cycle   from von Mises   from the tensors     error
      +0.5 x load          17.9189           17.9189         0.0%
       0   (off load)      35.8378           35.8378         0.0%
      -0.25 x load         26.8783           44.7972       -40.0%
      -0.5  x load         17.9189           53.7567       -66.7%
      -1.0  x load          0.0000           71.6756      -100.0%

It is exact while the load never reverses, and then collapses. At the bottom
of that table the load is fully reversed -- pushed as hard one way as the
other -- and the two ends have **identical** von Mises stress to every digit,
so the swing reads as zero and the part appears to last for ever. Every error
is in the direction that says the part is safe, and the worst of them is the
case fatigue exists to catch: a vibrating part lives in fully reversed
loading. It is also invisible to casual testing, because the method is exactly
right for an on-off load, which is what anybody would try first.

**The case here.** A straight bar pulled and pushed along its own axis, where
the stress is uniform and exactly ``P / A`` -- no bending, no stress
concentration, and no singularity to argue about. 100 x 10 x 5 mm steel, so
``A = 50 mm2``, held at one end and loaded at the other.

Two independent checks, deliberately not the same check twice:

1. **Against bar theory.** Under a 5000 N pull the stress away from the ends
   must be ``5000 / 50 = 100.00 MPa``.
2. **Against superposition**, which is a property of linear elasticity rather
   than of this software. With the two ends of a cycle at ``a`` and ``b``
   times a reference load, the swing must be ``|a - b| / 2`` of the reference
   stress and its middle ``(a + b) / 2`` of it -- including the sign, so a
   cycle that pulls the material apart is distinguishable from one that
   presses it together. This holds at every point in the part and needs no
   beam theory at all.

Recorded in ``docs/verification-plan.md``. Do not widen a tolerance to make a
failing build pass.
"""

from __future__ import annotations

import numpy as np
import pytest

from openoptima.domain.fatigue import EquivalentStress, FatigueSettings, LoadCycle
from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
    SolverSpecification,
    StressEvaluation,
)
from openoptima.domain.project import GeometryDefinition
from openoptima.domain.regions import RegionSelector, SemanticRegion, SurfaceType
from openoptima.domain.variables import DesignSpace, DesignVariable
from openoptima.geometry.occ.provider import OccGeometryProvider
from openoptima.meshing.gmsh_mesher import GmshMesher
from openoptima.results.fatigue import cycle_stress
from openoptima.solvers.calculix.solver import CalculiXSolver

from ..conftest import requires_calculix, requires_gmsh, requires_pylife

pytestmark = [requires_gmsh, requires_calculix, requires_pylife]

LENGTH, WIDTH, DEPTH = 100.0, 10.0, 5.0
AREA = WIDTH * DEPTH
MODULUS, POISSON, DENSITY = 210000.0, 0.3, 7850.0

#: The reference pull, chosen to make the answer a round 100 MPa.
REFERENCE_LOAD_N = 5000.0
REFERENCE_STRESS_MPA = REFERENCE_LOAD_N / AREA

#: Away from the ends a pulled bar carries exactly P/A. Near them the fixed
#: face stops the bar contracting sideways, which disturbs the stress over
#: about one depth, so the check is made in the middle third.
UNIFORM_TOLERANCE = 0.01

#: CalculiX writes its results file in ``E12.5`` -- six significant figures,
#: e.g. ``5.00000E+00``. Every stress component therefore arrives already
#: rounded, and two solves of the same part at different load levels are not
#: exactly proportional to each other however linear the physics is.
#:
#: **Measured on this very case**, against the load case at full load:
#:
#:     -1.0 x load    departure from exact proportionality   0.000e+00
#:     +0.5 x load                                           2.620e-06
#:     -0.5 x load                                           2.620e-06
#:
#: The reversed case is *exact* because negating a load negates every written
#: value without touching its mantissa. Halving one re-rounds it.
WRITTEN_PRECISION = 3.0e-6


def _superposition_tolerance(first: float, second: float) -> float:
    """How closely superposition can hold, given how the file is written.

    A swing is a *difference*, so rounding in the two ends is magnified by how
    close together they are: two load levels that nearly cancel leave a small
    difference carrying both roundings. That gives
    ``(|a| + |b|) / |a - b|`` times the per-value precision.

    Derived, not tuned. Measured residuals sit well inside it -- 1.03e-6
    against a 7.9e-6 bound for the closest pair here, and 3.42e-7 against
    2.6e-6 for the widest. This bounds the results file, not the physics: do
    not reach for it to make a real disagreement pass.
    """
    return WRITTEN_PRECISION * (abs(first) + abs(second)) / abs(first - second)


#: The multiples of the reference load that are solved.
MULTIPLES = (1.0, 0.5, -0.5, -1.0)


def _case(multiple: float) -> LoadCase:
    return LoadCase(
        id=_name(multiple),
        loads=(
            Load(
                kind=LoadKind.FORCE,
                region="load_face",
                vector=(multiple * REFERENCE_LOAD_N, 0.0, 0.0),
            ),
        ),
        boundary_conditions=(BoundaryCondition(region="fixed_face", dofs=(1, 2, 3)),),
        description=f"{multiple:+g} of the reference pull",
    )


def _name(multiple: float) -> str:
    return "m" + f"{multiple:+g}".replace("+", "p").replace("-", "n").replace(".", "_")


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    """One mesh, one solve, every load case. Nothing below re-meshes."""
    directory = tmp_path_factory.mktemp("stress_range")
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
    mesh, _region_map = mesher.generate(geometry, regions, directory / "mesh")

    material = Material.from_engineering_units(
        name="Steel",
        elastic_modulus_mpa=MODULUS,
        poisson_ratio=POISSON,
        density_kg_m3=DENSITY,
        allowable_stress_mpa=200.0,
    )
    model = AnalysisModel(
        name="axial bar",
        material=material,
        load_cases=tuple(_case(multiple) for multiple in MULTIPLES),
        stress_evaluation=StressEvaluation(measure="raw_max"),
    )
    solver = CalculiXSolver(SolverSpecification(name="calculix", timeout_seconds=900))
    results = solver.solve(model, mesh, directory / "solve")
    fields = {case.load_case_id: case for case in results.load_cases}
    return mesh, fields


def _middle_third(mesh, fields) -> np.ndarray:
    """Rows of the field arrays lying in the middle third of the bar."""
    coordinates = mesh.coordinates_of(fields.node_tags)
    x = coordinates[:, 0]
    return (x > LENGTH / 3.0) & (x < 2.0 * LENGTH / 3.0)


def _peak_from_tensor(fields) -> float:
    """The largest von Mises stress, computed from the tensor.

    Deliberately not the solver's own von Mises field. Both are correct, but
    they are rounded independently on the way into the results file, so
    comparing one against the other can never do better than
    ``FILE_PRECISION``. Superposition is exact, and testing it needs both
    sides to come from the same numbers.
    """
    from pylife.stress import equistress

    from openoptima.results.fatigue import _columns

    tensor = np.asarray(fields.stress_tensor, dtype=float)
    return float(np.asarray(equistress.mises(**_columns(tensor))).max())


def _measure(fields, between, convention=EquivalentStress.SIGNED_MISES_TRACE):
    cycle = LoadCycle(name="cycle", between=between)
    return cycle_stress(
        cycle,
        fields,
        FatigueSettings(enabled=True, cycles=(cycle,), equivalent_stress=convention),
        StressEvaluation(measure="raw_max"),
    )


def test_a_pulled_bar_carries_exactly_the_load_over_its_area(solved) -> None:
    """The closed form this whole benchmark rests on: 5000 N over 50 mm2."""
    mesh, fields = solved
    pulled = fields[_name(1.0)]
    inside = _middle_third(mesh, pulled)
    stress = np.asarray(pulled.von_mises)[inside]

    assert stress.size > 50, "too few nodes in the middle third to judge"
    assert float(np.median(stress)) == pytest.approx(REFERENCE_STRESS_MPA, rel=UNIFORM_TOLERANCE)


def test_the_tensor_and_the_von_mises_beside_it_describe_the_same_state(solved) -> None:
    """Two things at once, and the second is why this test is here.

    The solver writes a von Mises stress *and* the six-component stress state
    it came from. Recomputing one from the other must agree, and it agrees
    only to about one part in a million because the file is written in
    ``E12.5`` -- six significant figures. That sets the floor on any
    comparison between two quantities taken by different routes out of it.

    It would also catch a shear component read into the wrong slot, which
    would rotate every answer rather than break it -- the kind of error that
    produces a plausible number and no complaint.
    """
    _mesh, fields = solved
    from pylife.stress import equistress

    from openoptima.results.fatigue import _columns

    pulled = fields[_name(1.0)]
    tensor = np.asarray(pulled.stress_tensor, dtype=float)
    from_tensor = np.asarray(equistress.mises(**_columns(tensor)))
    written = np.asarray(pulled.von_mises)

    biggest = float(np.abs(written).max())
    worst = float(np.abs(from_tensor - written).max()) / biggest
    assert worst < WRITTEN_PRECISION, f"tensor and von Mises disagree by {worst:.2e} relative"


def test_a_fully_reversed_cycle_swings_the_whole_stress(solved) -> None:
    """The defect, as a number.

    Pushed as hard one way as the other, the two ends have identical von Mises
    stress, so a swing taken from that reads zero. The real swing is the whole
    stress and the middle of it is nothing.
    """
    _mesh, fields = solved
    up = np.asarray(fields[_name(1.0)].von_mises)
    down = np.asarray(fields[_name(-1.0)].von_mises)

    # The two ends really are indistinguishable by von Mises. This is what
    # makes the wrong method look reasonable.
    assert float(np.abs(up - down).max()) < 1.0e-6

    measured = _measure(fields, (_name(1.0), _name(-1.0)))
    assert measured.amplitude_max == pytest.approx(float(up.max()), rel=1.0e-9)
    assert measured.amplitude_max > 0.9 * REFERENCE_STRESS_MPA
    assert measured.mean_at_worst == pytest.approx(0.0, abs=1.0e-6)


@pytest.mark.parametrize(
    "first,second",
    [(1.0, -1.0), (1.0, 0.5), (1.0, -0.5), (0.5, -1.0), (-0.5, -1.0)],
)
def test_the_swing_and_its_middle_follow_superposition(solved, first, second) -> None:
    """Checked against linear elasticity, not against an earlier run.

    With the ends at ``a`` and ``b`` times the reference load, the whole stress
    field is proportional to the load, so the swing is ``|a - b| / 2`` of the
    reference field at **every** point and its middle is ``(a + b) / 2`` of it.
    That makes this exact rather than approximate, and it deliberately compares
    against the reference field's own peak rather than against ``P / A``: the
    two differ by about 30% at the held end, where the support stops the bar
    contracting sideways, and mixing them up would be comparing two different
    places in the part.
    """
    _mesh, fields = solved
    reference_peak = _peak_from_tensor(fields[_name(1.0)])

    measured = _measure(fields, (_name(first), _name(second)))

    tolerance = _superposition_tolerance(first, second)
    assert measured.amplitude_max == pytest.approx(
        abs(first - second) / 2.0 * reference_peak, rel=tolerance
    )
    # The bar is pulled under a positive multiple, so the reference peak is
    # tension and the middle of the swing carries the sign of (a + b).
    assert measured.mean_at_worst == pytest.approx(
        (first + second) / 2.0 * reference_peak, rel=tolerance, abs=1.0e-9
    )


def test_pulling_apart_is_told_from_pressing_together(solved) -> None:
    """The sign of the mean stress is not decoration.

    A mean that pulls the material apart holds a crack open and makes the same
    swing far more damaging; one that presses holds it shut. Two cycles with
    the identical swing and opposite middles must not report the same number.
    """
    _mesh, fields = solved
    pulled = _measure(fields, (_name(0.5), _name(1.0)))
    pressed = _measure(fields, (_name(-0.5), _name(-1.0)))

    assert pulled.amplitude_max == pytest.approx(pressed.amplitude_max, rel=1.0e-6)
    assert pulled.mean_at_worst > 0.0
    assert pressed.mean_at_worst < 0.0
    assert pulled.mean_at_worst == pytest.approx(-pressed.mean_at_worst, rel=1.0e-6)


def test_naming_the_ends_the_other_way_round_changes_nothing(solved) -> None:
    _mesh, fields = solved
    forward = _measure(fields, (_name(1.0), _name(-0.5)))
    backward = _measure(fields, (_name(-0.5), _name(1.0)))
    assert forward.amplitude_max == pytest.approx(backward.amplitude_max)
    assert forward.mean_at_worst == pytest.approx(backward.mean_at_worst)


def test_both_sign_conventions_agree_on_a_plain_pull(solved) -> None:
    """They do not always agree -- measured on the L-bracket they give
    opposite signs at 137 of 19 787 points -- but on a bar in simple tension
    there is nothing for them to disagree about, so a difference here would
    mean one of them is wired up wrongly."""
    _mesh, fields = solved
    by_trace = _measure(fields, (_name(0.5), _name(1.0)), EquivalentStress.SIGNED_MISES_TRACE)
    by_principal = _measure(
        fields, (_name(0.5), _name(1.0)), EquivalentStress.SIGNED_MISES_MAX_PRINCIPAL
    )
    assert by_trace.mean_at_worst == pytest.approx(by_principal.mean_at_worst, rel=1.0e-9)
