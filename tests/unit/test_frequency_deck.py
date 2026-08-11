"""The ``*FREQUENCY`` step: where it goes, what it carries, what it suppresses.

These need no CAE tool. The end-to-end evidence is in
``docs/verification-plan.md`` under V14.
"""

from __future__ import annotations

import numpy as np

from openoptima.domain.model import (
    AnalysisModel,
    BoundaryCondition,
    BucklingSettings,
    Load,
    LoadCase,
    LoadKind,
    Material,
    ModalSettings,
)
from openoptima.meshing.base import MeshData
from openoptima.solvers.calculix.deck import write_deck

STEEL = Material.from_engineering_units(
    name="Steel",
    elastic_modulus_mpa=210000.0,
    poisson_ratio=0.3,
    density_kg_m3=7850.0,
    allowable_stress_mpa=250.0,
)


def _mesh() -> MeshData:
    return MeshData(
        node_tags=np.array([1, 2, 3, 4]),
        coordinates=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        element_tags=np.array([1]),
        connectivity=np.array([[1, 2, 3, 4]]),
        element_type="C3D4",
        surface_nodes={
            "base": np.array([1, 2, 3]),
            "side": np.array([1, 2, 4]),
            "tip": np.array([4]),
        },
        surface_triangles={
            "base": np.array([[1, 2, 3]]),
            "side": np.array([[1, 2, 4]]),
            "tip": np.array([[4, 2, 3]]),
        },
    )


def _case(case_id: str, held: str = "base", dofs=(1, 2, 3)) -> LoadCase:
    return LoadCase(
        id=case_id,
        boundary_conditions=(BoundaryCondition(region=held, dofs=dofs),),
        loads=(Load(kind=LoadKind.FORCE, region="tip", vector=(1.0, 0.0, 0.0)),),
    )


def _write(tmp_path, cases, *, modal=True, modes=6, buckling=False):
    model = AnalysisModel(
        name="frequency deck test",
        material=STEEL,
        load_cases=tuple(cases),
        modal=ModalSettings(enabled=modal, modes=modes),
        buckling=BucklingSettings(enabled=buckling, modes=3),
    )
    deck = write_deck(model, _mesh(), tmp_path)
    return deck, deck.main_file.read_text()


class TestTheStepIsWrittenOnlyWhenAskedFor:
    def test_it_is_absent_by_default(self, tmp_path):
        _deck, text = _write(tmp_path, [_case("pull")], modal=False)
        assert "*FREQUENCY" not in text

    def test_it_appears_when_enabled(self, tmp_path):
        _deck, text = _write(tmp_path, [_case("pull")])
        assert "*FREQUENCY" in text

    def test_the_mode_count_is_passed_through(self, tmp_path):
        _deck, text = _write(tmp_path, [_case("pull")], modes=12)
        assert "*FREQUENCY\n12\n" in text


class TestTheStepCarriesSupportsButNoLoad:
    """A natural frequency comes from stiffness and mass, and nothing else."""

    def test_it_holds_the_part(self, tmp_path):
        _deck, text = _write(tmp_path, [_case("pull")])
        block = text.split("*FREQUENCY")[1]
        assert "*BOUNDARY, OP=NEW" in block
        assert "R_BASE, 1, 1, 0" in block

    def test_it_applies_no_force(self, tmp_path):
        """A load in this step would be noise. Worse, a reader might believe it."""
        _deck, text = _write(tmp_path, [_case("pull")])
        block = text.split("*FREQUENCY")[1]
        assert "*CLOAD" not in block
        assert "*DLOAD" not in block


