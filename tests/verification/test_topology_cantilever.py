"""V12 — a topology run end to end, and why its result must be re-analysed.

**What this benchmark does and does not claim.** It does not compare against a
published compliance value from the topology literature. Those numbers come
from a different method (density-based SIMP, usually in two dimensions) on a
different mesh, and compliance is not comparable across formulations. Quoting
one as though it validated this pipeline would be verification theatre.

What it does check is what this project actually owns: that the run produces a
sound structure, that the conversion into a solid preserves it, and that the
result is measured rather than taken on trust.

**The case.** A short cantilever design space, 60 x 20 x 4 mm, 600 elements,
fixed along one end and loaded 1 kN downwards at the free corner. Asked to keep
50 per cent of the material, with a 16 mm smallest feature.

Fifty per cent rather than forty, for a measured reason. At 40 per cent this
problem pinches: two parts of the shape meet at a single edge, which is not a
solid, and the conversion refuses it. That is the right refusal and it is
tested separately. It is not monotonic either -- 50 per cent comes out sound
and 60 per cent pinches again -- so it depends on the particular shape rather
than simply on how much material is left.

**One more thing this run must be, and is: repeatable.** It is pinned to a
single processor core. On several cores the identical problem produced two
different shapes; on one core it is bit-identical. See the note on
``REPRODUCIBLE_CPU_CORES`` in ``topology/runner.py``.

**What beam theory says should happen.** Bending stress in a beam is largest
furthest from the middle of the section, so material near the top and bottom
faces earns its place and material near the middle does not. An optimiser that
is working will build something like an I-beam. Measured on the 40 per cent
run:

=============  =======================
Depth band     Material kept
=============  =======================
y = 15-20 mm   57.8% (top flange)
y = 10-15 mm   34.2%
y = 5-10 mm    **22.2% (thin web)**
y = 0-5 mm     45.0% (bottom flange)
=============  =======================

That is the right shape, and it is checked below.

**The measurement that matters most**, taken on the 40 per cent run described
above. The result was re-analysed properly, with the void elements removed and
the same load applied:

- solid block, 600 elements: strain energy **71.6 mJ**
- optimised design, 239 elements (39.8% of the material): **650.7 mJ**

The optimised design is **9.1 times less stiff than the solid block** while
using 40 per cent of its material. Whether that is a good trade is an
engineering judgement and not this software's to make. The point for
verification is narrower and firmer: **that number cannot be predicted from the
optimiser's own objective, so it has to be measured.**

Much of the 9x is a local effect. The load is a point load at a corner, and the
optimiser thinned the tip region to 24 per cent, so a large share of the strain
energy is the structure deforming locally under the load rather than bending as
a beam. That is a real property of point loads on topology results, and a
reason to prefer a distributed load when setting one of these problems up.

Recorded as V12 in ``docs/verification-plan.md``.
"""

from __future__ import annotations

import collections
import itertools
import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from openoptima.domain.model import (
    BoundaryCondition,
    Load,
    LoadCase,
    LoadKind,
    Material,
    MeshSpecification,
)
from openoptima.domain.objectives import Objective
from openoptima.domain.project import GeometryDefinition, Project
from openoptima.domain.regions import (
    BoundingBox,
    RegionSelector,
    SelectionMode,
    SemanticRegion,
    SurfaceType,
)
from openoptima.domain.topology import TopologySettings
from openoptima.domain.variables import DesignSpace
from openoptima.evaluation.pipeline import EvaluationPipeline
from openoptima.geometry.base import SurfaceArtifact
from openoptima.topology import fetch
from openoptima.topology.runner import run_topology
from openoptima.topology.solidify import read_element_mesh, to_solid

from ..conftest import requires_calculix

trimesh = pytest.importorskip("trimesh")

LENGTH, DEPTH, THICKNESS = 60.0, 20.0, 4.0
NX, NY, NZ = 30, 10, 2
VOLUME_FRACTION = 0.5

#: The smallest feature the result may contain. Sets the smoothing radius, so
#: it also decides whether the optimiser has room to make a proper join.
FEATURE_SIZE_MM = 16.0

#: Enough rounds to actually reach the target. beso's own estimate for this
#: problem is (1 - 0.5) / 0.015 + 25 = 58; a run that stops short produces a
#: partly eroded block, and the runner refuses it.
ROUNDS = 60


