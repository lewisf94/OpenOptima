#!/usr/bin/env bash
# Standard pre-commit checks. Run from the repository root.
#
#   scripts/check.sh          fast checks only (no CAE tools needed)
#   scripts/check.sh --all    everything, including verification benchmarks
set -euo pipefail

python -m ruff check .
python -m ruff format --check .
python -m mypy src || echo "mypy reported issues (not blocking)"
python -m pytest tests/unit -q

if [[ "${1:-}" == "--all" ]]; then
    echo
    echo "Integration and verification (needs gmsh and CalculiX)..."
    python -m pytest tests/integration tests/verification -q
    openoptima doctor examples/l_bracket/project.yaml
fi

echo
echo "All checks passed."
