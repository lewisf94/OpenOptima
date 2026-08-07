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
  and where it is loaded, and
  OpenOptima decides where material should exist at all. This is what
  produces the organic, bone-like shapes people associate with generative
  design. Use it when you do not yet know the shape.

A likely workflow uses both: topology optimisation to find the shape,
then a parametric model of that shape to tune it and verify it properly.
Neither mode should ever be forced on a user, and the parametric path must
keep working exactly as it does now.

**A second intended direction is 3D printing.** OpenOptima currently
assumes a material that is equally strong in every direction, which a
printed part is not, and it has no idea what a printer can and cannot
make. The "Printing and real-world materials" section below closes that
gap. Until it does, a result for a printed part should be treated with
real caution — the two things most
likely to break such a part, layer adhesion and fatigue, are both
invisible today.

## Now (v0.1) — done

- Parametric geometry (OCC templates, optional CadQuery), and regions
  found by shape, not by face number
- Gmsh meshing, with size fields, quality gates, and automatic retries at
  a coarser size when meshing fails
- CalculiX linear static analysis, consistent loads, equilibrium checking
- DOE, sensitivity, NSGA-II, Pareto front, knee point, preference model
- SQLite storage, evaluation cache, run manifests, and provenance (a full
  record of what produced each result)
- CLI (`doctor`, `evaluate`, `doe`, `optimise`, `report`, `templates`)
- Cantilever verified to 0.98% against Timoshenko beam theory
- Linear buckling, verified to 0.11% against Euler, with an automatic
  cross-check that refuses results outside its validated range
- Desktop app: a local browser interface with setup checks, live
  progress, a Pareto chart and trade-off tables, packaged for Windows
  with PyInstaller

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
4. **Modal analysis**, as a constraint: keep the part's natural frequency
   (the frequency it vibrates at, if disturbed) away from a limit.

   Every object has frequencies it prefers to vibrate at. If something
   drives a part at one of those frequencies, small forces build into
   large ones and it shakes itself apart. A drone is the obvious case:
   the propellers spin at a known rate, and an arm whose natural
   frequency sits near it will fail from vibration however strong it is
   on paper. This is a real design driver, and today OpenOptima cannot
   see it at all.

   CalculiX supports this directly with a `*FREQUENCY` step, so the work
   is a new step type, a parser for the frequencies, and a new metric.
   Note it needs *both* bounds in practice: above the drive frequency for
   a stiff part, or deliberately below it for an isolated one. A
   single-sided limit would be the wrong shape.

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

3. **Fatigue.** Failing after many load cycles, at a stress the part
   would easily survive if applied once. This is very often what
   actually breaks a part in service, and a drone arm is the textbook
   case: the propellers vibrate it millions of times.

   It is deliberately last of this group, because it is the one that
   cannot be done without a human decision. It needs an S-N curve — a
   published table of how many cycles a material survives at a given
   stress — and those curves vary by material, surface finish, and
   manufacturing process. Printed materials have far less published
   fatigue data than wrought ones, and what exists depends heavily on
   print settings. It also needs an assumption about how the load
   cycles, which is a statement about the part's service life rather
   than about the part.

   So the software's job here is to compute a fatigue life *given* a
   curve and a duty cycle the engineer supplies, and to say plainly
   which curve it used. **Choosing the curve is not a decision
   OpenOptima should make**, and a default one would be worse than
   none: it would look authoritative and be wrong for most materials.

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
3. **Topology optimisation — the second way of designing a part.**
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

   **Scope it carefully. The optimisation is the easy part.** Its raw
   output is a density field — a fuzzy map of how much material belongs
   at each point in space, not a shape at all. Turning that into
   something printable and re-verifiable means extracting a surface from
   the fuzz, smoothing it without throwing away strength, enforcing a
   minimum feature size the manufacturing process can actually make, and
   producing a watertight solid. That conversion is most of the work,
   and it is where most open-source topology tools stop being useful.

   Whatever comes out must go back through the ordinary evaluation
   pipeline on a proper body-fitted mesh before any number from it is
   reported. A density field is not a verified result.

4. **Import CAD from SolidWorks, Fusion 360 or another package.** Both
   export STEP, which the geometry layer already reads, so a "use my own
   model" provider is a small piece of work on its own.

   **The catch is worth knowing before anyone starts.** An imported STEP
   file is a finished shape with no parameters in it. The dimensions
   that CAD package used are not in the file — only the resulting
   surfaces. So an imported model **cannot be parametrically
   optimised**: there is nothing for the optimiser to vary. What it can
   do is:

   - a single evaluation, to check a design somebody has already drawn;
   - act as the starting envelope for topology optimisation (item 15),
      which needs a region of space rather than a set of parameters;
   - serve as a fixed part in an assembly.

   Getting parametric optimisation from imported CAD is a much larger
   job: it needs the native file plus a live link back to the CAD
   package's own API, so OpenOptima can change a dimension and ask that
   package to rebuild. That is a per-vendor integration, only works
   where the software is installed and licensed, and would tie an
   open-source tool to a proprietary one. Worth doing eventually,
   perhaps, but it is a different project from reading a STEP file and
   should not be confused with one.

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
- **A non-commercial licence.** That would make this project
  source-available, not open source.

## Where help is most valuable

- Verification benchmarks with published reference values — no other
  contribution is as valuable as this one
- Geometry templates for common parts
- Testing on real parts, and reporting where region selectors are
  awkward to write
- Windows and macOS packaging
