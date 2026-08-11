"""Driving the topology optimiser, and refusing what it should not report.

The behaviour these guard is not "does it run". It is "does it refuse to hand
back something that looks like an answer and is not one". A topology run that
stops early still writes a shape file, and that file is indistinguishable from
a finished result once it is on disk.

Nothing here runs beso. The end-to-end run is an integration test, because it
needs both beso and CalculiX.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openoptima.domain.failures import EvaluationFailure, FailureCode
from openoptima.domain.model import Material
from openoptima.domain.topology import TopologySettings
from openoptima.topology import config, runner, workspace

STEEL = Material(
    name="steel",
    elastic_modulus=210000.0,
    poisson_ratio=0.3,
    density=7.85e-9,
    allowable_stress=250.0,
)


class TestWorkspaceMustHaveNoSpaces:
    """beso starts the solver with shell=True, which breaks on a space.

    The default Windows location is C:\\Users\\First Last\\Documents, so this
    is not a corner case.
    """

    @pytest.mark.parametrize(
        "path",
        [
            r"C:\Users\First Last\Documents",
            "/home/someone/My Projects",
            "/tmp/has;semicolon",
            "/tmp/has&ampersand",
            '/tmp/has"quote',
        ],
    )
    def test_awkward_paths_are_refused(self, path):
        assert not workspace.is_safe(path)

    @pytest.mark.parametrize(
        "path", ["/tmp/topo-abc123", r"C:\OpenOptima-work", "/var/folders/xyz"]
    )
    def test_plain_paths_are_accepted(self, path):
        assert workspace.is_safe(path)

    def test_an_override_is_tried_first(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENOPTIMA_TOPOLOGY_WORKSPACE", str(tmp_path))
        assert workspace.candidate_bases()[0] == tmp_path

    def test_the_chosen_directory_is_usable(self):
        with workspace.workspace() as directory:
            assert workspace.is_safe(directory)
            assert directory.is_dir()
            (directory / "probe").write_text("ok")
        assert not directory.exists()

    def test_it_can_be_kept_for_diagnosis(self):
        with workspace.workspace(keep=True) as directory:
            pass
        assert directory.exists()
        workspace._remove(directory)


class TestConfigGeneration:
    """beso reads its settings with exec(), so this generates Python source."""

    def render(self, **overrides):
        arguments = {
            "settings": TopologySettings(volume_fraction=0.4, minimum_feature_size_mm=6.0),
            "material": STEEL,
            "solver_executable": "/usr/bin/ccx",
            "working_directory": "/tmp/topo-x",
            "deck_name": "part.inp",
        }
        arguments.update(overrides)
        return config.render_config(**arguments)

    def test_a_windows_path_survives_being_written_into_python(self):
        """The reason every value goes through repr().

        'C:\\Users\\Tom' pasted into Python source becomes a string with a tab
        and a form feed in it. repr() escapes it properly.
        """
        text = self.render(solver_executable=r"C:\Program Files\ccx\ccx.exe")
        namespace: dict[str, object] = {
            "domain_optimized": {},
            "domain_density": {},
            "domain_material": {},
            "domain_FI": {},
            "domain_same_state": {},
        }
        exec(compile(text, "beso_conf.py", "exec"), namespace)
        assert namespace["path_calculix"] == r"C:\Program Files\ccx\ccx.exe"

    def test_it_is_valid_python_that_sets_what_beso_reads(self):
        namespace: dict[str, object] = {
            "domain_optimized": {},
            "domain_density": {},
            "domain_material": {},
            "domain_FI": {},
            "domain_same_state": {},
        }
        exec(compile(self.render(), "beso_conf.py", "exec"), namespace)

        assert namespace["mass_goal_ratio"] == 0.4
        assert namespace["file_name"] == "part.inp"
        assert namespace["optimization_base"] == "stiffness"
        assert namespace["filter_list"] == [["simple", 3.0]]

    def test_the_void_material_is_soft_but_not_zero(self):
        """A zero-stiffness element makes the solver's matrix singular."""
        namespace: dict[str, object] = {
            "domain_optimized": {},
            "domain_density": {},
            "domain_material": {},
            "domain_FI": {},
            "domain_same_state": {},
        }
        exec(compile(self.render(), "beso_conf.py", "exec"), namespace)
        void, solid = namespace["domain_material"]["all_available"]  # type: ignore[index]
        assert "0.21" in void  # 210000 * 1e-6
        assert "210000" in solid
        assert namespace["domain_density"]["all_available"][0] > 0  # type: ignore[index]


