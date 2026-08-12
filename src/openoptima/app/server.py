"""The desktop app's local web server.

Deliberately built on the standard library. FastAPI would be more comfortable
to write, but every extra dependency is another thing that has to be bundled
correctly into a Windows executable and another way the frozen build can fail
on a machine nobody can debug. ``http.server`` has no such risk, and for one
local user on one machine it is entirely adequate.

The server binds to loopback only. This is a desktop application that happens
to render in a browser, not a web service, and it must never be reachable from
the network.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import forget_solver, remember_solver
from ..domain.failures import EvaluationFailure
from ..domain.model import SolverSpecification
from ..domain.orthotropic import OrthotropicMaterial
from ..domain.variables import DesignSpace, VariableType
from ..evaluation.evaluator import default_job_count
from ..evaluation.runspace import tool_versions
from ..geometry import create_provider
from ..geometry.occ.templates import available_templates
from ..schema.loader import ProjectLoadError, load_project
from ..solvers import create_solver
from ..solvers.calculix.runner import verify_executable
from .faces import FaceView, build_view, describe_selection
from .jobs import JobRunner
from .solver_setup import BackgroundInstall, SolverInstallError, solver_status

STATIC_ROOT = Path(__file__).parent / "static"

#: Loopback only. Never bind to 0.0.0.0 -- see the module docstring.
HOST = "127.0.0.1"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def find_free_port(preferred: int = 8731) -> int:
    """Take the preferred port if it is free, otherwise let the OS choose."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, candidate))
                return int(probe.getsockname()[1])
            except OSError:
                continue
    raise RuntimeError("could not find a free port to listen on")


