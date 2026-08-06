# Architecture

## What this is

An engineering optimisation platform, not an FEA program. It owns the search —
parametric geometry, design of experiments, multi-objective optimisation,
preference handling, provenance — and delegates the physics to established
open-source solvers behind adapters. Structural analysis via CalculiX is the
first physics backend; the core knows nothing about it.

## The shape of it

```
                    ┌──────────────────────────────┐
                    │  project.yaml (versioned)    │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  domain/   pure data + rules │  no gmsh, no solver,
                    │  variables, regions, loads,  │  no numpy, no database
                    │  objectives, failures        │
                    └──────────────┬───────────────┘
                                   ▼
   ┌────────────┬──────────────────┴──────────────┬─────────────┐
   ▼            ▼                                 ▼             ▼
┌────────┐ ┌──────────┐                    ┌───────────┐ ┌───────────┐
│geometry│ │ meshing  │                    │  solvers  │ │  storage  │
│ occ    │ │  gmsh    │                    │ calculix  │ │  sqlite   │
│cadquery│ │          │                    │ analytic  │ │           │
└────┬───┘ └────┬─────┘                    └─────┬─────┘ └─────┬─────┘
     └──────────┴────────────┬───────────────────┴─────────────┘
                             ▼
                  ┌─────────────────────┐
                  │ evaluation pipeline │  state machine, run dirs,
                  │ + cache + retries   │  manifests, provenance
                  └──────────┬──────────┘
                             │  EvaluationResult  ← the only thing above sees
                             ▼
              ┌──────────────┴──────────────┐
              ▼                             ▼
       ┌────────────┐               ┌───────────────┐
       │ doe        │               │ optimisation  │
       │ sampling   │               │ pymoo NSGA-II │
       │ sensitivity│               │ pareto, knee, │
       └────────────┘               │ preferences   │
                                    └───────┬───────┘
                                            ▼
                                    ┌───────────────┐
                                    │ reporting/CLI │
                                    └───────────────┘
```

## Layers

**`domain/`** — the problem, stated in plain Python. Design variables, semantic
regions, materials, load cases, objectives, constraints, preferences, the
failure taxonomy, results. It imports nothing external, so it loads in
milliseconds and can be reasoned about without a CAE stack installed. A test
enforces this.

**`schema/`** — the versioned on-disk format. Pydantic models mirror the YAML
and exist to produce a precise error with a path (`load_cases.0.loads.1.region:
field required`) instead of a `KeyError` from deep inside the pipeline. Unknown
keys are rejected: a typo must not silently fall back to a default and change
the physics.

**`geometry/`** — providers turning a design vector into a solid. The built-in
`occ` provider uses gmsh's OpenCASCADE kernel; `cadquery` is optional. Both
produce a `GeometryArtifact`.

**`regions/`** — resolving selectors to faces by geometric signature. The most
important module in the repository; see below.

**`meshing/`** — gmsh with size fields, quality gates and a retry ladder.

**`solvers/`** — adapters. `calculix` is real; `analytic` is a closed-form
stand-in for CI on machines without a solver, and is loudly labelled as not
being one.

**`evaluation/`** — the state machine that runs a design through all of the
above, classifies what happened, writes a run directory and manifest, and
caches.

**`doe/`, `optimisation/`** — sampling, sensitivity, NSGA-II, Pareto analysis
and preference handling.

## Three decisions that shape everything

### 1. Regions are found, never numbered

`Face12` before a fillet change is not `Face12` after it. Storing a face index
in a project means a load will eventually migrate to the wrong face and produce
a converged, plausible, wrong answer.

So a region is a *selector* — required properties plus a scoring rule — and it
is re-resolved against the real geometry on every evaluation. If two faces score
within the ambiguity margin, the evaluation stops rather than guessing.

`openoptima doctor` builds the extremes of the design space and checks every
selector still resolves uniquely there, so the failure surfaces before a study
rather than 200 evaluations into one.

### 2. A bad design and a broken run are different things

```
INFEASIBLE   the design is bad          → tell the optimiser, it should learn
ERROR        we could not find out      → retry; never let the optimiser
                                          conclude anything from it
```

If a solver crash is reported as "a terrible design", the search learns to avoid
a perfectly good region of the design space because of an infrastructure
problem. `domain/failures.py` holds the taxonomy; every failure carries a code
and every code has a classification.

### 3. The Pareto front is the answer; preferences rank it

A weighted score (`0.6 × strength + 0.4 × weight`) changes meaning when units
change and cannot reach concave parts of a front. OpenOptima always produces the
front, then offers four levels of preference to *rank* it:

| Level | Mechanism | Answers |
|---|---|---|
| 1 | Hard limits | "never acceptable" |
| 2 | Targets | "what I am aiming for" |
| 3 | Desirability with saturation | "past this, more buys nothing" |
| 4 | Trade rules | "I will pay X of this for Y of that" |

Plus `marginal_rates`, which reports what each step along the front actually
costs — the evidence behind "the first 16 g buys a lot, the next 148 g buys
almost nothing".

## Concurrency

Parallelism is by **process**. gmsh holds global C state and CalculiX is a
subprocess; threads would serialise or corrupt each other. The parent
pre-allocates run directory ids and hands each worker a self-contained payload.

## Reproducibility

Every evaluation writes a run directory with geometry, mesh, solver deck, raw
solver output and a manifest recording tool versions, the setup digest and the
design. The cache key covers the design vector *and* the setup digest *and* the
tool versions — reusing a number computed under different physics is not a cache
hit, it is a wrong answer delivered quickly.

## Extending it

Adding a physics backend means implementing one protocol
(`solvers/base.py::StructuralSolver`) and registering it in
`solvers/__init__.py::create_solver`. Nothing in `doe/`, `optimisation/`,
`storage/` or `cli/` should need to change. That boundary is deliberate: it is
how the OpenFOAM cold-plate module arrives without a rewrite.
