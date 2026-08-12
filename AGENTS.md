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

11. **A topology run is not reproducible on more than one processor core.**
    Measured: the identical problem run twice on all cores produced two
    different shapes; run twice on one core it produced bit-identical output.
    CalculiX's threaded solve differs in the last bits of its arithmetic
    depending on how work lands on threads, and a topology run turns those bits
    into decisions — an element on the boundary between keep and remove goes one
    way in one run and the other way in the next, and seventy rounds compound it
    into a different part. `topology/runner.py` therefore defaults to one core
    and warns when given more. Do not "fix" that as a performance oversight: a
    design that cannot be reproduced from its own inputs cannot be defended,
    cached or verified.

12. **A shape made of triangles has no curve to put a midside node on.** For
    real CAD, `Mesh.SecondOrderLinear = 0` is right: it pushes each midside node
    onto the true surface, so a fillet is meshed as a curve rather than a set of
    flat facets. For a shape that arrived as an STL there *is* no true surface —
    the triangles are the surface — and gmsh pushes the node onto its own
    faceted stand-in instead. Measured on a real topology result: 6 of 2060
    elements came out inside out, minimum scaled Jacobian **−0.2893**. Refining
    to 4267 elements did not fix it. Straight midsides gave **+0.0851** with
    none inverted and the identical volume. `meshing/sources.py` decides this
    per route via `Loaded.curved_midsides`; do not "improve" the discrete path
    by turning curvature back on.

    Two more things about that route, both found the same way. gmsh splits one
    flat face into several patches — the top face of a real result arrived as
    five — so a selector asking for one face finds five equal candidates and
    stops; `regions/discrete.py` merges touching coplanar patches first. And a
    round face must be fitted to the **corner points**, not to the middles of
    the triangles: the middles sit inside the true circle, and a 3.000 mm hole
    fitted from them measures 2.967 mm. That is trap 3 again in a new guise.

13. **CalculiX carries an output request forward into the next step, so a
    `*FREQUENCY` step writes a mode shape nobody asked for.** A frequency step
    following a static one emits DISP, STRESS and ERROR into the FRD for
    *every* mode, because the static step's `*NODE FILE`/`*EL FILE` request is
    still in force. Six modes is eighteen extra blocks. Measured: 21 blocks and
    46 413 lines with the request carried forward, 3 blocks and 10 131 lines
    with it cleared, frequencies identical to every digit.

    That matters because `frd.py` reads results by block order — the n-th
    displacement block is the n-th solved step. Eighteen unexpected blocks
    shift every later static result along, and **a mode shape is a
    displacement field that looks exactly like a real deflection**, only
    scaled arbitrarily. There is no signature to catch it downstream: the
    stress is plausible, the deflection is plausible, and the load case they
    are attributed to is wrong. The fix is an empty `*NODE FILE` / `*EL FILE`
    in the frequency step, which replaces the carried-forward request; the
    reader also skips any block whose `100C` record says `MODAL`. Both are
    kept, because the failure is silent.

    The same step also emits a reaction total and an internal energy **per
    mode**, which is trap 6 with more records. Selecting by step number
    already handles it — do not replace that with counting.

14. **A part the supports do not hold still has no natural frequency, and
    CalculiX will not tell you.** A free body has six rigid-body modes at zero
    hertz: three ways to drift, three ways to spin. Measured on a beam held
    only against its load direction, four of six modes came back at exactly
    0 Hz with the first real one at 1821 Hz. Exit code 0, nothing in the log.
    A static solve does not necessarily catch this either — it only fails if
    the load happens to push in a direction nothing is holding.

    Reporting the next mode up instead answers a question nobody asked: the
    frequency of a part held in a way the project does not describe. It is
    `MODEL_NOT_HELD`, an infrastructure error, so the optimiser never learns
    from it. The threshold is **relative** — a mode at or below 1e-4 of the
    highest frequency in the same solve — because a fixed number of hertz
    cannot work: a real first mode was measured at 18.6 Hz on a long thin
    beam, and at 419 Hz on a stubby one, both correct.

