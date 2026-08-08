"""The desktop app's server and job runner.

These run without a browser and without a solver: they check routing, the
loopback-only binding, path traversal, and that the job runner reports state
honestly. The interface itself is exercised end to end in a real browser during
development; what is pinned here is the behaviour that would be a security or
correctness problem if it regressed.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openoptima.app.jobs import JobRunner
from openoptima.app.server import HOST, create_server, find_free_port

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="module")
def app():
    port = find_free_port()
    server = create_server(EXAMPLES.parent, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{port}"
    server.shutdown()


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return response.status, json.loads(response.read())


def post(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.status, json.loads(response.read())


class TestBinding:
    def test_only_listens_on_loopback(self):
        """A desktop app must not be reachable from the network."""
        assert HOST == "127.0.0.1"

    def test_free_port_is_actually_free(self):
        port = find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((HOST, port))  # would raise if it were in use

    def test_falls_back_when_the_preferred_port_is_taken(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind((HOST, 0))
            taken = held.getsockname()[1]
            assert find_free_port(taken) != taken


class TestServing:
    def test_index_is_served(self, app):
        with urllib.request.urlopen(app + "/", timeout=30) as response:
            body = response.read().decode()
        assert response.status == 200
        assert "OpenOptima" in body

    def test_status_reports_the_environment(self, app):
        status, data = get(app, "/api/status")
        assert status == 200
        assert "solver_available" in data
        assert data["cores"] >= 1
        assert any(t["name"] == "l_bracket" for t in data["templates"])

    def test_projects_are_discovered_without_duplicates(self, app):
        _status, data = get(app, "/api/projects")
        paths = [p["path"] for p in data["projects"]]
        assert paths, "the bundled examples should be found"
        assert len(paths) == len(set(paths)), "each project must be listed once"

    def test_opening_a_project_describes_it_in_plain_terms(self, app):
        _status, data = post(
            app, "/api/open", {"path": str(EXAMPLES / "l_bracket" / "project.yaml")}
        )
        assert data["name"]
        assert [v["id"] for v in data["variables"]]
        assert data["material"]["basis"], "the allowable-stress basis must be shown"
        assert "buckling" in data

    def test_a_missing_project_is_a_clear_error_not_a_crash(self, app):
        with pytest.raises(urllib.error.HTTPError) as info:
            post(app, "/api/open", {"path": "/nowhere/project.yaml"})
        assert info.value.code == 400
        assert "no project file" in json.loads(info.value.read())["error"]

    def test_unknown_routes_return_404(self, app):
        with pytest.raises(urllib.error.HTTPError) as info:
            get(app, "/api/nonsense")
        assert info.value.code == 404


class TestVariableOverrides:
    """Editing a variable's range in the browser, without touching the YAML."""

    PROJECT = str(EXAMPLES / "l_bracket" / "project.yaml")

    def test_a_range_can_be_narrowed_for_this_run_only(self, app):
        _status, data = post(
            app,
            "/api/open",
            {
                "path": self.PROJECT,
                "variable_overrides": {"thickness_h": {"minimum": 12.0, "maximum": 18.0}},
            },
        )
        thickness_h = next(v for v in data["variables"] if v["id"] == "thickness_h")
        assert (thickness_h["minimum"], thickness_h["maximum"]) == (12.0, 18.0)
        # Other variables, and the file on disk, are untouched.
        thickness_v = next(v for v in data["variables"] if v["id"] == "thickness_v")
        assert thickness_v["minimum"] == 5.0

    def test_a_default_outside_the_new_range_is_shown_clamped(self, app):
        """The project's own default (19 mm) can land outside an edited range.

        `doctor` and a real run both clamp it silently (`DesignVariable.clamp`,
        used by `DesignSpace.decode`), so the page must show the value that
        will actually be evaluated -- not the stale one from the file, which
        would otherwise display a "default" above the "maximum" next to it.
        """
        _status, data = post(
            app,
            "/api/open",
            {
                "path": self.PROJECT,
                "variable_overrides": {"thickness_h": {"minimum": 12.0, "maximum": 18.0}},
            },
        )
        thickness_h = next(v for v in data["variables"] if v["id"] == "thickness_h")
        assert thickness_h["default"] == 18.0

    def test_an_unknown_variable_is_a_clear_error_not_a_crash(self, app):
        with pytest.raises(urllib.error.HTTPError) as info:
            post(
                app,
                "/api/open",
                {
                    "path": self.PROJECT,
                    "variable_overrides": {"not_a_variable": {"minimum": 1, "maximum": 2}},
                },
            )
        assert info.value.code == 400
        assert "not_a_variable" in json.loads(info.value.read())["error"]

    def test_an_inverted_range_is_a_clear_error_not_a_crash(self, app):
        with pytest.raises(urllib.error.HTTPError) as info:
            post(
                app,
                "/api/open",
                {
                    "path": self.PROJECT,
                    "variable_overrides": {"thickness_h": {"minimum": 18, "maximum": 12}},
                },
            )
        assert info.value.code == 400
        assert "above its maximum" in json.loads(info.value.read())["error"]

    def test_doctor_runs_against_the_overridden_range(self, app):
        pytest.importorskip("gmsh")
        _status, data = post(
            app,
            "/api/doctor",
            {
                "path": self.PROJECT,
                "variable_overrides": {"thickness_h": {"minimum": 12.0, "maximum": 18.0}},
            },
        )
        smallest = next(p for p in data["probes"] if p["label"] == "smallest")
        assert smallest["design"]["thickness_h"] == 12.0


