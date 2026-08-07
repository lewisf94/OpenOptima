# Agent-ready backlog

Scoped units of work, each small enough for one focused session and each with a
checkable definition of done. Use the `agent-task` issue template when filing
these.

The ordering is deliberate: **trust before breadth.** Items 1-6 make existing
results defensible; nothing after them matters if those are wrong.

Two longer-term directions run through this list and are worth reading
together, because they change what the software is for rather than just
adding to it:

- **3D printing** (items 10-13). A printed part is not equally strong in
  every direction, and the two things most likely to break one -- layer
  adhesion and fatigue -- are both invisible today.
- **Topology optimisation** (item 16), offered *alongside* parametric
  optimisation rather than replacing it, with the user choosing which to
  run. This is the only route to organic, generative-looking shapes.

---

## Tier 1 — make the results defensible

### 1-4. Convergence, benchmarks and buckling — DONE

- **Mesh convergence command.** `openoptima converge`. One caveat: it now
  tracks strain energy too, which the original acceptance criteria asked
  for and could not have at the time.
- **Verification benchmarks.** V4 (plate with a hole, Howland), V5 (thick
  cylinder, Lame), V6 (mesh convergence), V7 (load case independence),
  V9 (buckling validity). See `verification-plan.md` for measured values.
- **Linear buckling.** Verified against Euler, and V9 later found and
  fixed a defect where CalculiX silently skipped the lowest mode.

Three real defects were found by writing these, each of which produced a
confident wrong answer rather than an error: reaction totals summed across
directions a set did not restrain, cached results silently losing their
mesh summary, and the buckling mode skip.

### 5. Failure diagnosis in study reports

**Goal.** When a study has a high error rate, say why in one line.

**Why.** Users currently have to read run directories to find out that, say,
90% of failures were one ambiguous region.

**Scope.** `reporting/`, `doe/sensitivity.py`.

**Acceptance.** Report groups failures by code with the most common message and
a concrete suggested fix; a study with >20% errors carries a prominent warning
that the results may be biased.

### 6. Infeasibility mapping in DOE

**Goal.** Report which parts of the design space are infeasible and why.

**Why.** "39 of 53 rejected" is much less useful than "everything below
thickness_h ≈ 14 mm fails the factor of safety constraint".

**Scope.** `doe/sensitivity.py`, `reporting/`.

**Acceptance.** Per-variable feasible ranges reported; the dominant binding
constraint identified.

---

## Tier 2 — reach

### 7. Surrogate-assisted optimisation

**Goal.** Fit models to evaluated designs, use expected improvement to choose
infill points, cut the number of full solves needed.

**Scope.** New `surrogates/` package, `optimisation/study.py`.

**Acceptance.**
- Cross-validated model quality reported; a poor model must be visible, not
  silently trusted.
- **Every candidate design is validated with a real solve before it can appear
  on a reported front.** Non-negotiable.
- Benchmark: reaches a comparable front to plain NSGA-II with materially fewer
  evaluations, measured on the L-bracket.

### 8. Local web UI

**Goal.** Browser front end: 3D viewer, interactive face picking that *writes a
selector*, live Pareto plot, run monitor.

**Why.** Writing selectors by hand is the sharpest usability edge in the tool.
Picking a face and having the software propose a robust selector — then
verifying it across the design range — removes it.

**Scope.** New `web/` package. Must call the existing engine API only; no
physics or optimisation logic in the UI layer.

**Acceptance.** A bracket study can be set up, run and inspected without editing
YAML; generated selectors pass `doctor` across the design range.

### 9. Pairwise preference elicitation

**Goal.** Instead of asking for a trade rate, show two designs and ask which is
preferred; infer the rate from several answers.

**Why.** Users find "A: 24 kPa / 0.079 K/W, B: 27 kPa / 0.071 K/W — which?"
far easier than stating an exchange rate in the abstract.

**Scope.** `optimisation/pareto.py`, UI.

**Acceptance.** Inferred rate reproduces a known synthetic preference within
tolerance; the user can always see and override the inferred rule.

---

## Tier 2b — 3D printing and real materials

These share one purpose: making a result trustworthy for a **printed**
part. Today it is not. Do them in this order — the first is a correctness
fix and the others build on it.

### 10. Orthotropic material