15. **A feature eats the face beside it, and the selector keeps finding what
    is left.** Adding a rounded corner where two faces meet trims both of
    them back. The region selector goes on resolving to the remains, with no
    error and nothing in the output to say the face is no longer the face
    that was picked. Measured on the example bracket, whose loaded end face
    starts at 1140 mm²: a 15 mm round leaves 240 mm², 18.9 mm leaves 6 mm²,
    and **18.99 mm leaves 0.6 mm²** — a strip 1900 times smaller than the
    face the user clicked, carrying the whole 2.5 kN.

    The kernel refuses the round outright at 19.0 mm, and that loud failure
    is worth nothing as protection: the dangerous band sits immediately
    below the point where it starts refusing. There is no threshold the
    software can pick here, because how small is too small depends on what
    the face is *for*. So `SemanticRegion.min_area_mm2` is the engineer's
    number with no default, enforced as **infeasible** in
    `regions/matcher.py::_checked`, and `openoptima doctor` always reports
    each region's area at both ends of the design range whether or not one
    is set. Do not invent a default for it.

    Two more things came out of the same work, both worth keeping in mind.
    **Adding one fillet renumbered every face of the part** — 5→2, 7→5, 8→7,
    nothing kept its number — which is why a feature names the two regions
    an edge lies between rather than the edge. And a region used to place a
    feature must resolve on the shape *before* that feature is applied as
    well as on the finished part; those are different shapes, and the error
    message has to say which one failed, because the fixes differ.

16. **The mesher's retry ladder decided what not to retry from a hardcoded
    list of failure codes.** Anything not on the list was retried at four
    coarser settings and then reported as `MESH_GENERATION_FAILED` — an
    infrastructure error. So an infeasible design arrived at the optimiser
    as "we could not find out", teaching it nothing about a design it should
    have learned to avoid, and costing four evaluations that could not have
    succeeded. `MANUFACTURING_RULE_VIOLATED` had been falling through it
    since the list was written; `REGION_TOO_SMALL` fell through it the day
    it was added, which is how this was found.

    It now asks `Outcome.INFEASIBLE` rather than naming codes, so a failure
    code added later cannot reopen it. The general lesson: **where the
    taxonomy already answers a question, ask the taxonomy.** A list of codes
    that has to be kept in step with an enum will eventually not be.
    `tests/unit/test_mesh_retry_classification.py` fails without the fix.

17. **Which way a part is printed halves its strength and does not move the
    stress at all.** Measured on `examples/drone_arm` — identical shape,
    identical loads, identical mesh, changing only `build_direction`:

        printed flat      stress 7.53 MPa   factor of safety 3.07   passes
        printed upright   stress 7.54 MPa   factor of safety 1.55   FAILS

    The stress agrees to three significant figures while the part goes from
    a comfortable pass to infeasible. There is no signature in a stress
    field to catch this, which is the point: **von Mises stress cannot
    express a material that is not equally strong in every direction**, and
    a reviewer eyeballing a stress plot has nothing to notice. The factor of
    safety has to come from a criterion that takes the whole stress tensor
    in the material's own axes — `results/directional.py`, trap 10 above.

    Two process lessons came out of the same work, and the first is the
    wider one.

    **A capability nobody can reach is not done, however well it is
    verified.** The entire directional stack — `domain/orthotropic.py`, both
    criteria, the `*ELASTIC, TYPE=ORTHO` deck writer, and verification
    benchmark V10 — was built, tested, and written up in
    `docs/engineering-assumptions.md` as something a user could do. No
    `project.yaml` could reach any of it, because `MaterialSchema` accepted
    only an isotropic material. The docs described a feature the software
    would refuse. When finishing a capability, load it from a project file
    and run it, or it is not finished.

    **Hoffman's admissibility limit binds on tension times compression, not
    on strength.** The docstring's "weakest direction under half the
    strongest" is right only when tension and compression fall together.
    A print is bad at being pulled apart and fine at being pressed, so its
    compression term holds the product up: measured with in-plane strengths
    at 22/30 MPa, Hoffman is accepted at 6.0 MPa through-layer tension
    (product ratio 0.2545) and refused at 5.0 (0.2121). Stating the limit as
    a strength ratio sends real prints to `max_stress` unnecessarily.
    `tests/unit/test_printed_material_schema.py` pins both facts.

    Related: **buckling and topology refuse a printed material outright**
    rather than picking one of its two stiffnesses. Both rest on a single
    modulus — the buckling cross-check compares against beam theory, and
    beso has nowhere to put an `*ORIENTATION`. Guessing which modulus to use
    would validate the answer against the wrong material, optimistically.

## What an agent must not decide alone

Raise these with a human rather than choosing:

- allowable stresses, safety factors, or any acceptance criterion;
- whether a mesh is converged enough to trust;
- loosening a verification tolerance;
- whether a design is safe to manufacture.

The software's job is to compute and present. Judging is the engineer's.
