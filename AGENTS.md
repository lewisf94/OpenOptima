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

18. **A CalculiX `MASS` element is not in `Eall`, so it has no weight.**
    Gravity written as `Eall, GRAV, ...` — which is what the deck writer did
    — never reaches a point mass. Measured on a 100 mm steel cantilever
    carrying 0.2 kg: reaction 0.3843 N with `Eall` alone, the beam's own
    weight, against 2.3463 N when both sets are named (hand calculation
    2.3470). Exit code 0, nothing in the log. A part sized against an
    acceleration case would have been sized without the thing it carries.
    `deck.py::_write_gravity` names every mass set;
    `tests/verification/test_point_mass.py` fails with exactly 0.3843 N if
    that is removed.

    **The mass is split between the face's nodes by count, not by the
    consistent rule — and that is trap 5 inverted.** Trap 5 says to
    integrate the shape functions for a surface *load*, because lumping
    evenly is wrong. For a *mass* the same integral is the wrong answer: it
    is exactly zero at the corner nodes of a quadratic face and negative for
    some element types. A zero is merely odd; a **negative mass** makes an
    eigenvalue solve return numbers with no physical meaning, and nothing
    downstream would flag it. The total is exact either way, and the total is
    what sets the frequency. So the rule is not "always integrate" — it is
    *integrate what is conserved as a force, count what is conserved as a
    quantity*.

    Two things about the capability itself. **Leaving a carried mass out is
    not a small error**: on `examples/drone_arm` the arm reads 191.4 Hz bare
    and 121.5 Hz with its 35 g motor — 58% high, in the direction that looks
    safe. And a carried mass is deliberately **not** added to `mass_kg`,
    because that metric is what the optimiser is trying to reduce and the
    motor is not something it can make lighter.

19. **The cache stores a verdict as well as the numbers, and the verdict
    goes stale when a limit moves.** A constraint threshold is deliberately
    *not* in the evaluation hash, and must not be: changing "factor of
    safety at least 2" to "at least 2.5" changes no computed number, so the
    cached stress, mass and frequency are all still right and re-solving
    them is waste. What was wrong was replaying the stored *feasibility*
    alongside them.

    Measured on `examples/drone_arm` when its frequency limit was lowered
    from 195 to 170 Hz: **30 of the next run's 50 designs came back from
    cache still carrying "First natural frequency >= 195"**, 9 of them
    marked infeasible while their own stored frequency cleared 170. The
    lightest of those was 72.1 g at 175.14 Hz — **lighter than the 72.7 g
    the run went on to report as its best**. The optimiser was told its own
    best answer was unavailable, and nothing in the output said so: the run
    completed, every number in it was real, and only the verdict attached to
    them was from a question nobody was asking any more.

    `evaluation/pipeline.py::rejudge` re-applies the current constraints to
    every cached result. Only a verdict that constraints decided is
    revisited — an infeasible design that broke a manufacturing rule, lost a
    region or failed to build is a fact about the shape, and no change to a
    limit makes it buildable. `tests/unit/test_cached_constraint_rejudge.py`
    fails in both directions without the fix.

    The general lesson, and it is the wider one: **cache what was computed,
    never what was concluded.** A derived judgement has to be re-derived,
    because its inputs are not only the ones that went into the hash.

20. **Giving the optimiser one more decision is not free, and a search that
    has not finished looking reports its best so far as the answer.** Adding
    `print_direction` to `examples/drone_arm` — a categorical choice between
    three orientations, on top of three continuous dimensions — moved the
    result from **72.7 g to 83.8 g on the same 64-evaluation budget**. Both
    runs completed, reported no errors, and printed a knee point. Nothing
    said the second had simply run out of evaluations, and the extra freedom
    could in principle only have helped.

    The example now runs 120. The wider point is that **an optimisation
    result is a lower bound on how good the design could be, never a
    statement that nothing better exists** — and adding a variable without
    adding budget quietly moves that bound the wrong way. A categorical is
    worse than a continuous one here: the search slides along a dimension but
    has to *sample* a choice, and `variables.py` encodes a categorical as a
    rounded index, so neighbouring values are not neighbouring designs.

    Related, and the reason this stayed correct: when the optimiser chooses
    the print direction, `setup_digest()` must **not** pin one. The chosen
    axis rides on the design vector, which is hashed separately; baking the
    default into the setup hash would make all three orientations of one
    section collide as a single cached result — the 3.07 answer served for
    the 1.55 design. `_material_digest` records `variable:<name>` instead.
    `tests/unit/test_build_direction_variable.py` fails without that.

    **The other half, and it is a reporting trap rather than a code one:
    which value the search returns for a categorical is not a finding until
    you have shown it beats the alternatives.** The run picked `y`, and the
    obvious write-up — "the optimiser chose to print it on edge" — was
    wrong. Fixing the direction to `y` and to `z` and running each at the
    same budget produced the *identical* section, 28.6 x 26.8 x 2.06 mm at
    78.8 g both times: the two are indistinguishable here because frequency
    and stiffness bind while the factor of safety, which is what the
    orientation actually moves, never does. Meanwhile the four-variable run
    reported 70.5 g, better than either — search-path variance larger than
    the effect being attributed to the choice.

    So: `x` really is ruled out (106.1 g at best against about 71 to 79 g),
    and the rest was noise. **Before quoting a chosen category as an
    engineering result, hold it fixed and race it against the others on
    equal budget.** An optimiser always returns *a* value, and a run that
    could not tell two options apart still has to print one of them.

