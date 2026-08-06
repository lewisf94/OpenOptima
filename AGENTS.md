# Working on OpenOptima

OpenOptima runs thousands of finite element analyses unattended and reports
numbers an engineer may size a real part from. That single fact drives every
rule below. A bug here does not crash — it produces a converged, plausible,
wrong answer, and nobody notices.

Read `docs/architecture.md` before making structural changes and
`docs/engineering-assumptions.md` before touching anything that affects a
number.

## Engineering invariants

These are not style preferences. Breaking one silently corrupts results.

- **Never identify a face by its index.** Face numbering changes when geometry
  is rebuilt. Regions are resolved from geometric signatures on every
  evaluation. Do not add a `FaceN` shortcut, a cached tag, or a "just this
  once" index lookup.
- **Never resolve an ambiguous region by guessing.** If two faces score within
  the ambiguity margin, stop and report it. A silently misplaced load is the
  worst failure this software can produce.
- **Never conflate an infeasible design with an infrastructure error.** A bad
  design teaches the optimiser something; a solver crash teaches it nothing and
  must never be fed back as a poor result. See `domain/failures.py`.
- **Never optimise raw peak stress by default.** Re-entrant corners and point
  supports are singular: the peak grows without bound under mesh refinement, so
  optimising it means optimising the mesh. Report the raw peak always; use a
  percentile or p-norm as the objective.
- **Never average across load cases.** Envelope them. Averaging a failing case
  against a passing one hides the failure.
- **Never report a buckling factor outside its validated range.** Solid-element
  buckling fails optimistically on slender members, and an optimiser acts on the
  number while ignoring any warning attached to it. `RESULT_UNRELIABLE` is a
  failure, not a footnote. Do not downgrade it to a warning.
- **Never pass user values straight to a solver.** Solvers are unitless.
  Convert into the internal `mm, N, MPa, t` system first — `domain/units.py`.
- **Never change a verification tolerance to make a build pass.** The tolerances
  in `tests/verification/` and `docs/verification-plan.md` encode physics. If
  one fails, either the code regressed or the physics changed; both need a human.
- **Never write to the user's source CAD model.** Copy into the run directory.
- **Never use `shell=True`.** Argument lists only. A project path may contain a
  space or a semicolon. Enforced by `tests/unit/test_architecture.py`.

## Architecture rules

- `domain/` imports no gmsh, no pymoo, no numpy, no solver, no database. It is
  plain Python data and rules. Enforced by a test.
- External tools live behind adapters: `geometry/`, `meshing/`, `solvers/`.
  Adding a second solver or a CFD backend must not require touching the
  optimiser, the DOE, the storage layer or the CLI.
- The optimiser consumes `EvaluationResult` and nothing else. It never reads an
  FRD file, a mesh or a solver log.
- gmsh has process-global state. Always go through `geometry.gmsh_session`.
  Parallelism is by process, never by thread.
- Anything that can change a number belongs in the evaluation hash
  (`evaluation/cache.py`). If you add a physics setting, add it to
  `Project.setup_digest()` in the same commit, or stale results will be served
  as fresh ones.

## Required checks

Run before finishing any change:

```bash
ruff check . && ruff format --check .
mypy src
pytest tests/unit                      # fast, no CAE tools needed
```

Additionally, by area touched:

```bash
pytest tests/integration -m gmsh       # geometry, regions, meshing
pytest tests/verification              # anything affecting a computed number
```

`pytest tests/verification` needs gmsh and CalculiX. It is not optional when
you have changed the mesher, the deck writer, a parser, the metrics or the unit
handling.

## Conventions

- British spelling in prose and identifiers (`optimise`, `normalised`).
- Comments explain *why*, especially where a subtlety cost someone a debugging
  session. Several such comments exist; do not delete them as "obvious".
- Every fixed engineering defect gets a regression test that fails without the fix.
- New failure modes get a `FailureCode`, classified as infeasible or error.
- Prefer an explicit error over a default when the correct value is unknowable.

## Traps that have already bitten

Each of these produced a plausible, wrong result and was caught by testing, not
by review. They are documented in `docs/adr/` and guarded by tests.

1. **Netgen optimisation after `setOrder(2)`** silently reverts elements to
   first order and orphans the midside nodes. The loads then land on nodes no
   element references and the model returns zero displacement. Optimise *before*
   raising the order; assert the element type you asked for.
2. **`getBoundary(oriented=True)` signs must not be applied to
   `gmsh.model.getNormal`.** gmsh already accounts for face orientation;
   applying the sign again flips every normal inward. Verified globally with the
   divergence theorem in `regions/signature.py`.
3. **Fitting a cylinder radius from the sampled centroid** works only for a full
   cylinder. On a fillet spanning 90 degrees it is badly wrong. Fit a circle.
4. **Splitting FRD values on whitespace** merges adjacent negative numbers.
   CalculiX writes fixed-width fields; slice by column.
5. **Lumping a surface load evenly over a quadratic face** is wrong — the exact
   integral of a corner shape function over a flat 6-node triangle is zero.
   Integrate the shape functions.
6. **A `*BUCKLE` step also emits a reaction total** — an artefact of the
   eigenvalue solve, not a real reaction. Associating reactions with load cases
   by dividing the record count summed it with the real one and reported a 100%
   equilibrium error on a sound model. Select reactions by step number.
7. **CalculiX buckling silently returns the wrong mode family on slender solid
   models**, off by a factor of nine in the unsafe direction, with a mode series
   that does not follow any column pattern. Refining the mesh does not fix it.
   This is why `results/buckling_check.py` exists.

## What an agent must not decide alone

Raise these with a human rather than choosing:

- allowable stresses, safety factors, or any acceptance criterion;
- whether a mesh is converged enough to trust;
- loosening a verification tolerance;
- whether a design is safe to manufacture.

The software's job is to compute and present. Judging is the engineer's.