class AppState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runner = JobRunner()
        self.solver_install = BackgroundInstall()
        #: When the page was last heard from, and whether it has ever been
        #: heard from at all. The launcher shuts the application down when the
        #: page goes quiet, so this is how closing the window stops the server.
        #: See `launcher._supervise` for why the browser process itself cannot
        #: be used for that.
        self.last_seen = 0.0
        self.ever_seen = False
        #: The most recently built shape for face-picking, if any. Replaced
        #: wholesale on every build, never mutated -- a click always resolves
        #: against exactly one gmsh build, never a mix of two.
        self.face_view: FaceView | None = None
        self._face_generation = 0

    def touch(self) -> None:
        self.last_seen = time.monotonic()
        self.ever_seen = True


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenOptima"

    def __init__(self, *args: Any, state: AppState, **kwargs: Any) -> None:
        self.state = state
        super().__init__(*args, **kwargs)

    # -- plumbing ------------------------------------------------------------
    def log_message(self, *_args: Any) -> None:
        """Silence the default per-request logging; the app has its own output."""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # The page may navigate away mid-response; that is not an error.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=_encode).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status=status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routing -------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        # Any request at all proves the page is still there.
        self.state.touch()
        if path == "/api/alive":
            return self._json({"ok": True})
        if path == "/" or path == "/index.html":
            return self._static("index.html")
        if path == "/favicon.ico":
            return self._static("favicon.svg")
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path == "/api/status":
            return self._json(self._status())
        if path == "/api/projects":
            return self._json({"projects": self._discover_projects()})
        if path.startswith("/api/job/"):
            job = self.state.runner.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self._error("no such job", 404)
            return self._json(job.to_dict())
        if path == "/api/current":
            job = self.state.runner.current()
            return self._json(job.to_dict() if job else {"state": "idle"})
        if path == "/api/solver":
            return self._json(solver_status())
        if path == "/api/solver/install":
            return self._json(self.state.solver_install.state())
        return self._error("not found", 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        self.state.touch()
        body = self._body()
        if path == "/api/open":
            return self._open(body)
        if path == "/api/doctor":
            return self._doctor(body)
        if path == "/api/faces/build":
            return self._faces_build(body)
        if path == "/api/faces/describe":
            return self._faces_describe(body)
        if path == "/api/run":
            return self._run(body)
        if path.startswith("/api/job/") and path.endswith("/stop"):
            job = self.state.runner.get(path.split("/")[3])
            if job is None:
                return self._error("no such job", 404)
            job.cancel()
            return self._json({"ok": True})
        if path == "/api/report":
            return self._report(body)
        if path == "/api/solver/locate":
            return self._locate_solver(body)
        if path == "/api/solver/install":
            return self._install_solver()
        if path == "/api/solver/forget":
            forget_solver()
            return self._json(solver_status())
        return self._error("not found", 404)

    # -- solver setup --------------------------------------------------------
    def _locate_solver(self, body: dict[str, Any]) -> None:
        raw = str(body.get("path", "")).strip().strip('"')
        if not raw:
            return self._error("choose the CalculiX program file first")
        # A folder is the likely mistake, so look inside it rather than
        # refusing: someone pointing at their CalculiX folder means the ccx in
        # it, and making them find the exact file is needless friction.
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            for relative in ("ccx.exe", "bin/ccx.exe", "ccx"):
                if (candidate / relative).is_file():
                    candidate = candidate / relative
                    break
        ok, message = verify_executable(candidate)
        if not ok:
            return self._error(message)
        remember_solver(candidate)
        return self._json(solver_status())

    def _install_solver(self) -> None:
        try:
            self.state.solver_install.start()
        except SolverInstallError as exc:
            return self._error(str(exc), 409)
        return self._json(self.state.solver_install.state())

    # -- static files --------------------------------------------------------
    def _static(self, relative: str) -> None:
        # Resolve and confirm the result is still inside the static directory,
        # so a crafted path cannot read arbitrary files off the disk.
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return self._error("not found", 404)
        if not candidate.is_file():
            return self._error("not found", 404)
        content_type = _CONTENT_TYPES.get(candidate.suffix, "application/octet-stream")
        self._send(200, candidate.read_bytes(), content_type)

    # -- endpoints -----------------------------------------------------------
    def _status(self) -> dict[str, Any]:
        versions = tool_versions()
        solver = create_solver(SolverSpecification(name="calculix"))
        available, message = solver.available()
        return {
            "solver_available": available,
            "solver_message": message,
            "versions": versions,
            "cores": default_job_count(),
            "templates": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": sorted(t.defaults),
                }
                for t in available_templates()
            ],
            "busy": self.state.runner.busy,
        }

    def _discover_projects(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        seen: set[Path] = set()
        for base in (self.state.root, self.state.root / "examples"):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("project.yaml")):
                # rglob from the root already reaches examples/, so the two
                # search roots overlap and every example would be listed twice.
                resolved = path.resolve()
                if resolved in seen or "openoptima_work" in path.parts:
                    continue
                seen.add(resolved)
                try:
                    project = load_project(path)
                    name, description = project.name, project.description
                except ProjectLoadError:
                    name, description = path.parent.name, "(could not be read)"
                found.append({"path": str(path), "name": name, "description": description.strip()})
        return found

    def _load(self, body: dict[str, Any]):
        path = Path(body.get("path", "")).expanduser()
        if not path.is_file():
            raise ProjectLoadError(f"no project file at {path}")
        project = load_project(path)
        return _with_variable_overrides(project, body.get("variable_overrides")), path

    def _open(self, body: dict[str, Any]) -> None:
        try:
            project, path = self._load(body)
        except ProjectLoadError as exc:
            return self._error(str(exc))
        return self._json(_describe(project, path))

    def _doctor(self, body: dict[str, Any]) -> None:
        try:
            project, path = self._load(body)
        except ProjectLoadError as exc:
            return self._error(str(exc))
        from .checks import run_doctor

        return self._json(run_doctor(project, path))

    def _run(self, body: dict[str, Any]) -> None:
        try:
            project, path = self._load(body)
        except ProjectLoadError as exc:
            return self._error(str(exc))
        kind = body.get("kind", "optimise")
        if kind not in ("doe", "optimise"):
            return self._error("kind must be 'doe' or 'optimise'")
        budget = body.get("budget")
        try:
            job = self.state.runner.start(project, path, kind, int(budget) if budget else None)
        except RuntimeError as exc:
            return self._error(str(exc), 409)
        return self._json(job.to_dict())

    def _faces_build(self, body: dict[str, Any]) -> None:
        try:
            project, path = self._load(body)
        except ProjectLoadError as exc:
            return self._error(str(exc))

        provider = create_provider(project.geometry, project.regions)
        if hasattr(provider, "root"):
            # Same reasoning as _doctor: a relative geometry.source is
            # written relative to the project file, not to wherever the app
            # process happens to be running from.
            provider.root = path.parent  # type: ignore[attr-defined]

        self.state._face_generation += 1
        generation = self.state._face_generation
        try:
            view, payload = build_view(project, path, provider, generation)
        except EvaluationFailure as exc:
            return self._error(f"could not build the part: {exc.message}")
        except Exception as exc:  # the part genuinely could not be built
            return self._error(f"could not build the part: {exc}")
        self.state.face_view = view
        return self._json(payload)

    def _faces_describe(self, body: dict[str, Any]) -> None:
        view = self.state.face_view
        if view is None:
            return self._error("no part has been built for picking yet", 409)
        generation = body.get("generation")
        if generation != view.generation:
            # The user reopened the part, resized it, or picked before the
            # first build finished: the tags in this request belong to a
            # shape that is no longer the current one. Resolving them anyway
            # would silently describe the wrong face.
            return self._error("this view is out of date; reload the part", 409)
        tags = body.get("tags")
        if not isinstance(tags, list) or not tags or not all(isinstance(t, int) for t in tags):
            return self._error("tags must be a non-empty list of face numbers")
        return self._json(describe_selection(view, tags))

    def _report(self, body: dict[str, Any]) -> None:
        path = Path(body.get("path", ""))
        if not path.is_file():
            return self._error("no report yet", 404)
        self._send(200, path.read_bytes(), "text/plain; charset=utf-8")


