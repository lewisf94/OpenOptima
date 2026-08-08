"""Small persistent settings, stored outside any project.

Only one thing lives here today: where the user's stress solver is. That has
to persist, because the alternative is asking somebody to set an environment
variable every time they want to run an analysis, which is the gap recorded in
``docs/known-issues.md``.

This sits at the top level rather than under ``app/`` because the solver
adapter reads it too, and a solver importing from the desktop app would invert
the dependency. Nothing here knows about geometry, meshing or physics: it is
file plumbing.

Deliberately forgiving on read and strict on write. A settings file that has
been hand-edited into invalid JSON must not stop the application from starting
-- it should behave as though nothing was remembered, which is recoverable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Overrides where settings live. Used by the tests so they never touch the
#: real user profile, and available to anyone running several installs.
_DIRECTORY_ENV_VAR = "OPENOPTIMA_CONFIG_DIR"

_SOLVER_KEY = "calculix_executable"


def settings_directory() -> Path:
    override = os.environ.get(_DIRECTORY_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "OpenOptima"


def settings_path() -> Path:
    return settings_directory() / "settings.json"


def load_settings() -> dict[str, Any]:
    """Every stored setting. An unreadable or corrupt file reads as empty."""
    path = settings_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_settings(values: dict[str, Any]) -> None:
    """Write settings, replacing the file.

    Written to a neighbouring temporary file and moved into place, so an
    interrupted write cannot leave a half-written file that then reads as
    corrupt on the next start.
    """
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def remembered_solver() -> str | None:
    """The solver the user chose in the app, if it is still there.

    A path that no longer exists returns ``None`` rather than being reported as
    a solver: the usual cause is an uninstall or a moved folder, and treating a
    stale entry as valid would produce a confusing failure much later, inside a
    run, instead of on the setup screen where it can be fixed.
    """
    value = load_settings().get(_SOLVER_KEY)
    if not isinstance(value, str) or not value:
        return None
    return value if Path(value).is_file() else None


def remember_solver(executable: str | Path) -> None:
    values = load_settings()
    values[_SOLVER_KEY] = str(Path(executable))
    save_settings(values)


def forget_solver() -> None:
    values = load_settings()
    if values.pop(_SOLVER_KEY, None) is not None:
        save_settings(values)
