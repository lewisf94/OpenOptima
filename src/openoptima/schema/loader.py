"""Loading and migrating project files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..domain.project import CURRENT_SCHEMA_VERSION, Project
from .project_schema import ProjectSchema


class ProjectLoadError(Exception):
    """A project file could not be read, migrated or validated."""


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Bring an older project document up to the current schema version.

    Migrations are explicit and ordered.  An old project is never simply
    reinterpreted under new defaults: if a default changed, the migration must
    write the old value in so the study keeps producing the same numbers.
    """
    version = int(raw.get("schema_version", 1))
    if version > CURRENT_SCHEMA_VERSION:
        raise ProjectLoadError(
            f"Project schema version {version} is newer than this build supports "
            f"({CURRENT_SCHEMA_VERSION}). Upgrade OpenOptima."
        )
    # No migrations yet — version 1 is the first published format.  When
    # version 2 arrives, add:  if version < 2: raw = _migrate_1_to_2(raw)
    raw["schema_version"] = CURRENT_SCHEMA_VERSION
    return raw


def load_project_dict(raw: dict[str, Any]) -> Project:
    try:
        schema = ProjectSchema.model_validate(migrate(dict(raw)))
    except ValidationError as exc:
        raise ProjectLoadError(_format_validation_error(exc)) from exc
    try:
        return schema.to_domain()
    except ValueError as exc:
        raise ProjectLoadError(str(exc)) from exc


def load_project(path: str | Path) -> Project:
    """Read a project YAML file and return the validated domain object."""
    path = Path(path)
    if not path.exists():
        raise ProjectLoadError(f"Project file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectLoadError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectLoadError(f"{path}: expected a mapping at the top level")
    try:
        return load_project_dict(raw)
    except ProjectLoadError as exc:
        raise ProjectLoadError(f"{path}: {exc}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["project file is not valid:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  {location or '<root>'}: {error['msg']}")
    return "\n".join(lines)