def _material_summary(material: Any) -> dict[str, Any]:
    """What the page shows about the material.

    A printed material has no single allowable stress. Reporting one anyway --
    by picking a direction, or by falling back to a default -- would put a
    number on the page that no part of the analysis actually used.
    """
    if isinstance(material, OrthotropicMaterial):
        strength = material.strength
        return {
            "name": material.name,
            "printed": True,
            "allowable_stress_mpa": None,
            "weakest_allowable_mpa": None if strength is None else strength.weakest,
            "basis": "unspecified" if strength is None else strength.basis,
        }
    return {
        "name": material.name,
        "printed": False,
        "allowable_stress_mpa": material.allowable_stress,
        "basis": material.allowable_stress_basis,
    }


def _describe(project, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": project.name,
        "description": project.description.strip(),
        "template": project.geometry.template,
        "variables": [
            {
                "id": v.id,
                "label": v.display_name,
                "minimum": v.minimum,
                "maximum": v.maximum,
                # Clamped: an edited range can leave the project's own default
                # outside it, and doctor/run silently clamp when that happens
                # (DesignSpace.decode -> DesignVariable.clamp) -- show the value
                # that will actually be used, not the stale one from the file.
                "default": v.clamp(v.effective_default()),
                "unit": v.unit,
            }
            for v in project.design_space
        ],
        "regions": [r.name for r in project.regions],
        "material": _material_summary(project.material),
        "load_cases": [{"id": lc.id, "description": lc.description} for lc in project.load_cases],
        "objectives": [
            {"metric": o.metric, "label": o.display_name, "direction": o.direction.value}
            for o in project.objectives
        ],
        "constraints": [c.describe() for c in project.constraints],
        "buckling": {
            "enabled": project.buckling.enabled,
            "slenderness_limit": project.buckling.slenderness_limit,
        },
        "budget": project.optimisation.algorithm.evaluation_budget,
    }


def _with_variable_overrides(project, raw: Any):
    """Apply edited numeric variable bounds to this run without rewriting YAML."""
    if raw is None:
        return project
    if not isinstance(raw, dict):
        raise ProjectLoadError("variable_overrides must be an object")
    unknown = set(raw) - set(project.design_space.ids)
    if unknown:
        raise ProjectLoadError(f"unknown design variable(s): {', '.join(sorted(unknown))}")
    variables = []
    for variable in project.design_space:
        change = raw.get(variable.id)
        if change is None:
            variables.append(variable)
            continue
        if variable.type not in (VariableType.CONTINUOUS, VariableType.INTEGER):
            raise ProjectLoadError(f"{variable.display_name} does not have a numeric range")
        if not isinstance(change, dict):
            raise ProjectLoadError(f"range for {variable.display_name} must be an object")
        try:
            minimum, maximum = float(change["minimum"]), float(change["maximum"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectLoadError(
                f"range for {variable.display_name} needs numeric limits"
            ) from exc
        if not all(value == value and abs(value) != float("inf") for value in (minimum, maximum)):
            raise ProjectLoadError(f"range for {variable.display_name} must be finite")
        if minimum > maximum:
            raise ProjectLoadError(f"minimum for {variable.display_name} is above its maximum")
        variables.append(replace(variable, minimum=minimum, maximum=maximum))
    return replace(project, design_space=DesignSpace(tuple(variables)))


def _encode(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and value != value:  # NaN is not valid JSON
        return None
    return str(value)


class AppServer(ThreadingHTTPServer):
    """The server, with the shared state hung off it.

    The launcher needs to see when the page was last heard from, to know
    whether the window is still open.
    """

    app_state: AppState


def create_server(root: Path, port: int) -> AppServer:
    state = AppState(root)
    handler = partial(Handler, state=state)
    server = AppServer((HOST, port), handler)
    server.daemon_threads = True
    server.app_state = state
    return server


def serve(root: Path, port: int, background: bool = False) -> ThreadingHTTPServer:
    server = create_server(root, port)
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    else:
        server.serve_forever()
    return server
