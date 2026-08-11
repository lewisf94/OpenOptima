# Roadmap

This roadmap is ordered by value, and is deliberately conservative about
what comes first. The guiding principle: **make the existing pipeline
trustworthy, before it grows any bigger.** A wider tool that produces
numbers nobody can defend is worth less than a narrower one that produces
numbers people can defend.

## Where this is going

The intended end state is **two ways of designing a part, and the user
chooses which to run**. They are not alternatives to each other and one
does not replace the other:

- **Parametric optimisation** (what exists today). You describe a shape
  with a handful of dimensions and OpenOptima finds the best values for
  them. Use it when you already know the shape and need the numbers right.
- **Topology optimisation** (not built — see "Topology optimisation"
  below). You describe the space the part may occupy, where it is held
  and where it is loaded, and OpenOptima decides where material should
  exist at all. This is what produces the organic, bone-like shapes
  people associate with generative design. Use it when you do not yet
  know the shape.

A likely workflow uses both: topology optimisation to find the shape,
then a parametric model of that shape to tune it and verify it properly.
Neither mode should ever be forced on a user, and the parametric path must
keep working exactly as it does now.

**A second intended direction is 3D printing.** OpenOptima currently
assumes a material that is equally strong in every direction, which a
printed part is not, and it has no idea what a printer can and cannot
make. The "Printing and real-world materials" section below closes that
gap. Until it does, a result for a printed part should be treated with
real caution — the two things most likely to break such a part, layer
adhesion and fatigue, are both invisible today.

## How much of this needs writing

Not all of it, and that changes the shape of everything below.

**OpenOptima is the control system, not the calculator.** Its job is to
connect CAD, meshing, solving, optimisation, fatigue, topology and
manufacturing rules into one automated system, and to check every number
that comes out of them. The specialist calculations belong to specialist
software that already exists and is maintained.

So the question for each item below is not "how do we implement this?"
It is:

1. What already does this?
2. What does OpenOptima need to connect to it?
3. How would we verify it?

That produces far less code and a more credible result.
[`capability-audit.md`](capability-audit.md) answers those three
questions for every item here, and nothing new should be started until
it has an entry there.

What no other software provides is the part this project is actually
built around: keeping a named face attached to the right surface while
the shape changes, telling a bad design apart from a broken run, and
proving every number against a published answer.

One rule applies to every item below. **Reusing a tool saves writing the
code, not proving the answer.** An external tool is a calculation
provider, never an authority — "beso says this is optimal" is not
"OpenOptima verified this", and only the second is a claim this software
may make.

## Now (v0.1) — done

- Parametric geometry (OCC templates, optional CadQuery), and regions
  found by shape, not by face number
- Gmsh meshing, with size fields, quality gates, and automatic retries at
  a coarser size when meshing fails
- CalculiX linear static analysis, consistent loads, equilibrium checking
- DOE, sensitivity, NSGA-II, Pareto front, knee point, preference model
- SQLite storage, evaluation cache, run manifests, and provenance (a full
  record of what produced each result)
- CLI (`doctor`, `evaluate`, `doe`, `optimise`, `topology`, `report`,
  `templates`)
- Cantilever verified to 0.98% against Timoshenko beam theory
- Linear buckling, verified to 0.11% against Euler, with an automatic
  cross-check that refuses results outside its validated range
- Desktop app: a local browser interface with setup checks, live
  progress, a Pareto chart and trade-off tables, packaged for Windows
  with PyInstaller
- Topology optimisation, run through `beso`, with the result turned back
  into a sealed shape and then **analysed** — so it reports a real stress
  and factor of safety rather than only a picture. See item 3 under
  "Later" for what this covers and what it still does not.
- Natural frequencies, verified against published beam theory, refusing to
  answer for a part the supports do not hold still

## Next (v0.2) — trust

Nothing new here. This tier makes what already exists defensible.

1. ~~Mesh convergence workflow~~ — **done.** `openoptima converge`.
2. ~~Verification benchmarks V4–V7~~ — **done.** Plate with a hole,
   thick cylinder under pressure, mesh convergence, load case
   independence. See `verification-plan.md`.
3. ~~Buckling validity boundary (V9)~~ — **done**, and it found a defect
   rather than a boundary. The trigger was never slenderness; it was the
   buckling factor itself. Fixed at its root.
