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
- **OpenOptima is the control system, not the calculator.** It owns CAD
  parameterisation, the design space, region tracking, orchestration, failure
  classification, constraint handling, verification, provenance and the
  presentation of a result. Established engineering mathematics belongs to
  established tools: CalculiX solves, Gmsh meshes, pymoo searches, beso does
  topology, pyLife does fatigue, trimesh does mesh geometry.
- **Search before you build any non-trivial engineering capability.** Audit it
  in `docs/capability-audit.md` first — every roadmap item has an entry, and a
  new one needs an entry before it needs code. Verdicts are use, wrap, build,
  or blocked. Two rules inside that:
  - **Read the licence in the package metadata, not the README badge.** One
    surrogate-optimisation library advertises Apache-2.0 and ships
    PolyForm Noncommercial. Using it would have made this project
    non-commercial.
  - **Reuse saves writing the code, never the verification.** A library's
    number faces the same published benchmark as ours, and inherits nothing
    by being popular or maintained.
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

## Git workflow

Push straight to `main`. This is a single-maintainer project — feature
branches and pull requests are overhead a `git revert` does not need. If a
session's tooling assigns a different working branch by default, that is
fine to develop on, but merge it to `main` yourself (fast-forward, no PR)
once the required checks pass, rather than leaving it stranded waiting for
review.

## Model selection

Work on this project happens across many separate sessions, sometimes on
different Claude models. State which model fits **before** starting a new
piece of work — one line, not a discussion — instead of picking silently.
Where several small pieces of work are queued, group the ones that suit the
same model into one sitting rather than switching back and forth: starting a
fresh session to re-derive context costs more than most single tasks here do.

**Use Opus 5** for anything that touches a computed number, or a defect that
could: the deck writer, parsers, mesh handling, units, metrics, failure
criteria, buckling, convergence, region resolution, and any new verification
benchmark. Also for architecture decisions — a new ADR, or a change to what a
layer is allowed to own. This is also the model for diagnosing a defect that
produces a plausible but wrong answer: write a throwaway script and look at
real output first, the same way every defect in the traps list below was
actually found — never write the assertion before seeing the number it is
supposed to catch.

**Sonnet 5 is enough** for documentation, the roadmap, the capability audit,
README work, recording a decision a human or an earlier session already made,
configuration and CI fixes that do not touch physics, and mechanical wiring
once a design is already settled elsewhere.

A task that is mostly one and partly the other gets the more careful model
for the part that touches a number, said explicitly rather than assumed. If
it genuinely is not clear which side a task falls on, say that too, rather
than guessing.

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
7. **CalculiX buckling silently skips the lowest mode and returns the second**,
   about nine times too high in the unsafe direction, with nothing in its output
   to say so. The trigger is *not* slenderness, which is what the code originally
   guarded: it is the buckling factor itself, below about **0.52** against the
   applied load. Measured identically at slenderness 69 and 277. Refining the
   mesh does not fix it and neither does asking for more modes. Fixed at the root
   by solving the `*BUCKLE` step against a load 1000x smaller and dividing back —
   the eigenvalue is exactly inversely proportional to the reference load. Do not
   remove that scaling. `results/buckling_check.py` and V9 in
   `docs/verification-plan.md` carry the evidence.
8. **The browser process the app launches is not the browser process that shows
   the window.** Launching Edge with a fresh profile directory starts a process
   that prepares the profile, hands the window to a *different* process, and
   exits after about half a second — reliably, not occasionally. Waiting on it
   to decide the app is still open therefore shut the server down before the
   window had even appeared, and the user's first ever launch showed
   "127.0.0.1 refused to connect". The `First Run` sentinel does not prevent
   the hand-off. The page says whether it is alive instead, by pinging
   `/api/alive`; see `app/launcher.py::_supervise`. A brand-new profile also
   makes Edge run its welcome flow in an ordinary browser window, so the
   profile is seeded as already set up before first launch.
9. **A frozen build has no package metadata unless you ask for it.** Some
   libraries call `importlib.metadata.version()` on themselves at import time.
   PyInstaller does not bundle `.dist-info` folders, so that raises
   `PackageNotFoundError`. This shipped: pymoo imports moocore for its
   hypervolume indicator, moocore looks up its own version, and every
   optimisation died the instant the user pressed Start — while the app itself
   started perfectly. Use `copy_metadata(..., recursive=True)` in the spec.
   The wider lesson is that **starting the server proves almost nothing**: the
   optimiser, the mesher, the solver adapter and the geometry provider are all
   imported lazily. `openoptima-app --self-check` imports them all and the
   Windows build script fails the build on it. Add anything new and lazily
   imported to `launcher._self_check_steps`.

10. **CalculiX writes stress in a different coordinate system depending on
    which file you read.** With an `*ORIENTATION` attached, `*EL PRINT` writes
    the `.dat` file in the material's local axes, while `*EL FILE` writes the
    `.frd` file in global axes. Measured on one element, isotropic material,
    orientation rotated 45 degrees, uniaxial tension of 100 MPa: the `.dat`
    file reports `50, 50, 0, -50` and the `.frd` file reports `100, 0, 0, 0`
    for the identical state. Both are correct in their own frame. We read the
    `.frd`, so anything that needs material axes — every directional failure
    criterion — must rotate the tensor itself. Assuming the two agree is a
    45-degree error that looks entirely plausible and changes which direction
    a printed part appears weakest in. `results/directional.py` does the
    rotation; `tests/unit/test_failure_criteria.py` guards it.

## What an agent must not decide alone

Raise these with a human rather than choosing:

- allowable stresses, safety factors, or any acceptance criterion;
- whether a mesh is converged enough to trust;
- loosening a verification tolerance;
- whether a design is safe to manufacture.

The software's job is to compute and present. Judging is the engineer's.