class TestBucklingIsRefused:
    """The safety-critical one.

    CalculiX can silently report a buckling factor about nine times too high,
    in the unsafe direction. OpenOptima corrects this by scaling the reference
    load; beso has no such correction. An optimiser acting on that number would
    drive the design straight at the failure it was told to avoid.
    """

    def test_the_buckling_objective_is_not_offered(self):
        assert "buckling" not in config.OBJECTIVES

    def test_asking_for_it_is_refused_with_the_reason(self):
        with pytest.raises(config.UnsupportedObjective) as caught:
            config.objective_for("buckling")
        message = str(caught.value)
        assert "nine times too high" in message
        assert "unsafe direction" in message

    def test_an_unknown_objective_is_refused(self):
        with pytest.raises(config.UnsupportedObjective, match="unknown topology objective"):
            config.objective_for("magnetism")

    def test_failure_index_needs_an_allowable_stress(self):
        """Allowable stress is a design decision this software will not infer."""
        with pytest.raises(config.UnsupportedObjective, match="allowable stress"):
            config.render_config(
                settings=TopologySettings(),
                material=STEEL,
                solver_executable="ccx",
                working_directory="/tmp/x",
                deck_name="part.inp",
                objective="failure_index",
            )


class TestRefusingAnUnfinishedRun:
    """A run that hits its round limit still writes a shape. It is not a result."""

    def test_stopping_short_of_the_target_is_refused(self):
        settings = TopologySettings(volume_fraction=0.4, evolution_rate=0.03)
        message = runner._check_target_reached([1.0, 0.9, 0.79], settings, rounds=15)
        assert message is not None
        assert "79%" in message and "40%" in message
        assert "not a finished" in message

    def test_it_suggests_how_many_rounds_are_needed(self):
        settings = TopologySettings(volume_fraction=0.4, evolution_rate=0.03)
        message = runner._check_target_reached([1.0, 0.79], settings, rounds=15)
        assert message is not None
        # (1 - 0.4) / 0.015 + 25 = 65
        assert "65 rounds" in message

    def test_reaching_the_target_passes(self):
        settings = TopologySettings(volume_fraction=0.4, evolution_rate=0.03)
        assert runner._check_target_reached([1.0, 0.6, 0.398], settings, rounds=70) is None

    def test_a_small_overshoot_is_accepted(self):
        """Material comes away in steps, so the last one rarely lands exactly."""
        settings = TopologySettings(volume_fraction=0.4, evolution_rate=0.03)
        assert runner._check_target_reached([1.0, 0.41], settings, rounds=70) is None

    def test_no_history_makes_no_claim(self):
        settings = TopologySettings(volume_fraction=0.4)
        assert runner._check_target_reached([], settings, rounds=0) is None


class TestClassifyingFailures:
    def test_a_missing_solver_is_an_error_not_a_bad_design(self):
        with pytest.raises(EvaluationFailure) as caught:
            runner._classify(0, "ERROR: There might be invalid path_calculix.", ())
        assert caught.value.code == FailureCode.SOLVER_NOT_FOUND

    def test_a_coarse_mesh_says_what_to_change(self):
        with pytest.raises(EvaluationFailure) as caught:
            runner._classify(0, "ERROR: simple filter failed due to division by 0.", ())
        assert caught.value.code == FailureCode.MESH_QUALITY_FAILED
        assert "refine the mesh" in str(caught.value)

    def test_a_missing_matplotlib_says_how_to_fix_it(self):
        with pytest.raises(EvaluationFailure) as caught:
            runner._classify(1, "ModuleNotFoundError: No module named 'matplotlib'", ())
        assert "pip install matplotlib" in str(caught.value)

    def test_producing_no_mesh_is_refused(self):
        with pytest.raises(EvaluationFailure) as caught:
            runner._classify(0, "finished quietly", ())
        assert caught.value.code == FailureCode.RESULT_PARSE_FAILED

    def test_a_clean_run_with_a_mesh_passes(self, tmp_path):
        mesh = tmp_path / "file010_state1.inp"
        mesh.write_text("*NODE\n")
        runner._classify(0, "total time: 0 h 0 min 6 s", (mesh,))


class TestSelectingTheResult:
    def test_only_the_final_round_is_returned(self, tmp_path):
        """Earlier rounds are shapes the optimiser had already moved on from."""
        for round_number in (3, 12, 70):
            for state in (0, 1):
                (tmp_path / f"file{round_number:03d}_state{state}.inp").write_text("*NODE\n")

        meshes = runner._collect_meshes(tmp_path)
        assert [p.name for p in meshes] == ["file070_state0.inp", "file070_state1.inp"]

    def test_the_solid_state_is_the_last_one(self, tmp_path):
        for state in (0, 1):
            (tmp_path / f"file070_state{state}.inp").write_text("*NODE\n")
        outcome = runner.TopologyOutcome(
            result_meshes=runner._collect_meshes(tmp_path),
            log="",
            iterations=70,
            output_directory=tmp_path,
            beso_commit="abc",
        )
        assert outcome.solid_mesh.name == "file070_state1.inp"

    def test_the_round_count_comes_from_the_file_name(self, tmp_path):
        assert runner._count_rounds((Path("file070_state1.inp"),)) == 70

    def test_no_meshes_gives_no_rounds(self):
        assert runner._count_rounds(()) == 0