4. **Better failure diagnostics.** When a study has a high error rate,
   OpenOptima should say why in one line. Today, the user has to read run
   directories to find out.
5. **`slenderness_limit` is still a human decision.** The default of 150
   was set against the defect V9 fixed, and measurements now run clean to
   433. Widening it is an engineering judgement, so it has deliberately
   been left alone.

## Then (v0.3) — reach

1. **Surrogate-assisted optimisation.** This would fit approximate models
   to the DOE results, and use expected improvement (a standard rule for
   picking the most informative next design to test) to choose each next
   candidate. Every candidate design would still be validated with a real
   solve. OpenOptima must never export a design whose numbers came only
   from an approximate model.
2. **More load types:** bearing loads, remote loads, bolt preload, and
   thermal expansion.
3. **Local web UI.** A browser front end with a 3D viewer, interactive
   face-picking to write selectors, a live Pareto plot, and pairwise
   design comparison. Instead of asking for a number, this would show you
   two designs and ask which one you prefer.
4. ~~**Modal analysis**~~ — **done.** `modal.enabled`, constrained as
   `natural_frequency_hz`, verified as V14 to +0.27% against the published
   cantilever answer. Keeps the part's natural frequency (the rate it
   vibrates at, if disturbed) away from a limit.

   Every object has frequencies it prefers to vibrate at. If something
   drives a part at one of those frequencies, small forces build into
   large ones and it shakes itself apart. A drone is the obvious case:
   the propellers spin at a known rate, and an arm whose natural
   frequency sits near it will fail from vibration however strong it is
   on paper. This is a real design driver, and today OpenOptima cannot
   see it at all.

   CalculiX supports this directly with a `*FREQUENCY` step, so the work
   was a new step type, a parser for the frequencies, and a new metric.
   Both bounds are supported, as planned: above the drive frequency for a
   stiff part, or deliberately below it for an isolated one.

   Two things cost more than the step itself, and both were found by
   running it rather than by reading about it. A `*FREQUENCY` step writes
   a mode shape into the results file for every mode without being asked,
   which would have shifted every later result along and handed a load
   case a mode shape instead of an answer. And a part the supports do not
   hold still comes back with modes at zero hertz and a successful exit
   code, which is now refused rather than filtered. Traps 13 and 14 in
   `AGENTS.md`.

   What it still does not do: no stress stiffening, no damping so no
   amplitude, and no forced response. See
   [`engineering-assumptions.md`](engineering-assumptions.md).

   Do this **before** fatigue. The two are linked: vibration is what
   drives the load cycles that cause fatigue, so the natural frequency
   tells you how many cycles the part actually sees.

## Printing and real-world materials (v0.3–v0.4)

These are grouped because they share one purpose: making a result
trustworthy for a **3D-printed** part. Today it is not. OpenOptima assumes
material that is equally strong in every direction, and knows nothing
about what a printer can make.

1. **Orthotropic material — the single biggest correctness fix for
   printed parts.** A printed part is not equally strong in every
   direction. It is markedly weaker *between* layers than *along* them,
   often by 30 to 50 per cent, because adjacent layers are fused rather
   than continuous. OpenOptima currently assumes one strength in all
   directions, so it can report a part as safe that will peel apart
   along its print layers under a load it appeared to survive.

   CalculiX supports this natively (`*ELASTIC, TYPE=ORTHO`), so the work
   is in the material model, the deck writer, and the schema — not in
   the solver. It also needs the **print direction** as part of the
   project, since "between layers" is meaningless without knowing which
   way the part was built. A sensible extension is to let the build
   direction itself be a design variable, because rotating a part on the
   bed can matter more than any dimension on it.

   This one is a correctness fix rather than a new capability: it makes
   existing numbers right, instead of adding new ones.