**Goal.** A material that can be weaker between print layers than along
them, plus a build direction to say which way that is.

**Why.** The single biggest correctness gap for printed parts. A print is
typically 30–50% weaker between layers. OpenOptima assumes one strength in
every direction, so it can report a part as safe that peels apart along
its layers. This makes existing numbers *right*; it does not add a new
capability.

**Scope.** `domain/model.py` (Material), `schema/`, `solvers/calculix/deck.py`
(`*ELASTIC, TYPE=ORTHO` plus `*ORIENTATION`). Not the solver — CalculiX
supports this already.

**Acceptance.**
- Nine orthotropic constants accepted, and validated for thermodynamic
  admissibility — an arbitrary set of numbers is not a physical material,
  and an inadmissible set makes the solve produce nonsense rather than
  fail.
- Build direction is part of the project and part of `setup_digest()`.
- An isotropic material still produces byte-identical decks, so nothing
  already verified moves.
- Verification: a plate loaded along the layers and across them gives
  the ratio of stiffnesses the constants specify.
- Allowable stress becomes direction-dependent, or the factor of safety
  is knowingly conservative and says which direction it used.

### 11. Print rules as a trade-off

**Goal.** Overhang angle, minimum wall thickness and build-volume fit,
expressed as something the user trades against — not a hard gate.

**Why.** How much performance somebody gives up to avoid support material
is a personal call and differs by part and printer. The project owner's
own stated preference is to keep the performance and deal with supports by
hand. A hard rule would silently delete the design they wanted.

**Scope.** New `manufacturing/` module for the geometric checks,
`domain/objectives.py` to expose them as metrics.

**Acceptance.**
- Overhang angle, minimum wall and build-volume fit are computed as
  **metrics**, so the existing preference model can trade against them
  with no new machinery: hard limits, targets, desirability and trade
  rules all work unchanged.
- A user can express "I will accept 10 g for each degree of overhang
  removed" using the trade rules that already exist.
- Printability can be switched off entirely and the front is unchanged
  from today.
- Only genuinely impossible cases are hard failures — a part that does
  not fit the bed cannot be printed at any price. Everything else is a
  trade.

### 12. Modal analysis

**Goal.** Natural frequency as a metric, so a part can be kept away from a
frequency that drives it.

**Why.** A drone's propellers spin at a known rate. An arm whose natural
frequency sits near it fails from vibration however strong it is
statically. OpenOptima cannot see this at all today.

**Scope.** `solvers/calculix/deck.py` (`*FREQUENCY` step), `dat.py` or
`frd.py` for the eigenvalues, `results/metrics.py`.

**Acceptance.**
- First N natural frequencies reported per load case, enveloped by taking
  the lowest.
- **Both** bounds supported: above a drive frequency for a stiff part, or
  deliberately below it for an isolated one. A single-sided limit is the
  wrong shape for this problem.
- Verification against the closed-form first frequency of a cantilever
  beam, with a documented tolerance.
- Frequencies are selected by step number, not by position — the same
  trap that produced a wrong reaction total and a wrong buckling factor.

### 13. Fatigue

**Goal.** Fatigue life from a user-supplied S-N curve and duty cycle.

**Why.** Very often what actually breaks a part in service, and the likely
failure mode for a vibrating drone arm.

**Do this after modal, not before.** For a vibrating part the cycles come
from resonance, and the natural frequency is what tells you how many
cycles per second the part actually sees. Doing fatigue first means
guessing a number modal would have given you.

**Scope.** New `results/fatigue.py`, plus a cycle definition in
`domain/model.py` and the schema. No solver change: fatigue is
post-processing on stresses that already exist.

**The three pieces.**

**1. A stress range, not a stress.** The static solve gives one state.
Fatigue is driven by how far stress swings each cycle: 0 to 100 MPa is a
different problem from 45 to 55 MPa, even though the peak is similar. This
maps onto the load cases that already exist. Either name a pair of load
cases as the two ends of a cycle, or give one case plus a ratio saying
what it swings between.

**2. An S-N curve.** The published relation between stress amplitude and
how many cycles the material survives. Basquin form or a table.

**3. Damage accumulation** when more than one cycle type applies. Miner's
rule sums the fraction of life each one uses.

**The awkward part, which must be solved and not ignored.**