21. **A distributing coupling carries the force and not the moment arm, so
    a carried item attached with one has no height.** Giving a point mass a
    real position needs it tied to its mounting face, and there are two
    ways: `*RIGID BODY`, which makes the face rigid, and
    `*DISTRIBUTING COUPLING`, which does not. The second looks strictly
    better — same load path, no artificial stiffening — and it is the
    standard choice for attaching a lumped mass.

    Measured on `examples/drone_arm` with the identical 35 g item at the
    identical place, 16 mm off the pad:

        rigid tie                       166.572 Hz
        distributing coupling           170.293 Hz
        (the same mass flat in the face) 170.312 Hz

    The distributing coupling returns the answer for an item with **no
    height at all**, to within 0.02 Hz. Exit code 0, nothing in the log, and
    a frequency that is entirely plausible. The rigid tie is what works, and
    its cost was measured rather than assumed: +0.29% on the first mode, and
    **nothing at all on the stress** — 4.4517 against 4.4512 MPa at the 99th
    percentile, on a face that carries the load as well as the motor.

    Two more things from the same work, both worth keeping.

    **CalculiX 2.21 has no rotary inertia element.** The roadmap said it
    supported one. `strings` on the binary finds `MASS` and no `ROTARYI`.
    So "it resists being turned" is built from ordinary point masses at real
    positions: seven of them reproduce any real item's mass, middle and
    turning resistance exactly, and only those three reach the solve once
    the group is rigid. **Check the binary, not the manual, before planning
    around a solver feature** — the manual describes a family this build
    does not have.

    **An area-weighted centroid is not a node average, and the difference
    is a real lever arm.** The drone arm's motor pad centres at exactly
    x = 134.0 mm. Averaging its node coordinates gives 133.53 — the mesh is
    denser at the edges — and placing the motor there instead reported
    165.934 Hz against the correct 165.531. Small, but it is a systematic
    error in a position, and positions multiply into inertia. Weight by area
    when asking where a face is.