2. **Print rules as a trade-off, not a hard limit.** Overhang angle,
   minimum wall thickness for the nozzle, and build-volume fit.

   **These must be adjustable, not absolute.** How much performance
   somebody will give up to avoid support material is a personal call,
   and it differs by part and by printer. A designer who is happy to
   remove supports by hand should be able to take the lighter, stronger
   part; another may want a shape that prints unattended.

   The mechanism for this already exists and should be reused rather
   than reinvented: printability becomes an **objective you trade
   against**, exactly like mass, and the existing preference model
   handles the rest. A user can then say "I will accept 10 grams for
   every degree of overhang removed", or set a target and let
   OpenOptima show what it costs, or ignore printability entirely and
   take the best-performing design. The Pareto front already answers
   "what does this cost me?", so printability should be one more axis on
   it rather than a gate that silently deletes good designs.

   Hard limits should stay available for the genuinely impossible — a
   part that does not fit the bed cannot be printed at any price — but
   the default should be a trade, not a refusal.

   The geometry underneath this is already solved elsewhere.
   [`trimesh`](https://github.com/mikedh/trimesh) measures the angle of
   every surface against the build direction, and checks that a shape is
   sealed. Use it rather than writing the same maths here. What belongs
   here is turning its answer into a number the optimiser can trade
   against.

3. **Fatigue.** Failing after many load cycles, at a stress the part
   would easily survive if applied once. This is very often what
   actually breaks a part in service, and a drone arm is the textbook
   case: the propellers vibrate it millions of times.

   **Do this after modal analysis, not before.** For a vibrating part the
   cycles come from resonance, so the natural frequency is what tells you
   how many cycles per second the part actually sees. Doing fatigue first
   means guessing a number modal would have given you.

   **What it needs.** Three things, none of which exist yet:

   - **A stress range, not a stress.** Today's analysis gives one steady
     answer. Fatigue is driven by how far stress *swings* each cycle: a
     part going from 0 to 100 MPa and back is in a different situation
     from one hovering between 45 and 55, even though the peak is
     similar. This fits the load cases that already exist — name two of
     them as the ends of a cycle.
   - **An S-N curve** — a published table of how many cycles a material
     survives at a given stress.
   - **A way to add up damage** when more than one kind of cycle applies.

   The last two are standard, published methods, and a maintained library
   already implements them:
   [`pylife`](https://github.com/boschresearch/pylife), from Bosch
   Research, under a licence this project can use. It handles S-N curves
   and damage summation, and works on results from a mesh. Bind to it
   rather than writing the curve fitting and the damage arithmetic again.
   See [ADR 9](adr/0009-build-versus-reuse.md).

   **One awkward problem has to be solved, not ignored.** Fatigue cracks
   start at the *peak* stress — and the peak is exactly the number this
   software refuses to use, because at a sharp corner it is meaningless
   and grows forever as the mesh is refined. But V4 showed that a *real*
   rounded feature does settle to a real number. So fatigue may use a
   peak stress only where that peak has been shown to settle: the feature
   must be modelled with a real radius, and `openoptima converge` must
   confirm the number has stopped moving. Where it has not, fatigue
   **refuses to answer**, exactly as buckling does outside its verified
   range. A fatigue life computed from a number that changes with the
   mesh is not a result.

   **What OpenOptima must not decide.** The software's job is to compute
   a life *given* a curve and a duty cycle the engineer supplies, and to
   say plainly which it used. Choosing the curve is not its decision, and
   a default would be worse than none — it would look authoritative and
   be wrong for most materials. Two further honesty requirements: the
   method for adding up damage is known to be unreliable, commonly out by
   a factor of three, so a life quoted to three significant figures
   implies a precision that does not exist; and the roughness of an
   as-printed surface knocks fatigue strength down by more than layer
   weakness does, so leaving it out makes the answer optimistic — the
   dangerous direction.

5. **Initial imperfections, and what to do instead.** Linear buckling
   assumes a perfectly straight part loaded exactly down its centre. Real
   parts have a slight bow and slightly off-centre loads, and both make a
   part buckle sooner than the calculation says.

   The textbook fix is to model it: nudge the mesh by a fraction of the
   buckling shape, then re-solve allowing for large movement. That needs
   **nonlinear analysis**, which OpenOptima does not do, and it brings
   three problems for a design search. A nonlinear solve is far slower. It
   can fail to converge, which inside a 500-design run means a design
   fails for a reason that has nothing to do with the design. And the size
   of the imperfection to assume is a judgement: too small is optimistic,
   too large fails everything.

   In practice engineers do not model it. They set a higher buckling
   margin, which is why buckling margins are conventionally well above
   stress margins. That is an acceptance criterion, so it belongs to the
   engineer and not to this software.

   Recorded so the question is not reopened without this context. If it is
   done, it belongs behind nonlinear analysis, not in front of it.

## Later (v0.4+) — breadth

1. **CFD plugin — cold plates.** A cold plate is a component that removes
   heat, often from electronics, by passing coolant through internal
   channels. This plugin would put OpenFOAM's `chtMultiRegionSimpleFoam`
   solver behind the same `Solver` protocol used for structural analysis,
   reporting pressure drop, thermal resistance, and temperature
   uniformity as metrics. The core of OpenOptima is already meant to work
   with any kind of physics, not just structural analysis. This plugin is
   the real test of whether that is true. Note: the version of OpenFOAM
   packaged by many Linux distributions is old. This plugin will likely
   need the OpenFOAM Foundation's own package, or a container.
2. **A second structural solver.** This would put Code_Aster or Kratos
   behind the same adapter as CalculiX. This would broaden what
   OpenOptima can do, and let OpenOptima cross-check its own results
   against a second, independent solver.
3. **Integrate topology optimisation through an external solver — the second
   way of designing a part. Landed; what is left is noted at the end of
   this item.** Note the wording. This item is not "implement topology
   optimisation"; the algorithm is somebody else's, and the integration
   and the checking are ours.
   Unlike the rest of OpenOptima, which adjusts a few chosen dimensions,
   topology optimisation decides where material should exist at all,
   without assuming a shape in advance. You give it the space the part
   may occupy, where it is held, and where it is loaded. It removes
   everything that is not earning its weight. This is what produces the
   organic, bone-like shapes people associate with generative design,
   and it is the only way to get them — no amount of parametric
   optimisation will ever produce one, because the shape is fixed by the
   model you wrote.

   **This is an addition, not a replacement.** Both modes stay, and the
   user chooses which to run. Parametric optimisation answers "I have a
   shape, what are the best dimensions?" Topology optimisation answers
   "I have a problem, what shape should it be?" Both are legitimate
   questions and neither subsumes the other. The parametric path must
   keep behaving exactly as it does now after this lands.

   They also combine well, and that is probably the most useful
   workflow: use topology optimisation to find a shape, rebuild it as a
   parametric model, then tune and verify that properly. A topology
   result on its own is an idea, not a verified part.

   **Do not write the optimiser.** One already exists that uses the same
   solver: [`beso`](https://github.com/calculix/beso), which drives
   CalculiX and is licensed compatibly. It takes a prepared analysis file
   as its input, and that file is exactly what OpenOptima's deck writer
   already produces. So this becomes an adapter around an existing tool,
   not a new solver. See [ADR 9](adr/0009-build-versus-reuse.md).

   One caution before its buckling mode is used here: `beso` can optimise
   for buckling, and OpenOptima found a defect in CalculiX's buckling
   solve that nothing in the wider ecosystem appears to guard against.
   **Measured, and `beso` does hit it**: a column whose real critical load
   is 14 409 N was reported as surviving 127 569 N, 8.86 times too high in
   the unsafe direction. Its buckling objective is therefore refused
   outright rather than passed on. See V11 in
   [`verification-plan.md`](verification-plan.md).

   **Scope it carefully. The optimisation is the easy part.** Its raw
   output is a density field — a fuzzy map of how much material belongs
   at each point in space, not a shape at all. Turning that into
   something printable and re-verifiable means extracting a surface from
   the fuzz, smoothing it without throwing away strength, enforcing a
   minimum feature size the manufacturing process can actually make, and
   producing a watertight solid. That conversion is most of the work,
   and it is where most open-source topology tools stop being useful.
   **That conversion is the part worth building here.**

   Whatever comes out must go back through the ordinary evaluation
   pipeline on a proper body-fitted mesh before any number from it is
   reported. A density field is not a verified result. **That is done**:
   `openoptima topology --analyse` re-meshes the shape, puts the same
   loads and supports back on, and reports a real stress, deflection and
   factor of safety. On the test case that number matters — the shape kept
   49.7% of the material and its factor of safety fell from 1.15 to 0.63,
   which nothing in the topology run itself would have told anybody.

   **What is still missing.** The result comes out as a triangle mesh, so
   it can be printed but not machined or cast without somebody redrawing
   it. Turning it back into a parametric CAD model is not automated and is
   not planned to be. A rounded blend also cannot be found on a triangle
   mesh, because it meets the faces it joins smoothly and leaves no crease
   to find it by, so a selector that asks for a blend by its radius will
   not match on a topology result.

4. **Import CAD from SolidWorks, Fusion 360 or another package.** Both
   export STEP, which the geometry layer already reads, so a "use my own
   model" provider is a small piece of work on its own.

   **What an imported file does and does not carry.** A STEP file is a
   finished shape with no parameters in it. The dimensions that CAD
   package used are not in the file — only the resulting surfaces — and
   nothing can recover them.

   That limitation is narrower than it first sounds. Running a study
   needs two separate things, and only one of them is missing:

   - **Where the loads and supports go** — not a problem. You pick faces
     in the 3D viewer and OpenOptima writes down a description of what
     it finds there. This works just as well on an imported shape as on
     a built-in template.
   - **What the optimiser may change** — this is the missing half. There
     are no dimensions in the file for it to vary.

   **You can supply your own, though.** OpenOptima can apply its own
   operations to an imported shape and vary those: a fillet radius on an
   edge you pick, a hole diameter on a round face you pick, an offset on
   a wall you pick. That covers a large share of real work, because
   improving an existing part usually does mean "make this boss thicker,
   this fillet bigger, this hole smaller" rather than redrawing it.

   So, for an imported model:

   | Capability | Works? |
   |---|---|
   | Evaluate the design as drawn | yes |
   | Pick load and support faces by clicking | yes |
   | Vary features *you* add — fillets, holes, offsets | yes |
   | Use it as the envelope for topology optimisation | yes |
   | Vary the original CAD's own parameters | no |

   Only the last one needs a live link back to the CAD package's own
   interface, so OpenOptima could change a dimension and ask it to
   rebuild. That is a separate piece of work per vendor, only works where
   that software is installed and licensed, and ties an open-source tool
   to a proprietary one. It is a different project from reading a STEP
   file and should not be confused with one.

   **The real difficulty is not the import.** Adding a fillet changes the
   model's faces: it creates new ones and destroys old ones. A face you
   picked before adding the fillet may not exist in the same form
   afterwards. This is the hardest version of the problem the whole
   project is built around, and it is why a click must be stored as a
   *description* of a face rather than its number, and why `doctor` must
   check those descriptions still work at every size in the range.

5. **Assemblies and contact** — more than one part in a model, with
   surfaces that can touch.

## Explicitly not planned

- **An LLM (large language model) inside the numerical loop.** Coding
  agents build and maintain this software. They do not choose design
  points, judge whether a mesh has converged, or decide whether a part is
  safe. Those decisions are either fully deterministic, or belong to a
  human.
- **A "just trust it" mode.** Every result carries its assumptions, its
  stress measure, its mesh quality, and its provenance (the record of
  what produced it). Any feature that hides that information makes
  OpenOptima worse, not better.
- **A licence that bans business use.** This one needs explaining,
  because the words are misleading.

  OpenOptima is free, and always will be. "Open source" does not mean
  "not for business use" — it means the opposite. An open-source licence
  lets *anybody* use the software, including a company using it to design
  a part it sells. Permitting that is part of the definition. GPL-3.0,
  the licence this project uses, guarantees four freedoms to everyone: to
  run it, to read how it works, to change it, and to pass it on. It never
  asks who you are or what you are using it for.

  Some licences do add that restriction — "you may read this code but you
  may not make money with it". PolyForm Noncommercial is one. Software
  under those terms is called **source-available**: you can see the
  source, but it is not open source, because it discriminates on who may
  use it.

  OpenOptima will not adopt one, and — this is the part that bites in
  practice — **it cannot depend on a library that uses one either.**
  GPL-3.0 requires us to pass every freedom on to whoever receives the
  software. We cannot pass on a freedom a dependency has taken away. One
  such library has already been refused for this reason; see
  [`capability-audit.md`](capability-audit.md).

  Free to use and open source are both true here. Neither of them means
  non-commercial.

## Where help is most valuable

- Verification benchmarks with published reference values — no other
  contribution is as valuable as this one
- Geometry templates for common parts
- Testing on real parts, and reporting where region selectors are
  awkward to write
- Windows and macOS packaging
