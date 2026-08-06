# Roadmap

Ordered by value, and deliberately conservative about what comes first.
The principle throughout: **make the existing pipeline trustworthy before making
it bigger.** A wider tool that produces numbers nobody can defend is worth less
than a narrow one that produces numbers they can.

## Now (v0.1) — done

- Parametric geometry (OCC templates, optional CadQuery), semantic regions
- gmsh meshing with size fields, quality gates, retry ladder
- CalculiX linear static, consistent loads, equilibrium checking
- DOE, sensitivity, NSGA-II, Pareto front, knee point, preference model
- SQLite storage, evaluation cache, run manifests, provenance
- CLI (`doctor`, `evaluate`, `doe`, `optimise`, `report`, `templates`)
- Cantilever verified to 0.98% against Timoshenko beam theory
- Linear buckling, verified to 0.11% against Euler, with an automatic
  cross-check that refuses results outside its validated range
- Desktop app: a local browser interface with setup checks, live progress, a
  Pareto chart and trade-off tables, packaged for Windows with PyInstaller

## Next (v0.2) — trust

Nothing new; make what exists defensible.

1. **Mesh convergence workflow.** `openoptima converge <project> --run <id>`
   re-runs a design at several mesh densities and reports whether the numbers
   have settled. Currently the user is told to do this manually, which means
   nobody does.
2. **Verification benchmarks V3–V5** (see `verification-plan.md`): plate with a
   hole, thick cylinder under pressure, mesh convergence study. The pressure
   load path is only unit-tested today.
3. **Buckling validity boundary.** Buckling now exists and is verified, but the
   `slenderness_limit` of 150 rests on a handful of measured points. A
   systematic sweep of section size against slenderness would replace a
   defensible guess with a measured boundary, and would tell us whether the
   failure is CalculiX's eigenvalue solve or the tetrahedral formulation.
4. **Better failure diagnostics.** When a study has a high error rate, say why
   in one line rather than making the user read run directories.

## Then (v0.3) — reach

5. **Surrogate-assisted optimisation.** Fit models to the DOE, use expected
   improvement to choose infill points, validate every candidate with a real
   solve. Never export a design whose numbers came only from a surrogate.
6. **More load types.** Bearing loads, remote loads, bolt preload,
   thermal expansion.
7. **Local web UI.** Browser front end with a 3D viewer, interactive face
   picking to author selectors, live Pareto plot, and pairwise design
   comparison to elicit preferences without asking for a number.
8. **Modal analysis** as a constraint (first natural frequency above a limit).

## Later (v0.4+) — breadth

9. **CFD plugin — cold plates.** OpenFOAM `chtMultiRegionSimpleFoam` behind the
   same `Solver` protocol: pressure drop, thermal resistance, temperature
   uniformity as metrics. The core is already physics-agnostic; this is the test
   of whether that claim is true. Note the packaged OpenFOAM on many distros is
   old — this will want the Foundation repo or a container.
10. **Second structural backend.** Code_Aster or Kratos behind the same
    adapter, both to broaden capability and to cross-check CalculiX.
11. **Topology optimisation.** As an exploration mode feeding the parametric
    workflow, not replacing it — the output is a density field, and turning that
    into a manufacturable, remeshable, verifiable part is most of the work.
12. **Assemblies and contact.**

## Explicitly not planned

- **An LLM inside the numerical loop.** Coding agents build and maintain this
  software; they do not choose design points, judge convergence or decide
  whether a part is safe. Those are deterministic or human decisions.
- **A "just trust it" mode.** Every result carries its assumptions, its stress
  measure, its mesh quality and its provenance. Any feature that hides those is
  a step backwards.
- **A non-commercial licence.** That would make the project source-available
  rather than open source.

## Where help is most valuable

- Verification benchmarks with published reference values — the highest-value
  contribution by some distance
- Geometry templates for common parts
- Testing on real parts and reporting where region selectors turn out to be
  awkward to write
- Windows and macOS packaging
