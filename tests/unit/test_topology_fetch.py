"""Fetching the topology optimiser reproducibly.

The point of every test here is the same: the code that decides where material
goes in somebody's part must be the exact code this project verified, or the
fetch must fail loudly. Silently running a different version is the failure
these tests exist to prevent.

Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import urllib.error

import pytest

from openoptima.topology import fetch


def fake_upstream(monkeypatch, contents: dict[str, bytes]):
    """Serve ``contents`` in place of raw.githubusercontent.com."""

    class Response:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self, _limit: int | None = None) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def opener(request, timeout=None):
        name = request.full_url.rsplit("/", 1)[-1]
        if name not in contents:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return Response(contents[name])

    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)


def genuine() -> dict[str, bytes]:
    """Payloads whose hashes match the pinned ones, so a fetch should succeed."""
    return {name: f"# {name} at pinned commit\n".encode() for name in fetch.BESO_FILES}


def pin_to(monkeypatch, contents: dict[str, bytes]) -> None:
    monkeypatch.setattr(
        fetch,
        "BESO_FILES",
        {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()},
    )


class TestSuccessfulFetch:
    def test_writes_every_pinned_file(self, tmp_path, monkeypatch):
        contents = genuine()
        pin_to(monkeypatch, contents)
        fake_upstream(monkeypatch, contents)

        installed = fetch.install(tmp_path)

        assert sorted(p.name for p in installed.directory.iterdir()) == sorted(contents)
        assert fetch.verify(installed.directory)
        assert installed.commit == fetch.BESO_COMMIT

    def test_the_licence_is_taken_too(self):
        """beso is LGPL-3.0 and its licence must travel with the code."""
        assert "LICENSE" in fetch.BESO_FILES

    def test_the_freecad_interface_is_not_taken(self):
        """We drive beso directly; its GUI would drag in FreeCAD."""
        assert "beso_fc_gui.py" not in fetch.BESO_FILES

    def test_the_directory_is_keyed_by_commit(self, tmp_path):
        """So a future upgrade adds a copy rather than overwriting a verified one."""
        assert fetch.BESO_COMMIT[:12] in fetch.install_directory(tmp_path).name

    def test_a_second_call_reuses_rather_than_refetching(self, tmp_path, monkeypatch):
        contents = genuine()
        pin_to(monkeypatch, contents)
        fake_upstream(monkeypatch, contents)
        first = fetch.install(tmp_path)

        def refuse(*_a, **_k):
            raise AssertionError("re-downloaded a copy that was already verified")

        monkeypatch.setattr(fetch.urllib.request, "urlopen", refuse)
        assert fetch.install(tmp_path).directory == first.directory


class TestItRefusesTheWrongCode:
    def test_a_changed_file_is_refused(self, tmp_path, monkeypatch):
        """The case that matters: upstream moved, so this is not what we verified."""
        contents = genuine()
        pin_to(monkeypatch, contents)
        contents["beso_main.py"] = b"# upstream changed under us\n"
        fake_upstream(monkeypatch, contents)

        with pytest.raises(fetch.BesoFetchError, match="does not match the version"):
            fetch.install(tmp_path)

    def test_nothing_is_written_when_one_file_is_wrong(self, tmp_path, monkeypatch):
        """A half-installed copy would look present to the next check.

        Every file is downloaded and checked before any is written, so a
        failure part-way through leaves nothing behind.
        """
        contents = genuine()
        pin_to(monkeypatch, contents)
        contents["beso_lib.py"] = b"# tampered\n"
        fake_upstream(monkeypatch, contents)

        with pytest.raises(fetch.BesoFetchError):
            fetch.install(tmp_path)

        assert not fetch.install_directory(tmp_path).exists()

    def test_the_message_says_a_human_is_needed(self, tmp_path, monkeypatch):
        """Not something to retry around: it needs re-verifying, not re-running."""
        contents = genuine()
        pin_to(monkeypatch, contents)
        contents["beso_main.py"] = b"# changed\n"
        fake_upstream(monkeypatch, contents)

        with pytest.raises(fetch.BesoFetchError) as caught:
            fetch.install(tmp_path)
        assert "needs a human" in str(caught.value)

    def test_a_missing_file_is_refused(self, tmp_path, monkeypatch):
        contents = genuine()
        pin_to(monkeypatch, contents)
        del contents["beso_filters.py"]
        fake_upstream(monkeypatch, contents)

        with pytest.raises(fetch.BesoFetchError, match="could not download"):
            fetch.install(tmp_path)

    def test_an_oversized_file_is_refused_before_it_is_written(self, tmp_path, monkeypatch):
        contents = genuine()
        pin_to(monkeypatch, contents)
        contents["beso_main.py"] = b"x" * (fetch._MAXIMUM_FILE_BYTES + 1)
        fake_upstream(monkeypatch, contents)

        with pytest.raises(fetch.BesoFetchError, match="larger than expected"):
            fetch.install(tmp_path)
        assert not fetch.install_directory(tmp_path).exists()

    def test_a_network_failure_is_reported_plainly(self, tmp_path, monkeypatch):
        def fail(*_a, **_k):
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(fetch.urllib.request, "urlopen", fail)
        with pytest.raises(fetch.BesoFetchError, match="could not download"):
            fetch.install(tmp_path)


class TestVerify:
    def test_tampering_after_install_is_detected(self, tmp_path, monkeypatch):
        """Guards against a copy edited on disk after it was verified."""
        contents = genuine()
        pin_to(monkeypatch, contents)
        fake_upstream(monkeypatch, contents)
        installed = fetch.install(tmp_path)

        (installed.directory / "beso_main.py").write_text("print('not verified')")
        assert not fetch.verify(installed.directory)

    def test_a_missing_directory_is_not_verified(self, tmp_path):
        assert not fetch.verify(tmp_path / "nothing here")

    def test_a_deleted_file_is_detected(self, tmp_path, monkeypatch):
        contents = genuine()
        pin_to(monkeypatch, contents)
        fake_upstream(monkeypatch, contents)
        installed = fetch.install(tmp_path)

        (installed.directory / "beso_lib.py").unlink()
        assert not fetch.verify(installed.directory)
