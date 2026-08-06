"""Architecture guards.

These are the rules that keep a second solver or a second physics from turning
into a rewrite. They are cheap to state and easy to violate accidentally, so
they are asserted rather than documented and hoped for.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "openoptima"

#: The domain layer describes the problem, not the tools used to solve it.
FORBIDDEN_IN_DOMAIN = {
    "gmsh",
    "pymoo",
    "numpy",
    "scipy",
    "sqlite3",
    "pydantic",
    "subprocess",
    "openoptima.geometry",
    "openoptima.meshing",
    "openoptima.solvers",
    "openoptima.storage",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_domain_layer_imports_no_tools():
    offenders: list[str] = []
    for path in (SOURCE_ROOT / "domain").rglob("*.py"):
        for module in _imported_modules(path):
            if module in FORBIDDEN_IN_DOMAIN:
                offenders.append(f"{path.name} imports {module}")
    assert not offenders, (
        "the domain layer must stay free of external tools so it can be "
        f"reasoned about and tested without a CAE stack: {offenders}"
    )


def test_domain_layer_imports_without_any_optional_dependency():
    """The domain package must import with only the standard library available."""
    code = (
        "import sys;"
        "blocked = ('gmsh', 'pymoo', 'scipy');"
        "sys.modules.update({name: None for name in blocked});"
        "import openoptima.domain as d;"
        "assert d.Project is not None;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_no_shell_true_anywhere():
    """A project path with a space or a semicolon must never reach a shell."""
    offenders = [
        str(path.relative_to(SOURCE_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if "shell=True" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"shell=True found in {offenders}"


def test_solver_adapters_are_interchangeable():
    from openoptima.domain.model import SolverSpecification
    from openoptima.solvers import create_solver
    from openoptima.solvers.base import StructuralSolver

    for name in ("calculix", "analytic"):
        solver = create_solver(SolverSpecification(name=name))
        assert isinstance(solver, StructuralSolver)
        assert hasattr(solver, "available")
        assert hasattr(solver, "solve")
