"""Cross-platform behaviour.

OpenOptima has to run on Windows as well as Linux, and the places the platform
leaks in are few but fatal: process control uses mechanisms that simply do not
exist on the other operating system, so a mistake here is an immediate crash
rather than a subtle wrong answer.

These tests run everywhere and check both branches regardless of the host, by
patching the platform flag. That matters because most contributors will only
ever run one of the two.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from openoptima.domain.model import SolverSpecification
from openoptima.solvers.calculix import runner


class TestProcessIsolation:
    """Each option raises on the platform it does not belong to."""

    def test_posix_uses_a_new_session(self, monkeypatch):
        monkeypatch.setattr(runner, "WINDOWS", False)
        kwargs = runner._process_isolation_kwargs()
        assert kwargs == {"start_new_session": True}

    def test_windows_uses_a_new_process_group(self, monkeypatch):
        monkeypatch.setattr(runner, "WINDOWS", True)
        # CREATE_NEW_PROCESS_GROUP only exists in the Windows build of
        # subprocess, so on other platforms supply it to prove the branch.
        if not hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
        kwargs = runner._process_isolation_kwargs()
        assert "creationflags" in kwargs
        assert "start_new_session" not in kwargs, (
            "start_new_session is POSIX-only and raises ValueError on Windows"
        )

    def test_the_two_branches_never_overlap(self, monkeypatch):
        monkeypatch.setattr(runner, "WINDOWS", False)
        posix = set(runner._process_isolation_kwargs())
        assert "creationflags" not in posix, "creationflags is not accepted by Popen on POSIX"


class TestExecutableDiscovery:
    def test_explicit_path_wins(self, tmp_path):
        fake = tmp_path / "ccx"
        fake.write_text("")
        found = runner.find_executable(SolverSpecification(executable=str(fake)))
        assert found == str(fake)

    def test_environment_variable_is_honoured(self, tmp_path, monkeypatch):
        fake = tmp_path / "ccx"
        fake.write_text("")
        monkeypatch.setenv("OPENOPTIMA_CCX", str(fake))
        assert runner.find_executable(SolverSpecification()) == str(fake)

    def test_explicit_setting_beats_the_environment(self, tmp_path, monkeypatch):
        chosen, other = tmp_path / "chosen", tmp_path / "other"
        chosen.write_text("")
        other.write_text("")
        monkeypatch.setenv("OPENOPTIMA_CCX", str(other))
        assert runner.find_executable(SolverSpecification(executable=str(chosen))) == str(chosen)

    def test_missing_solver_returns_none_rather_than_raising(self, monkeypatch):
        monkeypatch.delenv("OPENOPTIMA_CCX", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda *_a, **_k: None)
        monkeypatch.setattr(runner, "_bundled_search_paths", list)
        monkeypatch.setattr(runner, "_windows_search_paths", list)
        assert runner.find_executable(SolverSpecification()) is None

    def test_windows_install_locations_are_searched(self, tmp_path, monkeypatch):
        """The two common Windows distributions do not add themselves to PATH."""
        program_files = tmp_path / "Program Files"
        solver = program_files / "bConverged" / "CalculiX" / "bin" / "ccx.exe"
        solver.parent.mkdir(parents=True)
        solver.write_text("")

        monkeypatch.setattr(runner, "WINDOWS", True)
        monkeypatch.delenv("OPENOPTIMA_CCX", raising=False)
        monkeypatch.setenv("ProgramFiles", str(program_files))
        monkeypatch.setattr(runner.shutil, "which", lambda *_a, **_k: None)
        monkeypatch.setattr(runner, "_bundled_search_paths", list)

        assert runner.find_executable(SolverSpecification()) == str(solver)

    def test_prepomax_bundled_solver_is_found(self, tmp_path, monkeypatch):
        program_files = tmp_path / "Program Files"
        solver = program_files / "PrePoMax v2.1.0" / "Solver" / "ccx.exe"
        solver.parent.mkdir(parents=True)
        solver.write_text("")

        monkeypatch.setattr(runner, "WINDOWS", True)
        monkeypatch.delenv("OPENOPTIMA_CCX", raising=False)
        monkeypatch.setenv("ProgramFiles", str(program_files))
        monkeypatch.setattr(runner.shutil, "which", lambda *_a, **_k: None)
        monkeypatch.setattr(runner, "_bundled_search_paths", list)

        assert runner.find_executable(SolverSpecification()) == str(solver)


class TestInstallationHint:
    def test_windows_hint_names_the_windows_binary(self, monkeypatch):
        monkeypatch.setattr(runner, "WINDOWS", True)
        hint = runner.installation_hint()
        assert "ccx.exe" in hint
        assert "apt install" not in hint, "do not give Linux advice on Windows"

    def test_posix_hint_names_the_package_managers(self, monkeypatch):
        monkeypatch.setattr(runner, "WINDOWS", False)
        hint = runner.installation_hint()
        assert "apt install" in hint or "brew install" in hint

    def test_both_mention_the_override(self, monkeypatch):
        for windows in (True, False):
            monkeypatch.setattr(runner, "WINDOWS", windows)
            assert "OPENOPTIMA_CCX" in runner.installation_hint()


class TestPathHandling:
    """Everything user-facing must survive a Windows path.

    'C:\\Users\\Someone\\My Documents' has both backslashes and a space, and a
    project living under it must not break the deck, the run directories or the
    solver invocation.
    """

    def test_project_names_with_spaces_are_made_solver_safe(self):
        from openoptima.solvers.calculix.deck import _safe, _set_name

        assert " " not in _safe("Aluminium 6082-T6")
        assert "," not in _safe("a,b")
        assert " " not in _set_name("mounting face")

    def test_paths_are_never_interpolated_into_a_shell_string(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "shell=True" not in source
        assert 'f"{executable}' not in source