class TestModeShapesAreSuppressed:
    """The single most important line in the step.

    CalculiX carries a ``*NODE FILE`` request forward from the step that made
    it, so a frequency step following a static one writes DISP, STRESS and
    ERROR for every mode into the FRD without being asked. Measured on the
    probe cantilever: 21 blocks and 46 413 lines with the request carried
    forward, 3 blocks and 10 131 lines with it cleared, and identical
    frequencies either way.
    """

    def test_the_output_requests_are_cleared(self, tmp_path):
        _deck, text = _write(tmp_path, [_case("pull")])
        block = text.split("*FREQUENCY")[1]
        assert "*NODE FILE\n\n" in block
        assert "*EL FILE\n\n" in block

    def test_nothing_is_requested_by_name(self, tmp_path):
        """An empty request replaces the carried-forward one; a named request
        would reinstate exactly the problem this avoids."""
        block = _write(tmp_path, [_case("pull")])[1].split("*FREQUENCY")[1]
        node_file = block.split("*NODE FILE")[1].split("*")[0]
        assert node_file.strip() == ""


class TestLoadCasesHeldTheSameWayShareOneSolve:
    """Identical supports give identical frequencies, exactly, so solving twice
    would buy nothing. Four load cases on one set of supports pay for one
    eigenvalue solve rather than four."""

    def test_two_cases_with_the_same_supports_share_a_step(self, tmp_path):
        deck, text = _write(tmp_path, [_case("gentle"), _case("hard")])
        assert text.count("*FREQUENCY") == 1
        assert deck.frequency_step["gentle"] == deck.frequency_step["hard"]

    def test_different_supports_get_their_own_step(self, tmp_path):
        deck, text = _write(tmp_path, [_case("a", held="base"), _case("b", held="side")])
        assert text.count("*FREQUENCY") == 2
        assert deck.frequency_step["a"] != deck.frequency_step["b"]

    def test_the_same_region_held_in_different_directions_is_different(self, tmp_path):
        """Holding a face in one direction is not the same as holding it in
        three, and the frequencies differ. Comparing region names alone would
        have called these the same."""
        deck, text = _write(tmp_path, [_case("a", dofs=(1, 2, 3)), _case("b", dofs=(3,))])
        assert text.count("*FREQUENCY") == 2
        assert deck.frequency_step["a"] != deck.frequency_step["b"]


class TestStepNumbering:
    """Results are selected by step number, so these numbers are load-bearing."""

    def test_frequency_steps_come_after_every_load_case(self, tmp_path):
        """Adding modal analysis must not move any static or buckling step.

        Every verified benchmark in this project reads its static results from
        a step number. If a frequency step were interleaved, load case two
        would be read from load case one's neighbour.
        """
        deck, text = _write(tmp_path, [_case("a"), _case("b", held="side")])
        assert text.index("*STATIC") < text.index("*FREQUENCY")
        assert deck.frequency_step["a"] == 3
        assert deck.frequency_step["b"] == 4

    def test_buckling_steps_are_counted_too(self, tmp_path):
        """With buckling on there are two steps per load case, not one."""
        deck, _text = _write(tmp_path, [_case("a"), _case("b", held="side")], buckling=True)
        assert deck.frequency_step["a"] == 5
        assert deck.frequency_step["b"] == 6

    def test_no_mapping_is_recorded_when_modal_is_off(self, tmp_path):
        deck, _text = _write(tmp_path, [_case("a")], modal=False)
        assert deck.frequency_step == {}


class TestTheSettingIsInTheCacheKey:
    """A result computed without frequencies is not a hit for one that wants them.

    Everything that can change a number belongs in the evaluation digest. Leave
    a physics setting out and the cache serves the old answer to the new
    question, quickly and wrongly.
    """

    @staticmethod
    def _project(**modal):
        import dataclasses

        from openoptima.schema.loader import load_project

        project = load_project("examples/l_bracket/project.yaml")
        return dataclasses.replace(project, modal=ModalSettings(**modal))

    def test_turning_it_on_changes_the_digest(self):
        off = self._project(enabled=False)
        on = self._project(enabled=True)
        assert off.setup_digest() != on.setup_digest()

    def test_asking_for_more_modes_changes_the_digest(self):
        six = self._project(enabled=True, modes=6)
        twelve = self._project(enabled=True, modes=12)
        assert six.setup_digest() != twelve.setup_digest()

    def test_the_same_settings_give_the_same_digest(self):
        assert (
            self._project(enabled=True, modes=6).setup_digest()
            == self._project(enabled=True, modes=6).setup_digest()
        )
