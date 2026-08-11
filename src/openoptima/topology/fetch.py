"""Fetching ``beso``, the topology optimiser, reproducibly.

``beso`` is not on PyPI. It is a GitHub repository of scripts, so it cannot be
listed in ``pyproject.toml`` and installed with everything else. It has to be
downloaded, and a download is a thing that can silently change under you.

So it is pinned to one commit and every file is checked against a hash recorded
here. If upstream changes, the fetch **fails loudly** rather than running
different code than was verified. That matters more here than usual: this
program decides where material goes in a part somebody may build.

**Files are fetched one at a time, not as an archive.** GitHub generates
archives on demand and has changed how it compresses them before, which would
break an archive hash without a single byte of code changing. Fetching each
file at its pinned commit avoids that question entirely, avoids unpacking
untrusted archive paths, and takes only the seven files that are actually
needed.

``beso`` is LGPL-3.0. Its licence file is fetched with it and must stay with
it. It is deliberately **not** bundled into the installer -- doing that brings
distribution obligations that need a human's review first.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: The pinned commit. Changing this means re-recording every hash below, and
#: re-running the verification benchmarks -- a new version of the optimiser is
#: not verified just because the old one was.
BESO_COMMIT = "5056d301c2675df9ac1af771af4f1cbea642e4ad"

BESO_SOURCE_URL = "https://github.com/calculix/beso"
_RAW_BASE = f"https://raw.githubusercontent.com/calculix/beso/{BESO_COMMIT}"

#: Every file we take, with the SHA-256 of its contents at ``BESO_COMMIT``.
#: ``LICENSE`` is not optional: it is beso's own licence and must travel with
#: the code. ``beso_fc_gui.py`` is deliberately absent -- it is the FreeCAD
#: interface, and this project drives beso directly.
BESO_FILES: dict[str, str] = {
    "beso_conf.py": "af464229879e666526aff06f465b17344ca60cf2ea46c20637cc72469258aca3",
    "beso_filters.py": "a95a268f9fd4a567ad4d91f22e64d5c08395cedf161e4543e6dab0ee159af47e",
    "beso_lib.py": "820792f678f96edf5cb034b5488bb394b7f3fb15ade326a5910cd3a3c12e7dab",
    "beso_main.py": "f4cf3f65035e9ac00d4957c78f642c0188b06756cfcbdbad5da4a874d9be4d8f",
    "beso_plots.py": "0b030e91ad9ace24a57e5f9a053a4438f7a5fe2afa5e24a8ca24f9255eabdb2f",
    "beso_separate.py": "c0ad3a83084ced92622f109a498742b0a61af5ac7838bdbd5300ceb41e635545",
    "LICENSE": "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
}

#: The largest of these files is well under a megabyte. Refuse anything wildly
#: larger before the hash is checked, so a redirect somewhere unexpected cannot
#: fill the disk.
_MAXIMUM_FILE_BYTES = 4 * 1024 * 1024

ProgressCallback = Callable[[str, float], None]


class BesoFetchError(RuntimeError):
    """beso could not be fetched, or is not the code we verified against."""


@dataclass(frozen=True)
class InstalledBeso:
    """A verified copy of beso on disk."""

    directory: Path
    commit: str

    @property
    def main_script(self) -> Path:
        return self.directory / "beso_main.py"


def install_directory(base: Path) -> Path:
    """Where a verified copy lives. Keyed by commit, so upgrading is additive."""
    return base / f"beso-{BESO_COMMIT[:12]}"


def verify(directory: Path) -> bool:
    """True when every pinned file is present and its contents match."""
    for name, expected in BESO_FILES.items():
        path = directory / name
        if not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def _fetch_one(name: str, expected: str) -> bytes:
    """Download one pinned file and check it before it is returned."""
    # https only, and a pinned host, commit and file name: the URL is built
    # entirely from module constants, never from anything a project file or a
    # page can influence.
    url = f"{_RAW_BASE}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenOptima"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = response.read(_MAXIMUM_FILE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BesoFetchError(
            f"could not download the topology optimiser file {name!r} from {url}: {exc}"
        ) from exc

    if len(payload) > _MAXIMUM_FILE_BYTES:
        raise BesoFetchError(
            f"the topology optimiser file {name!r} was far larger than expected, "
            f"so it was stopped before anything was written to disk"
        )

    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise BesoFetchError(
            f"the topology optimiser file {name!r} does not match the version this "
            f"project verified. Expected {expected[:12]}..., got {actual[:12]}.... "
            f"Nothing has been installed. This needs a human: either the download "
            f"was corrupted, or upstream changed and the pinned hashes in "
            f"topology/fetch.py need updating and re-verifying."
        )
    return payload


def install(base: Path, progress: ProgressCallback | None = None) -> InstalledBeso:
    """Fetch and verify beso, or return the copy already verified at ``base``.

    Every file is downloaded and checked *before* any of them is written, so a
    failure part-way through cannot leave a half-installed copy that would then
    look present to a later check.
    """
    directory = install_directory(base)
    if verify(directory):
        return InstalledBeso(directory=directory, commit=BESO_COMMIT)

    contents: dict[str, bytes] = {}
    for index, (name, expected) in enumerate(BESO_FILES.items()):
        if progress:
            progress("Downloading the topology optimiser", index / len(BESO_FILES))
        contents[name] = _fetch_one(name, expected)

    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in contents.items():
        (directory / name).write_bytes(payload)

    if not verify(directory):  # pragma: no cover - _fetch_one raises first
        raise BesoFetchError("the topology optimiser failed its check after installing")
    if progress:
        progress("Downloading the topology optimiser", 1.0)
    return InstalledBeso(directory=directory, commit=BESO_COMMIT)
