"""Choosing a working directory ``beso`` can actually run in.

On Windows only, ``beso`` starts CalculiX through the shell, passing a *list*
of arguments (``beso_main.py``, near the end of its main loop). The shell joins
that list into one command string, so any path containing a space splits into
two arguments and the solver is handed a file name that does not exist. The
default place a Windows user keeps their work is
``C:\\Users\\First Last\\Documents``, so this is not a corner case -- it breaks
for most Windows users.

This is a real defect in beso and the fix belongs upstream.
``docs/adr/0010-topology-optimisation-via-beso.md`` records the decision taken
in the meantime: **give beso a working directory with no spaces in it,
wherever the user's project happens to live.** The two are allowed to differ.
Results are copied back afterwards.

The temporary directory is not automatically safe, which is the trap here. On
Linux it is usually ``/tmp``, but on Windows it is
``C:\\Users\\First Last\\AppData\\Local\\Temp`` -- a space, in the very place
that looks like the obvious answer.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"


class NoSafeWorkspace(RuntimeError):
    """No directory could be found that beso is able to run in."""


def is_safe(path: Path | str) -> bool:
    """True when beso can start the solver from here.

    A space is what actually breaks beso's call, but the other characters a
    shell treats specially -- semicolons, ampersands, redirections -- would not
    survive it either. All are refused here rather than discovered later on
    somebody else's machine.
    """
    text = str(path)
    return not any(character in text for character in ' \t;&|<>^"')


def candidate_bases() -> list[Path]:
    """Places to try, best first.

    An explicit override comes first so a user whose machine defeats every
    guess still has a way through, and it is the same environment-variable
    pattern already used for locating the solver.
    """
    candidates: list[Path] = []

    override = os.environ.get("OPENOPTIMA_TOPOLOGY_WORKSPACE")
    if override:
        candidates.append(Path(override))

    candidates.append(Path(tempfile.gettempdir()))

    if WINDOWS:
        # A short, predictable directory on the system drive. Chosen because
        # it is almost always writable and almost never contains a space,
        # unlike anything under the user's profile.
        system_drive = os.environ.get("SYSTEMDRIVE", "C:")
        candidates.append(Path(f"{system_drive}\\OpenOptima-work"))
    else:
        candidates.append(Path("/tmp"))  # noqa: S108 - checked for writability below

    return candidates


def choose_base() -> Path:
    """The first candidate that has no space and can be written to."""
    tried: list[str] = []
    for base in candidate_bases():
        if not is_safe(base):
            tried.append(f"{base} (contains a space or a shell character)")
            continue
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".openoptima-write-test"
            probe.touch()
            probe.unlink()
        except OSError as exc:
            tried.append(f"{base} (not writable: {exc})")
            continue
        return base

    attempts = "; ".join(tried) if tried else "none"
    raise NoSafeWorkspace(
        "could not find a directory the topology optimiser can run in. It needs "
        "a path with no spaces in it, because the optimiser starts the solver in "
        "a way that breaks on one. Tried: " + attempts + ". Set "
        "OPENOPTIMA_TOPOLOGY_WORKSPACE to a writable path with no spaces, for "
        "example C:\\OpenOptima-work."
    )


@contextmanager
def workspace(keep: bool = False) -> Iterator[Path]:
    """A private, space-free directory for one topology run.

    ``keep`` leaves it behind, which is what a failed run wants: the solver
    deck, the log and whatever beso managed to write are the evidence needed to
    work out why.
    """
    base = choose_base()
    directory = Path(tempfile.mkdtemp(prefix="topo-", dir=base))
    if not is_safe(directory):  # pragma: no cover - mkdtemp adds no spaces
        raise NoSafeWorkspace(
            f"the working directory {directory} contains a space even though its "
            f"parent did not. This should not happen; please report it."
        )
    try:
        yield directory
    finally:
        if not keep:
            _remove(directory)


def _remove(directory: Path) -> None:
    import shutil

    shutil.rmtree(directory, ignore_errors=True)
