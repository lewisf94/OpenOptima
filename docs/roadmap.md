# Roadmap

This roadmap is ordered by value, and is deliberately conservative about
what comes first. The guiding principle: **make the existing pipeline
trustworthy, before it grows any bigger.** A wider tool that produces
numbers nobody can defend is worth less than a narrower one that produces
numbers people can defend.

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

1. **Mesh convergence workflow.** The planned command
   `openoptima converge <project> --run <id>` would re-run a design at
   several mesh densities, and report whether the numbers have settled.
   Today, the documentation tells the user to do this by hand. In
   practice, nobody does.
2. **Verification benchmarks V4–V6** (see `verification-plan.md`): plate
   with a hole, thick cylinder under pressure, mesh convergence study.
   Today, the pressure load path is only unit-tested.
3. **Buckling validity boundary.** Buckling now exists, and is verified.
   But the `slenderness_limit` of 150 rests on only a handful of measured
   points. A systematic sweep of section size against slenderness would
   replace that defensible guess with a measured boundary. It would also
   tell us whether the failure comes from CalculiX's eigenvalue solve, or
   from the tetrahedral formulation itself.
4. **Better failure diagnostics.** When a study has a high error rate,
   OpenOptima should say why in one line. Today, the user has to read run
   directories to find out.

## Then (v0.3) — reach

5. **Surrogate-assisted optimisation.** This would fit approximate models
   to the DOE results, and use expected improvement (a standard rule for
   picking the most informative next design to test) to choose each next
   candidate. Every candidate design would still be validated with a real
   solve. OpenOptima must never export a design whose numbers came only
   from an approximate model.
6. **More load types:** bearing loads, remote loads, bolt preload, and
   thermal expansion.
7. **Local web UI.** A browser front end with a 3D viewer, interactive
   face-picking to write selectors, a live Pareto plot, and pairwise
   design comparison. Instead of asking for a number, this would show you
   two designs and ask which one you prefer.
8. **Modal analysis**, as a constraint: keep the part's natural frequency
   (the frequency it vibrates at, if disturbed) above a limit.

## Later (v0.4+) — breadth

9. **CFD plugin — cold plates.** A cold plate is a component that removes
   heat, often from electronics, by passing coolant through internal
   channels. This plugin would put OpenFOAM's `chtMultiRegionSimpleFoam`
   solver behind the same `Solver` protocol used for structural analysis,
   reporting pressure drop, thermal resistance, and temperature
   uniformity as metrics. The core of OpenOptima is already meant to work
   with any kind of physics, not just structural analysis. This plugin is
   the real test of whether that is true. Note: the version of OpenFOAM
   packaged by many Linux distributions is old. This plugin will likely
   need the OpenFOAM Foundation's own package, or a container.
10. **A second structural solver.** This would put Code_Aster or Kratos
    behind the same adapter as CalculiX. This would broaden what
    OpenOptima can do, and let OpenOptima cross-check its own results
    against a second, independent solver.
11. **Topology optimisation.** Unlike the rest of OpenOptima, which
    adjusts a few chosen dimensions, topology optimisation decides where
    material should exist at all, without assuming a shape in advance.
    This would be an exploration mode that feeds the existing parametric
    workflow, not a replacement for it. Its raw output is a density
    field — a map showing how much material belongs at each point in
    space, not yet a real shape. Turning that into a manufacturable,
    remeshable, and verifiable part is most of the actual work.
12. **Assemblies and contact** — more than one part in a model, with
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
