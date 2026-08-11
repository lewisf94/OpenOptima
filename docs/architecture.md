# Architecture

## What this is

OpenOptima is an engineering optimisation platform. It is not itself a
finite element analysis program. OpenOptima owns the search: parametric
geometry, design of experiments, multi-objective optimisation, preference
handling, and provenance (the record of exactly what produced each
result). It delegates the physics calculation to established open-source
solvers, through adapters. Structural analysis through CalculiX is the
first physics backend. The core of OpenOptima knows nothing about
CalculiX specifically.

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

**`domain/`** holds the problem itself, stated in plain Python: design
variables, regions, materials, load cases, objectives, constraints,
preferences, the failure taxonomy, and results. (Regions and the failure
taxonomy are explained in "Three decisions that shape everything", below.)
`domain/` imports nothing external. Because of this, it loads in
milliseconds, and you can reason about it without any CAE tool installed.
A test enforces this rule.

**`schema/`** holds the versioned on-disk format. Pydantic models mirror
the YAML. Their purpose is a precise, located error — for example,
`load_cases.0.loads.1.region: field required` — instead of a `KeyError`
from deep inside the pipeline. OpenOptima rejects unknown keys: a typo
must never silently fall back to a default value and change the physics.

**`geometry/`** holds the providers that turn a design vector into a solid
shape. The built-in `occ` provider uses Gmsh's OpenCASCADE kernel.
`cadquery` is optional. Both produce a `GeometryArtifact`.

**`regions/`** resolves selectors to faces, using each face's geometric
signature. This is the most important module in the repository — see
below.

**`meshing/`** wraps Gmsh: size fields, quality gates, and automatic
coarser retries when meshing fails.

**`solvers/`** holds the solver adapters. `calculix` is the real solver.
`analytic` is a closed-form stand-in, used for CI on machines without a
solver installed. `analytic` is clearly labelled as not being a real
solver, so nobody mistakes its output for a real result.

**`evaluation/`** holds the state machine that runs one design through
every layer above. It classifies what happened, writes a run directory
and manifest, and manages the cache.

**`doe/` and `optimisation/`** hold sampling, sensitivity analysis,
NSGA-II, Pareto front analysis, and preference handling.

## Three decisions that shape everything

### 1. Regions are found, never numbered

`Face12` before a fillet change is not `Face12` after it. Storing a face
index in a project means a load will eventually attach to the wrong face.
OpenOptima would then produce an answer that looks correct, and is wrong.

So OpenOptima defines a region with a *selector*: required properties,
plus a scoring rule. OpenOptima resolves this selector against the real
geometry again on every evaluation. If two faces score within the
ambiguity margin, the evaluation stops, instead of guessing.

`openoptima doctor` builds the part at the extremes of the design space,
and checks that every selector still resolves to exactly one face there.
This way, a setup mistake appears before a study starts — not 200
evaluations into one.

**Selectors can also be written the other way round** (`regions/describe.py`),
which is what turns a click in a 3D viewer into something storable: given a
face, produce the description that finds it again. It tries the fewest and
most stable properties first, makes each filter as loose as it can be while
still excluding the other faces, and returns nothing it has not resolved
through the real matcher and confirmed. Candidates are tested against the
part rebuilt at the extremes of its range, because a description written
from one shape is one nobody has tested — the bracket's fillet radius is
itself a design variable, so a radius written from its default matched
nothing at either end of that range. `openoptima faces` exposes this
without a viewer.

**The same selectors also work on a shape with no CAD behind it.** A
topology result arrives as a skin of triangles, so there is nothing to ask
what a face is; every face is measured from the triangles instead
(`regions/discrete.py`). That measurement has to put a face back together
first, because gmsh splits one flat face into several pieces — on a real
result, the top face of the part arrived as five. Once merged, the faces
that come out are the same kind of object as the CAD ones, so
`resolve_regions`, the deck writer, the solver and the metrics are all
reused unchanged.

The two routes are checked against each other, not assumed to agree: the
same bar analysed both ways matches to under 0.01% on deflection and on
stored energy (V13). Two things this route cannot do are stated in
[`verification-plan.md`](verification-plan.md) rather than hidden: a
rounded blend cannot be found at all, and a face that the optimiser has
cut in two really is two faces afterwards.

### 2. A bad design and a broken run are different things

```
INFEASIBLE   the design is bad          → tell the optimiser, it should learn
ERROR        we could not find out      → retry; never let the optimiser
                                          conclude anything from it
```

Suppose OpenOptima reports a solver crash as "a terrible design". Then the
search learns to avoid a perfectly good region of the design space,
because of an infrastructure problem — not because of the design.
`domain/failures.py` holds this taxonomy. Every failure carries a code,
and every code has a classification.

### 3. The Pareto front is the answer; preferences rank it

A weighted score, such as `0.6 × strength + 0.4 × weight`, changes meaning
whenever units change. It also cannot reach some good designs on the
front, no matter how the weights are set. So OpenOptima always produces
the full front first. Then it offers four levels of preference to *rank*
that front:

| Level | Mechanism | Answers |
|---|---|---|
| 1 | Hard limits | "never acceptable" |
| 2 | Targets | "what I am aiming for" |
| 3 | Desirability with saturation | "past this, more buys nothing" |
| 4 | Trade rules | "I will pay X of this for Y of that" |

OpenOptima also reports `marginal_rates`: what each step along the front
actually costs. This is the evidence behind the earlier claim: "the first
16 g buys a lot, the next 148 g buys almost nothing".

## Concurrency

OpenOptima parallelises by **process**, not by thread. Gmsh holds global C
state, and CalculiX runs as a subprocess; two threads would either
serialise or corrupt each other. The parent process assigns each run
directory an id in advance, and gives each worker process a
self-contained payload.

## Reproducibility

Every evaluation writes a run directory. That directory holds the
geometry, the mesh, the solver deck, the raw solver output, and a
manifest. The manifest records the tool versions, the setup digest, and
the design. The cache key covers three things together: the design
vector, the setup digest, and the tool versions. Reusing a number computed
under different physics is not a cache hit. It is a wrong answer,
delivered quickly.

## Extending it

To add a physics backend, implement one protocol
(`solvers/base.py::StructuralSolver`), and register it in
`solvers/__init__.py::create_solver`. Nothing in `doe/`, `optimisation/`,
`storage/`, or `cli/` needs to change. That boundary is deliberate. It is
how the planned OpenFOAM cold-plate module will arrive without a rewrite.