22. **Von Mises stress cannot see a load reverse, so a stress range taken
    from it reports the most damaging cycle there is as no cycle at all.**
    Fatigue is driven by how far stress *swings* each cycle. The obvious way
    to get that swing out of what this software already reports is to
    subtract one load case's von Mises stress from another's. Von Mises keeps
    the size of a stress state and throws away its direction. Measured on
    `examples/l_bracket`, top of the cycle at full load:

        bottom of cycle   from von Mises   from the tensors     error
          +0.5 x load          17.9189           17.9189         0.0%
           0   (off load)      35.8378           35.8378         0.0%
          -0.25 x load         26.8783           44.7972       -40.0%
          -0.5  x load         17.9189           53.7567       -66.7%
          -1.0  x load          0.0000           71.6756      -100.0%

    **Exact until the load reverses, then it collapses, and every error is in
    the direction that says the part is safe.** At the bottom of that table
    the load is fully reversed and the two ends have identical von Mises
    stress to every digit — measured `max|vm_up − vm_down| = 0.000000` over
    19 787 nodes — so the swing reads as zero and the part appears immortal.

    Three things make this the worst shape of defect this project has found.
    The failing case is *fully reversed loading*, which is exactly what a
    vibrating part lives in and the usual reason anybody asks about fatigue.
    The method is exactly right for an on-off load, which is what anybody
    would test first, so casual testing passes. And there is no signature in
    the output: an infinite life is a plausible number.

    The fix is to subtract the six-component stress **tensors** and only then
    reduce to one number. `results/fatigue.py` does that;
    `tests/unit/test_fatigue_stress_range.py` fails with exactly 0.0 without
    it, and V18 covers it against a closed form.

    **A mean stress must keep its sign and an amplitude must not.** How far
    the stress moved has no direction. The middle of the swing does, and it
    decides how damaging that swing is — pulling the material apart holds a
    crack open, pressing it together holds it shut. Measured at the same
    node: a cycle from zero to +2500 N reads a mean of −35.8378 MPa and one
    to −2500 N reads +35.8378, while unsigned von Mises reports 35.8378 for
    both. pyLife offers two conventions for that sign and **they are not
    interchangeable**: on the same part they disagree at 137 of 19 787 nodes.
    Every disagreement there was below 5.70 MPa against a 35.84 MPa peak and
    the governing node agreed either way, but that is a fact about that part.
    It is the engineer's choice, so both are offered and neither is silent.

    One measurement worth keeping for any future work on load cases.
    **CalculiX writes its results in `E12.5` — six significant figures — so
    two solves of the same part at different load levels are not exactly
    proportional.** Measured against the full-load case: the *negated* load
    departs by `0.000e+00`, because negating a value leaves its mantissa
    alone, while half the load departs by `2.620e-06`. A quantity built from
    the difference of two nearly equal load cases magnifies that by
    `(|a| + |b|) / |a − b|`. That is the results file, not the physics, and a
    tolerance covering it must be derived from it rather than tuned until a
    build passes.

23. **The percentile that exists to stop us optimising the mesh is itself a
    statistic about the mesh.** Raw peak stress is refused as an objective
    because it grows without limit at a singularity — V6 measured 19.8% and
    accelerating. The replacement is a high percentile of the nodal field.
    That fixes the singularity and introduces a quieter problem: **a
    percentile over nodes asks what the worst 1% of *nodes* see, and which
    nodes exist is a meshing decision.**

    Measured on `examples/l_bracket`, which pins its fillet refinement at
    2.0 mm while the global size shrinks — the identical design, identical
    loads, changing only the mesh:

        mesh size   nodes    raw peak   stress_max_mpa   factor of safety
            8.0     14 123    71.4534       69.6897           2.2959
            4.5     30 543    71.7266       67.8165           2.3593
            2.8     78 836    71.4716       58.9137           2.7158

    The raw peak is settled to ±0.2%. The percentile falls **15.5%** and is
    still moving, and **the factor of safety rises 18% — the reassuring
    direction.** The example constrains that factor to be at least 2, so
    which mesh you happened to choose moves a design across its own
    acceptance line.

    The mechanism, measured directly: the share of nodes above 60 MPa falls
    from **4.895% to 0.908%** over that refinement. The hot region keeps
    roughly its node count because its size is pinned; everything else gains
    nodes; so the hot nodes are diluted out of the top percentile. Refining
    the same part *uniformly* moves the percentile only **2.2%**. So this is
    not a property of percentiles in general — it is what happens when part
    of the mesh refines and part of it does not, which is precisely what a
    local refinement is for and what the docs recommend at a fillet.

    **Where the safety net had a hole.** `openoptima converge` already tracks
    `stress_max_mpa`, so the tool would have shown this. V6, the benchmark
    that proves the tool works, runs a cantilever with a **uniform** mesh —
    so the one configuration that breaks the percentile was never exercised.
    A tool that watches the right number is worth nothing if no benchmark
    ever puts it in front of the failing case.

    `tests/unit/test_stress_measure_mesh_dependence.py` holds the mechanism
    in place without a solver. **Nothing has been changed about the stress
    measure.** Weighting the percentile by material volume rather than by
    node count would make it a statement about the part — measured on the
    same sweep, the volume-weighted figure lands within 0.6% whether the part
    was refined uniformly or locally, where the node-based one differs by 8%.
    But changing it moves every number this project has ever reported, so it
    is the project owner's decision and not a tidy-up.

## What an agent must not decide alone

Raise these with a human rather than choosing:

- allowable stresses, safety factors, or any acceptance criterion;
- whether a mesh is converged enough to trust;
- loosening a verification tolerance;
- whether a design is safe to manufacture.

The software's job is to compute and present. Judging is the engineer's.
