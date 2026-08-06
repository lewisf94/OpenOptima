from __future__ import annotations

import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _has_gmsh() -> bool:
    try:
        import gmsh  # noqa: F401
    except Exception:
        return False
    return True


def _has_calculix() -> bool:
    return any(shutil.which(name) for name in ("ccx", "ccx_2.22", "ccx_2.21", "ccx_2.20"))


requires_gmsh = pytest.mark.skipif(not _has_gmsh(), reason="gmsh is not installed")
requires_calculix = pytest.mark.skipif(
    not _has_calculix(), reason="CalculiX (ccx) is not installed"
)


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def l_bracket_project():
    from openoptima.schema.loader import load_project

    return load_project(EXAMPLES / "l_bracket" / "project.yaml")
