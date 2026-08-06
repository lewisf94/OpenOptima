# Contributing

The most valuable contribution to this project is **evidence that a number is
wrong**. Software that crashes is easy to fix; software that returns a
plausible, converged, wrong answer is the failure mode that matters, and it
takes an engineer with a reference result to catch it.

If you have a hand calculation, a published benchmark, or a result from another
solver that disagrees with OpenOptima, please open an issue with the
"Wrong or suspicious result" template. That is worth more than a feature.

## Getting set up

```bash
sudo apt install calculix-ccx libglu1-mesa    # Debian/Ubuntu
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,optimise]"

pytest tests/unit                              # fast, no CAE tools needed
openoptima doctor examples/l_bracket/project.yaml
```

## Before opening a pull request

```bash
ruff check . && ruff format --check .
mypy src
pytest tests/unit
```

Plus, by area touched:

- geometry, regions or meshing → `pytest tests/integration`
- anything affecting a computed number → `pytest tests/verification`

## The rules that matter

Read [`AGENTS.md`](AGENTS.md). It applies to humans too — it is just written for
whoever is doing the work. The short version:

- Never identify a face by index; never guess an ambiguous region.
- Never conflate an infeasible design with an infrastructure error.
- Never optimise raw peak stress by default.
- Never average across load cases.
- Never widen a verification tolerance to make a build pass.
- Never use `shell=True`.

Each of these has a test enforcing it, and each exists because breaking it
produces wrong answers rather than errors.

## Working with coding agents

This project is built to be worked on by agents as well as people.
[`AGENTS.md`](AGENTS.md) is the root instruction file (`CLAUDE.md` imports it),
with additional per-module files in `src/openoptima/*/AGENTS.md` for the areas
with the sharpest traps.

If you use an agent, review its work against the invariants above. Agents are
good at the code and poor at knowing when a number is unphysical.

## Adding a solver or a physics backend

Implement `solvers/base.py::StructuralSolver` and register it in
`create_solver`. If you find yourself needing to change `doe/`, `optimisation/`,
`storage/` or `cli/` to do it, the abstraction is wrong — please say so in the
issue, because that is useful information about the design.

## Adding a geometry template

Add it to `geometry/occ/templates.py` with `register()`. A template must:

- validate its own parameters and raise `EvaluationFailure` with an
  **infeasible** code for impossible designs, *before* touching the CAD kernel;
- leave exactly one solid behind;
- never depend on face or edge tags surviving between builds.

Include an integration test that builds it at the extremes of its parameter
range, since that is where things break.

## Style

British spelling. Comments explain *why*, not *what* — especially where a
subtlety cost someone a debugging session. Several such comments exist; please
do not delete them as obvious. They were not obvious.

## Licence

Contributions are accepted under GPL-3.0-or-later.
