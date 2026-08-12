"""The 3D face-picking endpoints: building a view, and describing a click.

Real HTTP requests against a real running server, same pattern as
``test_app.py``. What matters here is not the geometry -- that is proved in
``tests/integration/test_tessellate.py`` and
``tests/integration/test_describe_across_range.py`` -- but that a stale or
malformed click is refused rather than silently resolved against the wrong
shape, since a click is exactly the kind of input this project cannot afford
to trust blindly.

**The server holds one view at a time**, and the ``app`` fixture below is a
single server shared by every test in this module for speed -- so a fresh
view is built at the *start* of every test that needs the current one to be
the bracket, never assumed to still be current from an earlier test. That
is not a style preference: the first version of this file cached one build
and handed its generation number to later tests, and the server's own
staleness check correctly refused it once an unrelated test had built a
different project in between. The fix is the same discipline the server
itself enforces on a real click.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openoptima.app.server import HOST, create_server, find_free_port

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("gmsh")
    port = find_free_port()
    server = create_server(EXAMPLES.parent, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{port}"
    server.shutdown()


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=60) as response:
        return response.status, json.loads(response.read())


def post(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.status, json.loads(response.read())


def error_body(exc: urllib.error.HTTPError) -> dict:
    return json.loads(exc.read())


def build_bracket(app: str) -> dict:
    """A fresh build of the L-bracket, current as of this call."""
    _status, data = post(
        app, "/api/faces/build", {"path": str(EXAMPLES / "l_bracket" / "project.yaml")}
    )
    return data


class TestBuildingAView:
    def test_it_returns_a_mesh_with_matching_array_lengths(self, app):
        mesh = build_bracket(app)["mesh"]
        assert mesh["triangle_count"] > 100
        assert len(mesh["positions"]) == mesh["triangle_count"] * 9
        assert len(mesh["face_tags"]) == mesh["triangle_count"]

    def test_it_lists_every_face_with_its_kind_and_area(self, app):
        faces = build_bracket(app)["faces"]
        assert len(faces) == 11  # the L-bracket has 11 faces at its defaults
        assert {f["surface_type"] for f in faces} == {"plane", "cylinder"}
        assert all(f["area_mm2"] > 0 for f in faces)

    def test_it_checks_descriptions_against_the_extremes(self, app):
        """The L-bracket has design variables, so there is a range to check
        descriptions against -- this is what makes the fillet/bolt-hole
        defects in describe.py catchable at all."""
        view = build_bracket(app)
        assert view["shape_can_change"] is True
        assert view["checked_against"] == 2

    def test_a_part_with_no_variables_has_only_one_shape(self, app):
        _status, data = post(
            app, "/api/faces/build", {"path": str(EXAMPLES / "imported_bracket" / "project.yaml")}
        )
        assert data["shape_can_change"] is False
        assert data["checked_against"] == 0

    def test_an_import_with_a_feature_shows_the_face_the_feature_made(self, app):
        """The hazard this piece exists to close.

        A description is written from what the viewer shows. If the viewer
        showed the raw import, every description would be written against a
        shape that exists at no point in the design range -- and the face the
        fillet creates could not be picked at all, because it would not be
        there. So the viewer builds through the features, and the extremes it
        checks against are featured shapes too.
        """
        _status, data = post(
            app,
            "/api/faces/build",
            {"path": str(EXAMPLES / "imported_bracket_fillet" / "project.yaml")},
        )
        # 11 faces on the bare import, 12 once the corner is rounded.
        assert len(data["faces"]) == 12
        assert data["shape_can_change"] is True
        assert data["checked_against"] == 2

        rounds = sorted(
            (f for f in data["faces"] if f["surface_type"] == "cylinder"),
            key=lambda f: f["area_mm2"],
        )
        # The added corner at the 6 mm default: a quarter cylinder 60 mm long,
        # pi/2 * 6 * 60 = 565.5 mm2. Neither bolt hole nor the internal fillet
        # is that size, so finding it proves the feature reached the viewer.
        assert any(abs(f["area_mm2"] - 565.49) < 0.5 for f in rounds)

    def test_a_missing_project_is_a_clear_error(self, app):
        with pytest.raises(urllib.error.HTTPError) as info:
            post(app, "/api/faces/build", {"path": "/nowhere/project.yaml"})
        assert info.value.code == 400
        assert "no project file" in error_body(info.value)["error"]

    def test_each_build_advances_the_generation(self, app):
        first = build_bracket(app)
        second = build_bracket(app)
        assert second["generation"] > first["generation"]


class TestDescribingAClick:
    @staticmethod
    def _a_face_tag(view, surface_type="plane"):
        return next(f["tag"] for f in view["faces"] if f["surface_type"] == surface_type)

    def test_a_single_face_gets_a_plain_english_explanation(self, app):
        view = build_bracket(app)
        tag = self._a_face_tag(view, "plane")
        _status, data = post(
            app, "/api/faces/describe", {"generation": view["generation"], "tags": [tag]}
        )
        assert data["ok"] is True
        assert data["explanation"]
        assert "surface_type: plane" in data["yaml"]
        assert data["checked_against"] == 2

    def test_two_faces_together_describe_as_a_set(self, app):
        """The bolt holes, described as one selection -- the case that once
        silently swallowed a third face; see describe.py.

        The bracket has three cylinder faces at its defaults: the internal
        fillet (radius 8 mm) and the two bolt holes (radius 4.5 mm each).
        Picking "the two smallest" rather than "the first two returned" is
        deliberate -- grabbing the fillet and one hole together is a
        selection nobody would actually make, and the software correctly
        refuses to describe it, which very nearly made this test pass for
        the wrong reason.
        """
        view = build_bracket(app)
        cylinders = sorted(
            (f for f in view["faces"] if f["surface_type"] == "cylinder"),
            key=lambda f: f["area_mm2"],
        )
        holes = [f["tag"] for f in cylinders[:2]]
        _status, data = post(
            app, "/api/faces/describe", {"generation": view["generation"], "tags": holes}
        )
        assert data["ok"] is True
        assert "mode: all" in data["yaml"]

    def test_an_unknown_face_tag_is_reported_not_crashed_on(self, app):
        view = build_bracket(app)
        _status, data = post(
            app, "/api/faces/describe", {"generation": view["generation"], "tags": [999999]}
        )
        assert data["ok"] is False
        assert "999999" in data["error"]

    def test_a_stale_generation_is_refused(self, app):
        """A second build makes the first one's tags meaningless -- see
        FaceView's generation field. Resolving them anyway would silently
        describe a face in a shape that no longer exists."""
        stale = build_bracket(app)
        tag = self._a_face_tag(stale, "plane")
        post(app, "/api/faces/build", {"path": str(EXAMPLES / "strut" / "project.yaml")})

        with pytest.raises(urllib.error.HTTPError) as info:
            post(
                app,
                "/api/faces/describe",
                {"generation": stale["generation"], "tags": [tag]},
            )
        assert info.value.code == 409
        assert "out of date" in error_body(info.value)["error"]

    def test_describing_before_any_build_is_refused(self, app):
        """A fresh server, so this exercises the true no-view-yet path
        rather than a stale one -- a different code path in the handler."""
        port = find_free_port()
        server = create_server(EXAMPLES.parent, port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            fresh = f"http://{HOST}:{port}"
            with pytest.raises(urllib.error.HTTPError) as info:
                post(fresh, "/api/faces/describe", {"generation": 1, "tags": [1]})
            assert info.value.code == 409
            assert "no part has been built" in error_body(info.value)["error"]
        finally:
            server.shutdown()

    def test_malformed_tags_are_rejected(self, app):
        view = build_bracket(app)
        for bad in ([], ["not-a-number"], "1", None):
            with pytest.raises(urllib.error.HTTPError) as info:
                post(
                    app,
                    "/api/faces/describe",
                    {"generation": view["generation"], "tags": bad},
                )
            assert info.value.code == 400