class TestStaticFileSafety:
    """A crafted path must not read arbitrary files off the disk."""

    @pytest.mark.parametrize(
        "attack",
        [
            "/static/../../../../etc/passwd",
            "/static/../server.py",
            "/static/..%2f..%2fserver.py",
        ],
    )
    def test_traversal_is_refused(self, app, attack):
        with pytest.raises(urllib.error.HTTPError) as info:
            urllib.request.urlopen(app + attack, timeout=30)
        assert info.value.code == 404

    def test_legitimate_assets_are_served(self, app):
        for asset in ("/static/app.js", "/static/style.css"):
            with urllib.request.urlopen(app + asset, timeout=30) as response:
                assert response.status == 200
                assert response.read()


class TestJobRunner:
    def test_starts_idle(self):
        assert not JobRunner().busy

    def test_unknown_job_is_none(self):
        assert JobRunner().get("nope") is None

    def test_a_job_serialises_for_the_page(self):
        from openoptima.app.jobs import Job

        job = Job(id="job1", kind="optimise", project_path="p", budget=10)
        payload = job.to_dict()
        assert payload["state"] == "running"
        assert payload["evaluated"] == 0
        assert payload["budget"] == 10

    def test_cancelling_is_visible_to_the_worker(self):
        from openoptima.app.jobs import Job

        job = Job(id="job1", kind="doe", project_path="p")
        assert not job.cancelled
        job.cancel()
        assert job.cancelled

    def test_progress_is_capped_so_a_long_run_does_not_bloat_responses(self):
        from openoptima.app.jobs import Job

        job = Job(id="job1", kind="doe", project_path="p")
        job.progress = [{"n": i} for i in range(1000)]
        assert len(job.to_dict()["progress"]) == 200


class TestGmshFromAWorkerThread:
    """gmsh must be usable from a server thread.

    ``gmsh.initialize()`` installs a SIGINT handler, and Python refuses to do
    that off the main thread. Since the app serves every request on a worker,
    the very first geometry operation after launch used to fail with
    "signal only works in main thread of the main interpreter" — and then, because
    gmsh was left partly initialised, every later call worked. That shape made it
    look intermittent when it was in fact guaranteed on the first use.
    """

    def test_geometry_can_be_built_on_a_worker_thread(self):
        pytest.importorskip("gmsh")
        import threading

        from openoptima.domain.project import GeometryDefinition
        from openoptima.domain.variables import DesignSpace, DesignVariable
        from openoptima.geometry.occ.provider import OccGeometryProvider

        outcome: dict[str, object] = {}

        def build() -> None:
            import tempfile

            provider = OccGeometryProvider(
                GeometryDefinition(
                    provider="occ",
                    template="cantilever_box",
                    parameters={"length": 40.0, "width": 10.0, "height": 10.0},
                )
            )
            space = DesignSpace(
                (DesignVariable(id="length", minimum=40.0, maximum=40.0, default=40.0),)
            )
            try:
                with tempfile.TemporaryDirectory() as scratch:
                    artifact = provider.build(space.defaults(), Path(scratch))
                    outcome["volume"] = artifact.volume
            except Exception as exc:
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=build)
        thread.start()
        thread.join(timeout=300)

        assert "error" not in outcome, outcome.get("error")
        assert outcome["volume"] == pytest.approx(40.0 * 10.0 * 10.0, rel=1e-6)
