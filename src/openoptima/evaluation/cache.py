"""Evaluation caching.

A cache hit must mean "this exact question has already been answered".  The key
therefore covers the design vector *and* everything else that could change the
answer: the project setup digest (material, loads, mesh settings, stress
measure, solver) and the versions of the tools that produced it.

Reusing a number computed under different physics is not a cache hit, it is a
wrong answer delivered quickly.
"""

from __future__ import annotations

import hashlib
import json

from ..domain.project import Project
from ..domain.variables import DesignVector

#: Tools whose version changes invalidate cached results.
_VERSION_KEYS = ("openoptima", "gmsh", "calculix")


def evaluation_hash(
    project: Project, design: DesignVector, versions: dict[str, str] | None = None
) -> str:
    payload = {
        "setup": project.setup_digest(),
        "design": design.canonical_text(),
        "versions": {key: (versions or {}).get(key, "") for key in _VERSION_KEYS},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