**Fatigue is driven by the peak stress at a concentration, and the peak is
exactly the number this project refuses to optimise.** Everything else
here uses a percentile, precisely because the raw peak at a sharp corner
is meaningless -- V6 measured it climbing 19.8% and still rising. But a
fatigue crack starts at the real peak, so a percentile is the wrong input.

The resolution comes from V4: a *real* concentration, with a modelled
radius, does converge -- it scattered inside 3.2% while the singularity
ran away. So fatigue may use a peak stress **only where that peak has been
shown to settle**. In practice:

- the stress-raising feature must be modelled with a real radius, not
  left as a sharp corner;
- the peak must be converged, checked with the machinery `openoptima
  converge` already provides;
- where it is not converged, fatigue **refuses to report**, the same way
  buckling refuses outside its validated range. A fatigue life computed
  from a mesh-dependent peak is a number that changes when you refine the
  mesh, and reporting it with a warning attached protects nobody.

This dependency is why fatigue sits behind the convergence work rather
than beside it.

**Acceptance.**
- The curve and the duty cycle are **supplied by the engineer**. There is
  no default curve: curves vary by material, surface finish and process,
  printed materials have thin published data that shifts with print
  settings, and a default would look authoritative while being wrong for
  most parts. That is worse than refusing.
- Every report states which curve was used, as `allowable_stress_basis`
  already does for static strength.
- Mean-stress correction is explicit and named. Goodman, Gerber and
  Soderberg give materially different answers, so a silent choice is a
  hidden assumption.
- **Refuses to report a life computed from an unconverged peak**, with a
  `FailureCode` of its own, classified as an error rather than an
  infeasible design.
- The reported life carries its own uncertainty in words. Miner's rule is
  known to be unreliable -- commonly out by a factor of three -- and a
  fatigue life quoted to three significant figures implies a precision
  that does not exist.
- Surface finish is an explicit input, not an assumption. On an
  as-printed surface the roughness knockdown is often larger than the
  layer-adhesion effect, and omitting it makes the answer optimistic,
  which is the dangerous direction.

## Tier 3 — breadth

### 14. OpenFOAM CFD plugin (cold plates)

**Goal.** Conjugate heat transfer behind the existing `Solver` protocol:
pressure drop, thermal resistance, temperature uniformity as metrics.

**Why.** This is the test of whether the core really is physics-agnostic. If it
requires changes to `doe/`, `optimisation/`, `storage/` or `cli/`, the
abstraction was wrong and that is worth knowing.

**Scope.** New `solvers/openfoam/`. Separate mesh recipe — do **not** force one
mesh to serve both CFD and FEA; their refinement requirements conflict.

**Acceptance.**
- Verification against a documented reference (developing laminar flow in a
  straight channel, or a published cold-plate case).
- Energy balance and mass conservation checked automatically, as the structural
  path checks force equilibrium.
- **No changes required** to the optimisation, DOE, storage or CLI layers.

### 15. Second structural backend

**Goal.** Code_Aster or Kratos behind the same adapter.

**Why.** Broadens capability and, more usefully, cross-checks CalculiX — running
the same verification case through two independent solvers is strong evidence.

### 16. Topology optimisation — the second way of designing a part

**Goal.** Density-based topology optimisation, offered *alongside* the
parametric workflow as a mode the user chooses.

**Why.** Parametric optimisation answers "I have a shape, what are the
best dimensions?" Topology optimisation answers "I have a problem, what
shape should it be?" Both are legitimate and neither subsumes the other.
It is also the only route to the organic, bone-like shapes people expect
from generative design — no amount of parametric optimisation produces
one, because the shape is fixed by the model the user wrote.

**This is an addition, not a replacement.** The parametric path must behave
exactly as it does now after this lands. Do not fold one into the other,
and do not make either the "default" that the other is a special case of.

**Scope.** New `topology/` package. Must not require changes to `doe/`,
`optimisation/`, `storage/` or `cli/` beyond adding a mode.

**Note on effort.** The optimisation itself is the easy part. Its raw
output is a density field — a fuzzy map of how much material belongs at
each point, not a shape. Extracting a surface from that, smoothing it
without discarding strength, enforcing a minimum feature size the process
can actually make, and producing a watertight solid is most of the work.
That conversion is where most open-source topology tools stop being
useful. Scope accordingly.

