# Agent-ready backlog

Scoped units of work, each small enough for one focused session and each with a
checkable definition of done. Use the `agent-task` issue template when filing
these.

The ordering is deliberate: **trust before breadth.** Items 1–6 make existing
results defensible; nothing after them matters if those are wrong.

---

## Tier 1 — make the results defensible

### 1. Mesh convergence command

**Goal.** `openoptima converge <project> --run <id>` re-runs a design at several
mesh densities and reports whether the numbers have settled.

**Why.** The documentation tells users to check convergence manually, which
means nobody does. This is the largest gap between what the software reports and
what a reviewer would accept.

**Scope.** `optimisation/`, `cli/`, `reporting/`. Do not change the mesher.

**Acceptance.**
- Runs one design at ≥4 mesh sizes and reports displacement, strain energy,
  reaction and each stress measure at each.
- Reports Richardson-extrapolated values and observed order of convergence where
  the data support it.
- States plainly which quantities have converged and which have not — peak
  stress at a singularity never will, and saying so is the point.
- Verification test asserting the cantilever's displacement converges and its
  raw peak stress at the fixed face does not.

### 2. Verification benchmark V3 — plate with a hole

**Goal.** Verify stress concentration against the finite-width Howland solution.

**Why.** The only stress verification today is nominal bending on a prismatic
bar. This case also *demonstrates* the singularity argument behind ADR 6.

**Scope.** `tests/verification/`. The `plate_with_hole` template already exists.

**Acceptance.** Documented reference, tolerance and measured value added to
`verification-plan.md`; convergence behaviour of the peak recorded.

### 3. Verification benchmark V4 — thick cylinder under pressure

**Goal.** Verify the pressure load path against the Lamé solution.

**Why.** `*DLOAD` element-face pressure is currently covered only by unit tests
of the face lookup. No end-to-end evidence exists that pressure loading is
correct.

**Scope.** `tests/verification/`, plus a `thick_cylinder` template in
`geometry/occ/templates.py`.

**Acceptance.** Hoop and radial stress within a documented tolerance at several
radii; reaction check passes.

### 4. Linear buckling

**Goal.** Buckling factor as a constraint metric.

**Why.** The biggest safety gap. Minimising mass drives designs towards thin
slender sections — precisely what buckles — and the current analysis is blind to
it. `engineering-assumptions.md` says so; this closes it.

**Scope.** `solvers/calculix/deck.py` (`*BUCKLE` step), `frd.py` (eigenvalues),
`results/metrics.py`, `domain/model.py`.

**Acceptance.**
- `buckling_factor` available as a constraint metric.
- Verification test against the Euler critical load for a pinned column, with a
  documented tolerance.
- Multiple load cases handled; the metric is the envelope minimum.

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

## Tier 3 — breadth

### 10. OpenFOAM CFD plugin (cold plates)

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

### 11. Second structural backend

**Goal.** Code_Aster or Kratos behind the same adapter.

**Why.** Broadens capability and, more usefully, cross-checks CalculiX — running
the same verification case through two independent solvers is strong evidence.

### 12. Topology optimisation as an exploration mode

**Goal.** Density-based topology optimisation feeding the parametric workflow.

**Note.** The optimisation is the easy part. Extracting a boundary, enforcing
minimum feature sizes, producing a manufacturable CAD model and re-verifying it
on a body-fitted mesh is most of the work. Scope it accordingly.

---

## Standing rules for any of these

From `AGENTS.md`, repeated because they are the ones most often broken:

- Never identify a face by index; never guess an ambiguous region.
- Never conflate an infeasible design with an infrastructure error.
- Never widen a verification tolerance to make a build pass.
- Add anything that can change a number to `Project.setup_digest()` in the same
  commit, or stale cached results will be served as fresh.
- Every fixed engineering defect gets a regression test that fails without the fix.