def build_design_space(path: Path) -> Path:
    """A plain hex-element cantilever: fixed at one end, loaded at the far corner."""

    def node_id(i: int, j: int, k: int) -> int:
        return 1 + i + (NX + 1) * j + (NX + 1) * (NY + 1) * k

    dx, dy, dz = LENGTH / NX, DEPTH / NY, THICKNESS / NZ
    lines = ["*NODE, NSET=Nall"]
    for k in range(NZ + 1):
        for j in range(NY + 1):
            for i in range(NX + 1):
                lines.append(f"{node_id(i, j, k)}, {i * dx:.6f}, {j * dy:.6f}, {k * dz:.6f}")

    lines.append("*ELEMENT, TYPE=C3D8, ELSET=Eall")
    number = 0
    for k in range(NZ):
        for j in range(NY):
            for i in range(NX):
                number += 1
                corners = [
                    node_id(i, j, k),
                    node_id(i + 1, j, k),
                    node_id(i + 1, j + 1, k),
                    node_id(i, j + 1, k),
                    node_id(i, j, k + 1),
                    node_id(i + 1, j, k + 1),
                    node_id(i + 1, j + 1, k + 1),
                    node_id(i, j + 1, k + 1),
                ]
                lines.append(f"{number}, " + ", ".join(str(c) for c in corners))

    fixed = sorted({node_id(0, j, k) for j in range(NY + 1) for k in range(NZ + 1)})
    lines.append("*NSET, NSET=Nfix")
    for start in range(0, len(fixed), 8):
        chunk = fixed[start : start + 8]
        lines.append(", ".join(str(x) for x in chunk) + ("," if start + 8 < len(fixed) else ""))

    tip = sorted({node_id(NX, 0, k) for k in range(NZ + 1)})
    lines.append("*NSET, NSET=Nload")
    lines.append(", ".join(str(x) for x in tip))

    lines += [
        "*MATERIAL, NAME=STEEL",
        "*ELASTIC",
        "210000., 0.3",
        "*SOLID SECTION, ELSET=Eall, MATERIAL=STEEL",
        "*STEP",
        "*STATIC",
        "*BOUNDARY",
        "Nfix, 1, 3",
        "*CLOAD",
        f"Nload, 2, {-1000.0 / len(tip):.6f}",
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*EL PRINT, ELSET=Eall",
        "S",
        "*END STEP",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def beso_is_available() -> bool:
    """beso is fetched at run time, so it may simply not be here."""
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return shutil.which("python") is not None or True


requires_beso = pytest.mark.skipif(
    not beso_is_available(),
    reason="the topology optimiser needs matplotlib, which it imports at module scope",
)


@pytest.fixture(scope="module")
def topology_result(tmp_path_factory):
    """One real topology run, reused by everything below."""
    tmp = tmp_path_factory.mktemp("v12")
    deck = build_design_space(tmp / "cantilever.inp")

    beso = fetch.install(Path(tempfile.mkdtemp()))
    settings = TopologySettings(
        volume_fraction=VOLUME_FRACTION,
        minimum_feature_size_mm=FEATURE_SIZE_MM,
        maximum_iterations=ROUNDS,
        evolution_rate=0.03,
    )
    outcome = run_topology(
        settings=settings,
        material=Material(
            name="steel",
            elastic_modulus=210000.0,
            poisson_ratio=0.3,
            density=7.85e-9,
            allowable_stress=250.0,
        ),
        deck=deck,
        beso=beso,
        solver_executable="/usr/bin/ccx",
        output_directory=tmp / "out",
        # One core, so this benchmark gives the same shape every time it runs.
        cpu_cores=1,
        timeout_seconds=1800,
    )
    return outcome, deck


@requires_calculix
@requires_beso
class TestTheRunReachesWhatItWasAskedFor:
    def test_the_material_target_is_met(self, topology_result):
        outcome, _deck = topology_result
        assert outcome.mass_fraction is not None
        assert outcome.mass_fraction == pytest.approx(VOLUME_FRACTION, abs=0.03)

    def test_it_finished_within_the_rounds_allowed(self, topology_result):
        """Stopping early is the good outcome, not a shortfall.

        beso stops once its objective settles, so a run that reaches the mass
        target in fewer rounds than allowed has converged. Measured here: 54 of
        60. What must not happen is running out of rounds short of the target,
        and the runner refuses that case outright.
        """
        outcome, _deck = topology_result
        assert 0 < outcome.iterations <= ROUNDS

    def test_it_produced_both_material_states(self, topology_result):
        outcome, _deck = topology_result
        assert len(outcome.result_meshes) == 2
        assert outcome.solid_mesh.is_file()


@requires_calculix
@requires_beso
class TestMaterialGoesWhereBendingNeedsIt:
    """The check that the optimiser is doing its job, against beam theory.

    Bending stress is largest furthest from the middle of the section, so a
    working optimiser keeps material at the top and bottom and thins the middle
    -- an I-beam. This is a real, independent expectation, not a number copied
    from another tool.
    """

    def test_the_middle_is_thinner_than_both_outsides(self, topology_result):
        outcome, _deck = topology_result
        nodes, elements, _type = read_element_mesh(outcome.solid_mesh)

        kept: collections.Counter = collections.Counter()
        for element in elements:
            centre = sum(nodes[tag][1] for tag in element[:8]) / 8.0
            kept[int(centre // 5.0)] += 1

        bottom, lower_middle, upper_middle, top = (kept[i] for i in range(4))
        middle = lower_middle + upper_middle
        outside = bottom + top
        assert outside > middle, (
            f"material should collect at the top and bottom of a beam in "
            f"bending, but got {outside} elements outside against {middle} in "
            f"the middle. The optimiser is not finding the load path."
        )

    def test_the_tip_is_lighter_than_the_root(self, topology_result):
        """Bending moment is largest at the fixed end, so material belongs there."""
        outcome, _deck = topology_result
        nodes, elements, _type = read_element_mesh(outcome.solid_mesh)

        root = sum(1 for e in elements if sum(nodes[t][0] for t in e[:8]) / 8.0 < LENGTH * 0.25)
        tip = sum(1 for e in elements if sum(nodes[t][0] for t in e[:8]) / 8.0 > LENGTH * 0.75)
        assert root > tip


@requires_calculix
@requires_beso
class TestTheConversionKeepsTheStructure:
    """This is the part OpenOptima built, so this is what it must prove."""

    def test_the_result_becomes_one_sealed_solid(self, topology_result):
        outcome, _deck = topology_result
        solid = to_solid(outcome.solid_mesh)
        assert solid.watertight
        assert solid.body_count == 1

    def test_the_blocky_volume_matches_the_elements_that_survived(self, topology_result):
        """Ties the extracted surface back to the mesh it came from.

        The elements are 2 mm cubes, so their count times 8 mm^3 must equal the
        volume enclosed by the surface. If these disagree, the surface
        extraction has lost or gained material.
        """
        outcome, _deck = topology_result
        _nodes, elements, _type = read_element_mesh(outcome.solid_mesh)
        solid = to_solid(outcome.solid_mesh)

        element_volume = (LENGTH / NX) * (DEPTH / NY) * (THICKNESS / NZ)
        assert solid.volume_before_smoothing_mm3 == pytest.approx(
            len(elements) * element_volume, rel=1e-9
        )

    def test_smoothing_costs_material_and_says_so(self, topology_result):
        """Material is strength. A loss must be measured, never assumed away."""
        outcome, _deck = topology_result
        solid = to_solid(outcome.solid_mesh)

        assert solid.volume_mm3 < solid.volume_before_smoothing_mm3
        assert solid.volume_change > -0.10, (
            f"smoothing removed {abs(solid.volume_change):.1%} of the material, "
            f"which is more than this case has ever needed. Check the number of "
            f"smoothing passes is still even."
        )

    def test_the_surface_survives_a_round_trip_through_a_file(self, topology_result, tmp_path):
        outcome, _deck = topology_result
        solid = to_solid(outcome.solid_mesh)
        written = solid.write_stl(tmp_path / "shape.stl")

        reloaded = trimesh.load(written)
        assert reloaded.is_watertight
        assert reloaded.volume == pytest.approx(solid.volume_mm3, rel=1e-6)


@pytest.fixture(scope="module")
def reanalysed(topology_result, tmp_path_factory):
    """The optimised shape and the untouched block, both put through the pipeline.

    Both go the same way in -- as triangles, re-meshed into solid elements, with
    the same loads and supports resolved by the same selectors -- so the pair is
    a fair comparison rather than two different analyses.
    """
    outcome, _deck = topology_result
    directory = tmp_path_factory.mktemp("v12_reanalysis")
    project = reanalysis_project()
    pipeline = EvaluationPipeline(project, directory / "runs", keep_artifacts=False)

    optimised = to_solid(outcome.solid_mesh).as_surface(directory / "optimised.stl")

    block_path = directory / "block.stl"
    trimesh.creation.box(
        extents=(LENGTH, DEPTH, THICKNESS),
        transform=trimesh.transformations.translation_matrix(
            (LENGTH / 2, DEPTH / 2, THICKNESS / 2)
        ),
    ).export(block_path)
    block = SurfaceArtifact(
        stl_path=block_path,
        volume=LENGTH * DEPTH * THICKNESS,
        bbox=BoundingBox(0.0, 0.0, 0.0, LENGTH, DEPTH, THICKNESS),
        surface_area=2 * (LENGTH * DEPTH + LENGTH * THICKNESS + DEPTH * THICKNESS),
        source_description="the untouched design space",
    )
    return pipeline.evaluate_surface(block), pipeline.evaluate_surface(optimised)


def reanalysis_project() -> Project:
    """The same problem the optimiser was given, written as an ordinary project."""
    regions = (
        SemanticRegion(
            "fixed_end",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(-1.0, 0.0, 0.0),
                within_box=BoundingBox(-0.1, -1.0, -1.0, 0.1, DEPTH + 1, THICKNESS + 1),
                # The optimiser removes the middle of this face, so what was one
                # pad comes back as two. Asking for one would be ambiguous, and
                # rightly refused.
                mode=SelectionMode.ALL,
            ),
        ),
        SemanticRegion(
            "loaded_end",
            RegionSelector(
                surface_type=SurfaceType.PLANE,
                normal=(1.0, 0.0, 0.0),
                within_box=BoundingBox(
                    LENGTH - 0.1, -1.0, -1.0, LENGTH + 0.1, DEPTH + 1, THICKNESS + 1
                ),
                mode=SelectionMode.ALL,
            ),
        ),
    )
    return Project(
        name="cantilever re-analysis",
        geometry=GeometryDefinition(provider="occ", template="plate"),
        design_space=DesignSpace(variables=()),
        regions=regions,
        material=Material(
            name="steel",
            elastic_modulus=210000.0,
            poisson_ratio=0.3,
            density=7.85e-9,
            allowable_stress=250.0,
        ),
        load_cases=(
            LoadCase(
                id="tip",
                loads=(Load(kind=LoadKind.FORCE, region="loaded_end", vector=(0.0, -1000.0, 0.0)),),
                boundary_conditions=(BoundaryCondition(region="fixed_end"),),
            ),
        ),
        mesh=MeshSpecification(
            global_size=2.5,
            minimum_size=1.0,
            element_order=2,
            curvature_refinement=False,
            size_from_thickness=False,
        ),
        objectives=(Objective(metric="mass_kg", direction="min"),),
    )


@requires_calculix
@requires_beso
class TestTheLoopClosesBackToRealNumbers:
    """What the whole exercise is for.

    A topology run hands back a shape. A shape says nothing about stress,
    deflection or how close the part is to breaking, so the shape has to go back
    through the ordinary analysis before anybody may quote a number about it.
    These tests are that analysis, and they are the reason ADR 10 refuses to
    report a density field as a result.

    **Measured at the time of writing**, both through the identical pipeline:

    ==================  =============  ==============
    \\                    Solid block    Optimised
    ==================  =============  ==============
    Volume               4800.0 mm3     2384.5 mm3
    Deflection           0.1424 mm      0.3310 mm
    Stiffness            7022 N/mm      3021 N/mm
    Stored energy        69.1 mJ        161.8 mJ
    Peak stress          217.0 MPa      400.0 MPa
    Factor of safety     **1.15**       **0.63**
    ==================  =============  ==============

    So the design keeps 49.7 per cent of the material and 43.0 per cent of the
    stiffness. And it **stops passing**: at a 250 MPa allowable it goes from a
    factor of safety of 1.15 to 0.63.

    Nothing in the topology run says that. beso was asked for stiffness at a
    mass target and delivered it; stress was never part of the question. Whether
    to accept the trade is the engineer's decision, and this is the measurement
    that decision needs.
    """

    def test_both_shapes_analyse_successfully(self, reanalysed):
        block, optimised = reanalysed
        for label, result in (("solid block", block), ("optimised", optimised)):
            assert result.failure_code is None, f"{label}: {result.message}"
            assert result.metrics

    def test_the_optimised_shape_keeps_about_half_the_material(self, reanalysed):
        block, optimised = reanalysed
        share = optimised.metrics["volume_mm3"] / block.metrics["volume_mm3"]
        assert share == pytest.approx(VOLUME_FRACTION, abs=0.05)

    def test_removing_material_costs_stiffness(self, reanalysed):
        """Measured 43.0 per cent of the block's stiffness for 49.7 per cent of it.

        The bound is deliberately loose, because the exact ratio depends on the
        mesh and the optimiser version. What must stay true is the direction and
        the order of magnitude: taking half the material away costs more than a
        little stiffness and less than all of it.
        """
        block, optimised = reanalysed
        ratio = optimised.metrics["stiffness_n_per_mm"] / block.metrics["stiffness_n_per_mm"]
        assert 0.2 < ratio < 0.8
        assert optimised.metrics["displacement_max_mm"] > block.metrics["displacement_max_mm"]

    def test_the_stress_is_measured_rather_than_assumed(self, reanalysed):
        """The number the optimiser never computed, and the reason for all of this."""
        block, optimised = reanalysed
        assert optimised.metrics["stress_max_mpa"] > block.metrics["stress_max_mpa"]
        assert optimised.metrics["factor_of_safety"] < block.metrics["factor_of_safety"]
        # Reported alongside the percentile, always, so a singular peak at a
        # sharp corner cannot hide behind the smoothed figure.
        assert optimised.metrics["stress_raw_max_mpa"] >= optimised.metrics["stress_max_mpa"]

    def test_the_supports_land_on_both_pads_of_the_split_face(self, reanalysed):
        """The face the part bolts to comes back in two pieces, and both are held.

        If the selectors had found only one of them the part would be held on
        half its mounting face, and every number above would be wrong while
        looking entirely reasonable.
        """
        _block, optimised = reanalysed
        assert optimised.run_directory
        # Read from the run manifest, which is the durable record: the mesh
        # folder is cleared once a run finishes.
        manifest = json.loads(
            (Path(optimised.run_directory) / "evaluation_manifest.json").read_text()
        )
        fixed_end = manifest["regions"]["fixed_end"]
        assert len(fixed_end["face_tags"]) == 2
        assert fixed_end["total_area"] > 40.0


@requires_calculix
@requires_beso
def test_the_optimisers_own_figure_does_not_predict_the_result(topology_result):
    """The reason a topology result is never reported without re-analysing it.

    Measured on this case: the solid block stores 71.6 mJ of strain energy and
    the optimised design stores 650.7 mJ -- **9.1 times less stiff** for 40 per
    cent of the material. Whether that trade is worth taking is the engineer's
    judgement. What matters here is that the number had to be measured.

    beso's own figure cannot stand in for it, and this test shows why. Its
    ``energy_density_mean`` **rises** through the run, from 0.015 to 0.34,
    while the mass falls from 4800 to 1912 mm3. That is exactly what should
    happen -- less material means each remaining piece works harder -- and it
    makes the number useless as a score for the finished shape. It is a
    sensitivity measure inside beso's own model, nothing more.

    This test does not assert the 9.1 ratio, which depends on the mesh and the
    optimiser version. It asserts what must stay true however those change.
    """
    outcome, _deck = topology_result

    log = outcome.output_directory / "cantilever.log"
    assert log.is_file()
    rows = [
        (float(parts[1]), float(parts[2]))
        for parts in (line.split() for line in log.read_text().splitlines())
        if len(parts) >= 3 and re.fullmatch(r"\d+", parts[0])
    ]
    assert len(rows) > 10

    masses = [mass for mass, _objective in rows]
    objective = [value for _mass, value in rows]

    # The run did what it was asked: material came away.
    assert masses[-1] < masses[0]
    assert all(later <= earlier + 1e-9 for earlier, later in itertools.pairwise(masses))

    # And beso's own figure went *up* as it did, so it is not a score for the
    # finished shape. If this ever reverses, the number has changed meaning and
    # the docstring above needs revisiting before anyone relies on it.
    assert objective[-1] > objective[0]

    # Nothing in the outcome claims a stress, a displacement or a factor of
    # safety, because none has been computed. The shape has to go back through
    # the ordinary evaluation pipeline first.
    assert not hasattr(outcome, "stress_max_mpa")
    assert not hasattr(outcome, "factor_of_safety")
    assert not hasattr(outcome, "displacement_max_mm")