**Acceptance.**
- The user picks the mode; neither mode is forced and the parametric path
  is unchanged.
- Output is a watertight solid, not a density field.
- **Whatever comes out goes back through the ordinary evaluation pipeline
  on a body-fitted mesh before any number from it is reported.** A density
  field is an idea, not a verified part, and nothing may be reported from
  one directly.
- Minimum feature size is enforced and stated, so the result is
  manufacturable rather than merely optimal.
- Verification against a published benchmark, such as the MBB beam, where
  the expected material layout is documented.

### 17. Import CAD from SolidWorks, Fusion 360 or elsewhere

**Goal.** A geometry provider that reads a STEP file the user exported
from their own CAD package.

**Why.** Most people already have a model. Requiring them to rebuild it as
a template or a CadQuery script is a real barrier.

**Scope.** New provider in `geometry/`. The OCC kernel already reads STEP,
so this is mostly plumbing plus region resolution on an imported solid.

**Know this before starting.** An imported STEP file is a finished shape
with **no parameters in it** — the dimensions the CAD package used are not
in the file, only the resulting surfaces. Nothing can recover them.

That is a narrower limitation than it first appears, and it is worth being
precise about it, because the obvious reading ("imported models cannot be
optimised") is wrong. Two separate things are needed to run a study, and
only one of them is missing:

- **Where the loads and supports go.** *Not a problem.* The user picks
  faces in the 3D viewer (item 8) and OpenOptima writes a selector from
  what it sees there. This works exactly as well on an imported solid as
  on a template.
- **What the optimiser is allowed to change.** *This* is what a STEP file
  lacks. There are no dimensions in it to vary.

**But the user can supply their own.** OpenOptima can apply geometric
operations to an imported solid and vary *those*: a fillet radius on a
picked edge, a hole diameter on a picked cylindrical face, an offset on a
picked wall. OCC does all of these on an imported BREP, and the optimiser
rebuilds by re-applying them at each design point. This covers a large
share of real work, because optimising an existing part usually does mean
"make this boss thicker, this fillet bigger, this hole smaller" rather
than rebuilding it from scratch.

So the honest ladder for an imported model:

| Capability | Works? |
|---|---|
| Single evaluation of the design as drawn | yes |
| Pick load and support faces by clicking | yes |
| Vary features the *user* adds — fillet radii, hole diameters, offsets | yes |
| Starting envelope for topology optimisation (item 16) | yes |
| Vary the original CAD's own parameters | no |

Only the last needs a live link back to the originating package's API, so
OpenOptima can change a dimension and ask it to rebuild. That is a
per-vendor integration, works only where that software is installed and
licensed, and ties an open-source tool to a proprietary one. It is a
different project from reading a STEP file and must not be confused with
one.

**The hard part is not the import.** It is that a user's own operations
change the model's topology. Adding a fillet creates new faces and
destroys old ones, so a load face picked before the fillet may not exist
in the same form after it. This is the topological naming problem in its
sharpest form — worse than the template case, where the operations are
known in advance. Two consequences:

- A click must write a **selector**, never a face index. This is already
  required by item 8, and it matters more here.
- `doctor` must build the extremes of the design range *including* the
  user's added operations, and confirm every selector still resolves
  uniquely. That machinery exists; this is the case that will stress it.

**Acceptance.**
- STEP import produces a single valid solid, or fails with a clear reason.
- `doctor` works on an imported model: selectors resolve across the whole
  design range, and ambiguity is still refused rather than guessed.
- Feature operations (fillet, hole, offset) can be applied to an imported
  solid and driven as design variables, with selectors surviving the
  topology change they cause.
- The CLI and the app are honest about which rung of the ladder above a
  given project is on. Offering a parametric run with nothing to vary is
  worse than saying there is nothing to vary.
- Units are checked on import. A model in inches silently treated as
  millimetres is exactly the class of error this project exists to
  prevent.

---

## Standing rules for any of these

From `AGENTS.md`, repeated because they are the ones most often broken:

- Never identify a face by index; never guess an ambiguous region.
- Never conflate an infeasible design with an infrastructure error.
- Never widen a verification tolerance to make a build pass.
- Add anything that can change a number to `Project.setup_digest()` in the same
  commit, or stale cached results will be served as fresh.
- Every fixed engineering defect gets a regression test that fails without the fix.
