"""Getting a stress solver onto the user's machine, and remembering where it is.

Three things are covered here: the small settings file that remembers which
solver the user picked (``config.py``), the checks that catch a solver
executable that will not actually run (``runner.verify_executable`` and
``find_executable``), and the download-and-unpack path that fetches CalculiX
for someone who does not have it (``app/solver_setup.py``), plus the window
launcher (``app/launcher.py``).

None of this may touch the network, a real solver or the developer's own
settings file, so every test that reads or writes settings redirects them
first with the ``OPENOPTIMA_CONFIG_DIR`` environment variable, and every test
that downloads anything instead builds a small zip file by hand or replaces
the network call with a stand-in.
"""

from __future__ import annotations

import re
import threading
import time
import zipfile
from pathlib import Path

import pytest

from openoptima import config
from openoptima.app import launcher, solver_setup
from openoptima.domain.model import SolverSpecification
from openoptima.solvers.calculix import runner


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point every settings read and write at pytest's own throwaway folder.

    Without this, a call to :func:`config.remember_solver` or similar would
    read or overwrite the developer's real settings file -- exactly the
    accident this fixture exists to make impossible.
    """
    monkeypatch.setenv(config._DIRECTORY_ENV_VAR, str(tmp_path))
    return tmp_path


class TestConfig:
    def test_settings_directory_honours_the_override(self, config_dir):
        assert config.settings_directory() == config_dir

    def test_remember_then_recall_round_trips(self, config_dir, tmp_path):
        solver = tmp_path / "ccx.exe"
        solver.write_text("solver")
        config.remember_solver(solver)
        assert config.remembered_solver() == str(solver)

    def test_a_remembered_solver_that_no_longer_exists_reads_as_none(self, config_dir, tmp_path):
        """A stale entry must fail here, not deep inside a run.

        The usual cause is an uninstall or a moved folder. Reporting the old
        path as though it still worked would send the user into a run that
        fails much later, in a place the setup screen cannot warn about.
        """
        solver = tmp_path / "ccx.exe"
        solver.write_text("solver")
        config.remember_solver(solver)
        solver.unlink()
        assert config.remembered_solver() is None

    def test_invalid_json_reads_as_empty_not_a_crash(self, config_dir):
        """A hand-edited settings file must not stop the app from starting."""
        path = config.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not valid json", encoding="utf-8")
        assert config.load_settings() == {}

    def test_valid_json_that_is_not_an_object_reads_as_empty(self, config_dir):
        path = config.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2]", encoding="utf-8")
        assert config.load_settings() == {}

    def test_save_settings_leaves_no_temporary_file_behind(self, config_dir):
        """The write goes to a neighbouring ``.tmp`` file and is moved into
        place, so an interrupted write cannot leave a half-written file --
        but only if the temporary file is actually cleaned up on success."""
        config.save_settings({"calculix_executable": "x"})
        leftovers = list(config_dir.glob("*.tmp"))
        assert leftovers == []
        assert config.settings_path().exists()

    def test_forget_solver_removes_the_key_and_is_idempotent(self, config_dir, tmp_path):
        solver = tmp_path / "ccx.exe"
        solver.write_text("solver")
        config.remember_solver(solver)
        config.forget_solver()
        assert config.remembered_solver() is None
        config.forget_solver()  # calling it again must not raise


class TestVerifyExecutable:
    """``verify_executable`` is what stops a bad path being stored at all."""

    def test_a_path_that_does_not_exist_is_reported(self, tmp_path):
        missing = tmp_path / "nope.exe"
        ok, message = runner.verify_executable(missing)
        assert ok is False
        assert str(missing) in message

    def test_a_folder_is_not_the_solver(self, tmp_path):
        ok, message = runner.verify_executable(tmp_path)
        assert ok is False
        assert "folder" in message

    def test_the_viewer_is_identified_as_the_wrong_program(self, tmp_path):
        viewer = tmp_path / "cgx.exe"
        viewer.write_bytes(b"")
        ok, message = runner.verify_executable(viewer)
        assert ok is False
        assert "viewer" in message

    def test_a_file_that_will_not_run_is_reported(self, tmp_path):
        """A text file named like the solver must not be accepted as one.

        On Windows, trying to run it usually raises ``OSError`` deep inside
        ``solver_version``, which is caught there and turned into an empty
        version string. That is exercised for real rather than mocked, so if
        it ever proves flaky on a particular platform, ``ok is False`` is the
        one assertion to keep.
        """
        fake = tmp_path / "ccx.exe"
        fake.write_text("this is a text file, not a real program")
        ok, message = runner.verify_executable(fake)
        assert ok is False
        assert "would not run" in message


class TestFindExecutable:
    """The search order, as documented on :func:`runner.find_executable`."""

    def test_an_explicit_executable_wins_over_a_remembered_solver(self, config_dir, tmp_path):
        chosen = tmp_path / "chosen.exe"
        chosen.write_text("")
        remembered = tmp_path / "remembered.exe"
        remembered.write_text("")
        config.remember_solver(remembered)

        found = runner.find_executable(SolverSpecification(executable=str(chosen)))
        assert found == str(chosen)

    def test_the_environment_variable_wins_over_a_remembered_solver(
        self, config_dir, tmp_path, monkeypatch
    ):
        from_env = tmp_path / "from_env.exe"
        from_env.write_text("")
        remembered = tmp_path / "remembered.exe"
        remembered.write_text("")
        config.remember_solver(remembered)
        monkeypatch.setenv("OPENOPTIMA_CCX", str(from_env))

        found = runner.find_executable(SolverSpecification())
        assert found == str(from_env)

    def test_a_remembered_solver_is_used_when_nothing_else_is_set(
        self, config_dir, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("OPENOPTIMA_CCX", raising=False)
        remembered = tmp_path / "remembered.exe"
        remembered.write_text("")
        config.remember_solver(remembered)

        found = runner.find_executable(SolverSpecification())
        assert found == str(remembered)


class TestPinnedDownloadConstants:
    """These three constants are a security boundary, not a convenience.

    Pinning to one exact commit and one exact checksum means an upstream
    change cannot silently alter what gets downloaded and run on somebody's
    machine. Changing any of them is meant to be a deliberate, by-hand act,
    so these tests exist to catch an accidental edit -- for example a
    find-and-replace that turns the commit hash back into a branch name.
    """

    def test_download_url_is_https_and_pinned_to_a_commit_not_a_branch(self):
        url = solver_setup.DOWNLOAD_URL
        assert url.startswith("https://")
        assert "/master/" not in url
        assert "/main/" not in url
        # A commit hash is 40 hex characters; a branch name is not.
        assert re.search(r"/[0-9a-f]{40}/", url), (
            "the URL must contain a 40-character commit hash, not a branch name"
        )

    def test_checksum_is_a_full_lower_case_sha256(self):
        assert re.fullmatch(r"[0-9a-f]{64}", solver_setup.DOWNLOAD_SHA256)

    def test_the_licence_travels_with_the_program(self):
        assert "LICENSE.txt" in solver_setup._WANTED

    def test_the_viewer_is_not_shipped(self):
        assert "cgx.exe" not in solver_setup._WANTED


class TestInstall:
    def test_install_refuses_on_an_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(solver_setup, "is_supported", lambda: False)
        with pytest.raises(solver_setup.SolverInstallError):
            solver_setup.install()


class TestExtract:
    """``_extract`` is the one place a downloaded archive touches disk."""

    def _build_archive(self, path: Path, *, omit: str | None = None, extra: str | None = None):
        with zipfile.ZipFile(path, "w") as bundle:
            for name in solver_setup._WANTED:
                if name == omit:
                    continue
                bundle.writestr(solver_setup._ARCHIVE_PREFIX + name, b"stub contents")
            if extra:
                bundle.writestr(extra, b"hostile contents")

    def test_a_missing_wanted_file_is_named_in_the_error(self, tmp_path):
        archive = tmp_path / "calculix.zip"
        self._build_archive(archive, omit="LICENSE.txt")
        target = tmp_path / "extracted"

        with pytest.raises(solver_setup.SolverInstallError) as info:
            solver_setup._extract(archive, target, None)
        assert "LICENSE.txt" in str(info.value)

    def test_a_hostile_entry_cannot_escape_the_target_directory(self, tmp_path):
        """Zip-slip regression test.

        The destination filename for every file ``_extract`` writes is one of
        the names in ``_WANTED`` -- chosen by OpenOptima -- and is never taken
        from the archive entry itself. That is what makes it safe to open an
        archive containing an entry like ``"../../escaped.txt"``: nothing in
        ``_extract`` ever asks the zip file what to call anything on disk, so
        a path-traversal entry just sits in the archive unopened.
        """
        archive = tmp_path / "calculix.zip"
        self._build_archive(archive, extra="../../escaped.txt")
        # Nested, so an escape would land somewhere under tmp_path that is
        # easy to detect with rglob below.
        target = tmp_path / "sub" / "install"

        solver_setup._extract(archive, target, None)

        for name in solver_setup._WANTED:
            assert (target / name).is_file()
        assert not list(tmp_path.rglob("escaped.txt")), (
            "the hostile entry must never be written anywhere"
        )


class _ThreadCapture:
    """Stands in for the ``threading`` name inside ``solver_setup`` only.

    ``BackgroundInstall.start()`` creates and starts its own worker thread
    without handing it back, so a test has no direct way to join it. Patching
    the module's own reference to ``threading`` (rather than the real
    ``threading`` module, which every other part of the process also relies
    on) records the thread object so the test can join it afterwards and
    leave nothing running once it finishes.
    """

    def __init__(self, real):
        self._real = real
        self.created: list[threading.Thread] = []

    def Thread(self, *args, **kwargs):
        thread = self._real.Thread(*args, **kwargs)
        self.created.append(thread)
        return thread

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestBackgroundInstall:
    def test_a_fresh_instance_is_idle(self):
        background = solver_setup.BackgroundInstall()
        assert background.state()["state"] == "idle"
        assert not background.running

    def test_starting_twice_is_rejected(self, monkeypatch):
        capture = _ThreadCapture(threading)
        monkeypatch.setattr(solver_setup, "threading", capture)

        release = threading.Event()

        def blocking_install(progress=None):
            release.wait(timeout=5)
            return solver_setup.InstalledSolver(
                executable=Path("ccx.exe"), version="0.0", directory=Path(".")
            )

        monkeypatch.setattr(solver_setup, "install", blocking_install)

        background = solver_setup.BackgroundInstall()
        background.start()
        try:
            # The state is set to "running" synchronously inside start(),
            # before the worker thread is even spawned, so this is
            # deterministic rather than a race against the blocked thread.
            with pytest.raises(solver_setup.SolverInstallError):
                background.start()
        finally:
            release.set()
            assert capture.created, "start() should have created exactly one thread"
            capture.created[0].join(timeout=5)

    def test_a_failed_install_is_reported_as_an_error(self, monkeypatch):
        def failing_install(progress=None):
            raise solver_setup.SolverInstallError("boom")

        monkeypatch.setattr(solver_setup, "install", failing_install)

        background = solver_setup.BackgroundInstall()
        background.start()

        deadline = time.monotonic() + 2.0
        state = background.state()
        while state["state"] == "running" and time.monotonic() < deadline:
            state = background.state()
        assert state["state"] == "error"
        assert "boom" in state["message"]

    def test_a_successful_install_names_the_version(self, monkeypatch, tmp_path):
        installed = solver_setup.InstalledSolver(
            executable=tmp_path / "ccx.exe", version="2.23", directory=tmp_path
        )

        def succeeding_install(progress=None):
            return installed

        monkeypatch.setattr(solver_setup, "install", succeeding_install)

        background = solver_setup.BackgroundInstall()
        background.start()

        deadline = time.monotonic() + 2.0
        state = background.state()
        while state["state"] == "running" and time.monotonic() < deadline:
            state = background.state()
        assert state["state"] == "done"
        assert "2.23" in state["message"]


class TestProfileDirectory:
    def test_sits_under_the_redirected_base_and_is_created(self, tmp_path, monkeypatch):
        if launcher.os.name == "nt":
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        else:
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

        profile = launcher._profile_directory()
        assert tmp_path in profile.parents
        assert profile.is_dir()


class TestOpenWindow:
    def test_returns_none_when_no_browser_is_found(self, monkeypatch):
        monkeypatch.setattr(launcher, "_window_browser", lambda: None)
        assert launcher.open_window("http://127.0.0.1:1234/") is None

    def test_the_browser_is_launched_with_an_argument_list_not_a_shell_string(
        self, tmp_path, monkeypatch
    ):
        """Guards the project's "never pass a shell string" rule.

        A path under the user's profile can contain a space, so the browser
        must always be started from an argument list, never a single string
        handed to a shell.
        """
        if launcher.os.name == "nt":
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        else:
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setattr(launcher, "_window_browser", lambda: str(tmp_path / "browser.exe"))

        captured: dict[str, object] = {}

        def fake_popen(command, **kwargs):
            captured["command"] = command
            return object()

        monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

        url = "http://127.0.0.1:5000/"
        result = launcher.open_window(url)

        assert result is not None
        command = captured["command"]
        assert isinstance(command, list)
        assert f"--app={url}" in command
        assert any(part.startswith("--user-data-dir=") for part in command)
