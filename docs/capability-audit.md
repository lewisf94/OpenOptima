# Capability audit

**Purpose.** Before OpenOptima builds any more engineering capability, this
document records what already exists to do the job, and whether we should use
it or write our own.

This exists because the alternative is expensive. Writing a topology optimiser
from scratch is months of work, and a maintained one already runs on the same
solver OpenOptima already uses. Finding that out afterwards is the worst
possible time.

**Read this before starting any roadmap item.** If the item is not in the table
below, audit it first and add it. The questions to answer are listed at the end.

---

## 1. What OpenOptima owns, and what it does not

This is the boundary the audit is measured against.

**OpenOptima owns the parts that connect everything together:**

- turning a CAD model into a set of dimensions that can be varied;
- keeping a named face attached to the right surface when the shape changes;
- deciding what the optimiser is allowed to change;
- running the analysis, and knowing when a run failed rather than a design;
- constraints, objectives, and the search strategy;
- checking every number before it is reported, and recording where it came
  from;
- converting data between the tools below;
- presenting a result an engineer can act on.

**Other software owns the specialist calculations:**

| Job | Tool |
|---|---|
| Solving the physics | CalculiX |
| Meshing | Gmsh |
| Search algorithms | pymoo |
| Topology optimisation | beso |
| Fatigue | pyLife |
| Mesh and surface geometry | trimesh |

The short version: **OpenOptima is the control system, not the calculator.**

One rule cuts across all of it. **Reuse saves writing the code. It never saves
proving the answer.** A number from somebody else's library faces exactly the
same verification benchmark as one written here. A library does not inherit
OpenOptima's verification status by being popular, or by being maintained, or
by being the obvious choice.

---

## 2. The audit

Verdict key: **Use** = call it as a separate program. **Wrap** = depend on it
and adapt its input and output. **Build** = write it here. **Blocked** = cannot
be used, reason given.

### Topology optimisation — Use

Deciding where material should exist at all, rather than adjusting dimensions
of a shape you already drew.

| | |
|---|---|
| Candidate | [`beso`](https://github.com/calculix/beso) |
| Licence | LGPL-3.0 — usable |
| Method | Bi-directional evolutionary structural optimisation |
| Input | A prepared CalculiX analysis file — exactly what our deck writer makes |
| Objectives | Stiffness, failure index, buckling, heat flow |
| Also has | Multiple load cases, orientation for directional material, and a casting-direction filter, which is the same maths a print-overhang rule needs |
| Needs FreeCAD? | No. That is only its interface |

**Closing the loop back to real numbers — done.** ADR 10 requires that a
topology result is never reported until it has been re-analysed on a
body-fitted mesh. It now is, through `openoptima topology --analyse`.

| Step | State |
|---|---|
| Smoothed surface meshes into solid elements | **works** — gmsh produced 3972 quadratic tetrahedra from the real result |
| The mounting and loaded faces survive smoothing | **works** — but only since flat faces were held; before that gmsh found none |
| The shape becomes an ordinary `GeometryArtifact` | **no, and it did not need to** — see below |
| Region resolution | **reused unchanged** — `resolve_regions` takes plain `FaceSignature` objects and knows nothing about gmsh |
| Deck, solver, metrics | **reused unchanged** |

The third row was the gap, and going through a BREP turned out to be the wrong
way to close it. gmsh rebuilds an imported STL as its own discrete surfaces,
not OpenCASCADE ones, so there is no BREP to export — that route is closed, not
merely awkward. What the pipeline actually needs from a shape is a set of
`FaceSignature` objects and a meshable volume, and both can be had from the
triangles directly. `regions/discrete.py` measures them; `meshing/sources.py`
builds the volume. Nothing downstream changed.

**This was a build, and the audit rule says to say why.** Nothing existing does
it. Mesh libraries segment a triangle mesh into patches — gmsh does it itself,
via `classifySurfaces` — but a patch is not a face. gmsh handed back 57 patches
for a part with 46 faces, splitting the top face into five pieces, because it
cuts a surface until each piece is simple enough to describe. Turning patches
back into the faces a selector was written against is the part nobody else does,
because nobody else is trying to keep a *selector* valid across a rebuild. That
is region resolution, which this audit already lists as ours.

Verified as V13 in [`verification-plan.md`](verification-plan.md): the same bar
through both routes agrees to under 0.01% on deflection and stored energy, and
a 3.000 mm hole comes back as 3.0000 mm. One limit is real and stated rather
than tolerated — a rounded blend cannot be found this way, because it meets the
faces it joins smoothly and leaves no crease to find it by.

**Verdict: do not write a topology optimiser.** Run beso as a separate
program. ADR 10 records why it must be a separate program rather than an
imported library: importing it would let its own solver loop reach around the
evaluation cache, the failure classification, the region resolution and the
buckling fix — which is most of what makes a number here trustworthy.

**Two things must be verified before any beso number is reported.**

1. **Its buckling objective — measured, and the answer is no.** CalculiX
   silently returns the second buckling mode instead of the first when the true
   factor falls below about 0.52. OpenOptima fixes this by scaling the
   reference load; beso applies no such scaling.

   Measured on one column, one mesh, with only the applied load changed. A
   column whose real critical load is 14 409 N was reported as surviving
   127 569 N — **8.86 times too high, in the unsafe direction** — at every load
   where the true factor fell below the threshold. With OpenOptima's scaling
   the same model gives 14 409 N every time, within 0.11% of Euler.

   So beso's buckling objective **is** affected. It is refused in
   `topology/config.py` with the reason, rather than passed on. See V11 in
   [`verification-plan.md`](verification-plan.md) for the full table.
2. **The shape that comes out.** beso produces a density map, not a solid.
   Converting that into something manufacturable and re-analysable is most of
   the work, and it is ours.

**Known defect in beso, and it affects us.** On Windows it calls the solver
with `shell=True` and an argument list. That combination joins the arguments
into one string, so any path containing a space breaks it — and the default
Windows location is `C:\Users\First Last\Documents`. It also imports
matplotlib at module level, which the Windows build deliberately excludes.

**Decided:** run beso's own working directory somewhere with no spaces in its
path — regardless of where the project itself lives — and open the fix
upstream in parallel. A local patched fork stays available if upstream does
not take the fix, but is not the starting position. See
[ADR 10](adr/0010-topology-optimisation-via-beso.md).

### 2D analysis and 2D topology optimisation — Build (and the limit is ours)

Analysing a part as a flat shape rather than a solid body.

**beso already does both.** Its element handling covers shells (`S3`, `S6`,
`S4`, `S8R` and more), plane stress (`CPS*`), plane strain (`CPE*`),
axisymmetric (`CAX*`) and membranes, alongside every 3D solid type. So a 2D
topology run needs nothing added to beso.

**OpenOptima cannot currently produce a 2D model at all.** The mesher asks gmsh
only for three-dimensional elements and accepts only `C3D4` and `C3D10`
tetrahedra. Everything downstream — the deck writer, the region resolver, the
load application — assumes a solid body with faces.

So this is our work, not a reuse question. What it needs:

- a mesher path that produces surface elements;
- **a decision the user must make and OpenOptima must not guess**: whether the
  part is *plane stress* (a thin plate, free to thin down under load) or
  *plane strain* (a slice through something long, held by the material either
  side of it). The same shape and load give different answers, and picking
  wrongly is silent;
- regions resolved from edges rather than faces;
- verification benchmarks of its own. A 3D benchmark proves nothing about 2D.

**Why it is worth doing.** A 2D solve is far cheaper than a 3D one, so it
suits exploring a shape before committing to a full run. Many real parts are
genuinely two-dimensional as well: anything cut from plate, and any extruded
profile of constant section.

**Verdict: build, and only after 3D topology is integrated and verified.**
Doing 2D first would mean two unproven things at once.

### Fatigue — Wrap

Failing after many load cycles, at a stress the part would survive if applied
once.

| | |
|---|---|
| Candidate | [`pyLife`](https://github.com/boschresearch/pylife) 2.3.1 |
| Licence | Apache-2.0 — usable |
| From | Bosch Research |
| Provides | S-N curves, rainflow counting, damage summation, failure probability, stress gradients, FE mesh handling, FKM guideline |

**Verdict: write no fatigue mathematics.** The boundary is: CalculiX gives
stress, OpenOptima converts it into pyLife's input, pyLife returns a life, and
OpenOptima turns that into a constraint the optimiser can use — for example,
"must survive ten million cycles".

Two honesty requirements survive the reuse, because they are engineering
statements rather than arithmetic. Damage summation is commonly out by a factor
of three, so a life quoted to three significant figures implies a precision
that does not exist. And an as-printed surface is rougher than a machined one,
which lowers fatigue strength by more than layer weakness does — leaving it out
makes the answer optimistic, which is the dangerous direction.

### Printability geometry — Wrap

Whether a shape can actually be printed: overhang angle, wall thickness, build
volume.

| | |
|---|---|
| Candidate | [`trimesh`](https://github.com/mikedh/trimesh) 5.0.0 |
| Licence | MIT — usable |
| Provides | Surface normals, mesh queries, ray casting, watertightness, connectivity |

**Verdict: write no mesh geometry.** trimesh measures the angle of every
surface against the build direction. OpenOptima decides what that means — and
the decision is the part worth building, because printability must be a
trade-off the user sets, not a gate that silently deletes good designs.

### Modal analysis — Build ✔ done

The frequencies a part naturally vibrates at.

CalculiX does this natively with a `*FREQUENCY` step. There was no library to
reuse and none was needed. The work was writing the step, parsing the
frequencies, verifying against a published beam answer, and exposing the result
as a constraint.

**The verdict held, but the estimate of where the work sat did not.** Writing
the step and reading the numbers was an afternoon. The rest was the two ways
CalculiX makes it quietly wrong: it writes a mode shape into the results file
for every mode without being asked, which shifts every later result along, and
it reports a part nobody held as a set of zero-hertz modes with a successful
exit code. Neither is documented and neither announces itself. Traps 13 and 14
in `AGENTS.md`, and V14 in the verification plan.

### Directional failure criteria — Build ✔ done

Whether a material that is weaker in one direction than another has failed.

Checked: `composipy`, `lamipy`, `compositeslib`. All implement classical
laminate theory, which describes thin layered plates. **None applies to a
three-dimensional solid element**, which is what OpenOptima analyses. This is a
genuine gap, and the arithmetic is small enough that a dependency would cost
more than it saves.

Built in `domain/failure_criteria.py`: the Hoffman criterion and a maximum
stress criterion, verified against closed-form answers. The hard part was never
the equation — it was the stress rotation, the sign conventions, and finding
that Hoffman cannot describe a material whose weakest direction is under half
its strongest.

**Reachable from a project file since 2026-08-12**, under `material.printed`.
That gap is worth recording, because it is a failure mode this audit does not
otherwise catch: the material model, both criteria, the deck writer and
verification benchmark V10 were all built, tested and documented as a user
feature, and `MaterialSchema` still accepted only an ordinary material. The
capability existed and nobody could reach it. **An audit entry marked done
means the calculation is trusted, not that a user can run it** — those are
separate claims and this one was true of the first while being false of the
second for as long as the docs said otherwise.

Measured once it was reachable, on `examples/drone_arm`: changing only the
print direction moves the factor of safety from 3.07 to 1.55 while the
99th-percentile stress stays at 7.53 against 7.54 MPa.

One correction to the note above. Hoffman's limit binds on **tension times
compression** on each axis, not on strength alone — the through-layer product
must stay above a quarter of the in-plane product. Where both fall together
that is the familiar "under half"; but a print is weak only in *tension*
through its layers, so its compression term holds the product up and the
criterion tolerates far more than the rule of thumb suggests. Measured with
in-plane strengths at 22/30 MPa: accepted at 6.0 MPa through-layer tension,
refused at 5.0.

### Surrogate optimisation — Blocked

Using a fast approximate model to cut down how many real analyses are needed.

| | |
|---|---|
| Candidate | [`pysamoo`](https://github.com/anyoptimization/pysamoo) |
| Licence | **PolyForm Noncommercial 1.0.0** — not usable |

**This one is worth reading carefully, because the obvious check gives the
wrong answer.** The repository displays an Apache-2.0 badge. Its `setup.py`
declares `PolyForm Noncommercial License 1.0.0` and the classifier
`License :: Other/Proprietary License`. The badge is wrong.

PolyForm Noncommercial forbids commercial use. That is not an open-source
licence, and depending on it would make OpenOptima non-commercial too — which
the roadmap already rules out by name, because it would make this project
source-available rather than open source.

**Verdict: pysamoo cannot be used.** If surrogate optimisation is wanted,
audit alternatives separately. Do not resolve this by reading a badge.

**The general lesson:** check the licence in the package metadata, not the
README.

### Multi-objective search — Wrap ✔ done

Already correct. pymoo provides NSGA-II and the rest; OpenOptima defines the
engineering problem and interprets the result. No change needed.

### GPU-accelerated solving — Wrap, pending measurement

Whether CalculiX can use a graphics card to solve faster, and whether that
would actually speed up an OpenOptima run rather than only a single solve.

**Correcting a claim made in chat before this was checked.** CalculiX was
described as not using a GPU "at all", full stop. That was wrong, stated with
more confidence than a training-data memory earns, and the opposite of this
project's own rule to measure before asserting. Corrected here, in public,
rather than left standing.

**What is actually true, checked against the CalculiX project's own site and
its user forum.** Since version 2.17, CalculiX can be built against the
PaStiX solver instead of its default (SPOOLES). PaStiX alone is reported to
give up to 4x on the equation-solving step; adding an Nvidia card with CUDA
installed is reported to reach up to 8x. That step is not a small part of a
run either — CalculiX's own profiling puts 59% or more of total run time
there for a typical model.

**Why this is `Wrap, pending measurement` and not `Use`.** Three real
problems, found on the CalculiX forum rather than assumed:

1. **It is not in the standard download.** The build this project fetches
   (`app/solver_setup.py`) is the ordinary prebuilt binary. Getting PaStiX
   and CUDA means compiling CalculiX from source against `hwloc`, `PaRSEC`,
   `scotch` and a patched PaStiX build — a real dependency chain, and one
   that would have to be repeated for every platform this project ships to.
2. **Current reports are mixed, not uniformly positive.** Forum users
   report the "build without CUDA" option not fully working, and at least
   one report of 0% GPU utilisation despite correct setup, with performance
   *worse* than CPU-only MKL Pardiso in that case. This is not a solved,
   drop-in path today.
3. **The published speedup measures the wrong thing for how this project
   runs.** OpenOptima's parallelism is by process — many designs solved at
   once, one per CPU core (`AGENTS.md`: "Parallelism is by process, never by
   thread"). A GPU is one device. Whether ten CalculiX processes sharing one
   GPU for their equation solve beats ten CPU cores each solving
   independently is not answered by a benchmark of one solve going faster —
   it depends on how well a shared GPU serves several simultaneous callers,
   and on whether the smaller meshes typical of a single-part optimisation
   (tens of thousands of degrees of freedom, not millions) are large enough
   for the transfer overhead to pay for itself. Nobody has measured this for
   OpenOptima's actual workload.

There is a fourth question that is not a performance question at all. A
different linear solver does its arithmetic in a different order, and this
project has already found that arithmetic order can change more than the
last decimal place — see trap 11 in `AGENTS.md`, where thread-order
differences changed which elements a topology run kept. Before a GPU-solved
number is trusted for anything, it needs to pass the same verification suite
every other number does, not merely agree "closely".

**Verdict: worth a proper audit, not yet a build.** The audit is: compile
the PaStiX/CUDA path once, run it against the existing verification decks,
and measure two things — does the answer still pass every V-numbered
benchmark, and does a full multi-design study finish faster in wall-clock
time, not just one solve. Only a positive measurement on the second question
justifies shipping this to users, because a faster single solve that cannot
be parallelised across a study is not a faster study. See the roadmap for
who does which half of this.

### CalculiX deck writing and result parsing — Build ✔ keep ours

| Candidate | Notes |
|---|---|
| [`pygccx`](https://github.com/calculix/pygccx) | Under the official CalculiX organisation. 3D solid elements, Gmsh meshing, one class per CalculiX keyword. The closest match to our deck layer |
| [`pyccx`](https://github.com/drlukeparry/pyccx) | BSD-2. Meshing, materials, load cases, result extraction |

**Verdict: keep ours, and this is a result rather than a principle.** Either
would have been a reasonable starting point on day one. Neither covers what has
since been built on top and verified: the buckling load-scaling fix, consistent
nodal loads, orthotropic materials, the equilibrium check, and the element
order assertion. Replacing verified code with unverified code and then
re-adding the missing half costs more than it saves.

Revisit if that stops being true.

### Region resolution across a changing shape — Build

Keeping "the load goes on this face" pointing at the right face after the
geometry is rebuilt.

This is the topological naming problem. The best open-source attempt is
FreeCAD's `ElementMap`, which FreeCAD's own documentation calls a mitigation
rather than a solution, and which is tied to FreeCAD's document model.

**Nothing to reuse.** This is the core problem OpenOptima exists to solve.

### Adding a fillet or chamfer to an imported shape — Use ✔ done

Cutting a rounded or flat corner into a solid, and doing it on an edge that
is found rather than remembered.

| Part of the job | Verdict |
|---|---|
| The solid modelling itself | **Use.** `gmsh.model.occ.fillet` and `.chamfer` are OpenCASCADE, the same kernel that already builds and imports every shape in the project. Writing a surface-blending algorithm would be absurd |
| Deciding *which* edge | **Build.** This is region resolution again, and it is the thing OpenOptima exists to own |

**Verdict: use OpenCASCADE for the geometry, own the naming.** No new
dependency: this reaches the exact kernel already in use through the mesher
that is already a hard requirement, so there was no licence to read and no
version to pin.

The split matters more than it looks. Every CAD kernel offers "fillet these
edge numbers", and every edge number is worthless the moment the shape is
rebuilt — measured here, adding one fillet renumbered every face of the
part. So the reusable half is the blend surface, and the half that had to be
written is a single call to `resolve_region` on each of the two faces the
edge lies between. That is about thirty lines, and it is the only part of
the operation that could have produced a plausible, wrong answer.

**Verification was not inherited.** Following [ADR
9](adr/0009-build-versus-reuse.md), OpenCASCADE's answer faces a check of its
own: a fillet of radius *r* on a straight 90-degree corner of length *L* must
remove `r²(1 − π/4)L` and a chamfer of size *s* must remove `s²L/2`. Both
match to every digit measured — a 10 mm fillet removed 1287.61 mm³ against
1287.6129 predicted, a 12 mm chamfer exactly 4320.00 mm³. That check also
catches a feature landing on the wrong edge, which is the failure that
actually worries us.

**Not done: holes, pockets, wall offsets.** A hole needs a position on a
face, which needs a coordinate convention and its own ambiguity rules. It is
a separate piece of work, not a missing corner of this one.

### Telling a bad design from a broken run — Build

No library models this, because no library owns the optimisation loop. A design
that fails is information the optimiser can use. A solver that crashes is not,
and feeding it back as a poor result teaches the optimiser something false.

### Mesh convergence arithmetic — Build ✔ done

Richardson extrapolation and the Grid Convergence Index are published, standard
and about 150 lines. No dependency is worth adding.

### 3D rendering and picking in the browser — Use ✔ done

Drawing triangles in WebGL, letting the mouse orbit around them, and working
out which triangle a click landed on.

| Candidate | Notes |
|---|---|
| [`three.js`](https://threejs.org) 0.160.0 | MIT licence — usable and compatible with GPL-3.0. The de facto standard for exactly this |
| Write it by hand | Raw WebGL: shaders, matrix math, a camera, ray-triangle intersection |

**Verdict: use three.js, do not write a 3D engine.** This is presentation, not
engineering mathematics, but the same "reuse saves writing the code" logic
from [ADR 9](adr/0009-build-versus-reuse.md) applies to any solved problem —
correct ray-triangle picking is exactly the kind of thing worth getting from a
library with millions of users rather than from a first attempt.

**How it was fetched, since this app has no build step.** Every other script
in `openoptima-app` is a plain file with no bundler, and three.js is used the
same way: `npm view three@0.160.0` in a scratch directory, then
`three.module.min.js` and the official `OrbitControls.js` copied unmodified
into `app/static/vendor/` and loaded via a browser-native import map — no
`node_modules`, no npm dependency in `pyproject.toml`, nothing for
PyInstaller to bundle beyond two more static files it already knows how to
ship. See `static/vendor/README.md`.

**What stayed ours.** Turning a solid into the triangles to draw
(`geometry/tessellate.py`) and turning a click into a durable description
(`regions/describe.py`) are both this project's own problem — nobody else
knows what a *face* means here, or that a tag is not allowed to be trusted
between builds. three.js draws the picture; it has no idea what a bolt hole
is.

---

## 3. Before starting any new capability

Search first. Check GitHub, PyPI, the CalculiX and Gmsh ecosystems, FreeCAD,
and the published literature.

Then answer these, in writing, in this document:

1. What does it actually need to do?
2. What existing projects do it?
3. What method do they use?
4. What licence — **from the package metadata, not the README badge**?
5. Are they maintained?
6. Can they be called from a program, or only from an interface?
7. What input do they need, and what output do they give?
8. How accurate are they, and against what?
9. **How would we verify them here?**
10. Use, wrap, build, or blocked?

Only then write code.

Question 9 is not optional and never transfers. Whatever the answer to the
others, the number that reaches an engineer must be checked against a published
reference by a test in this repository.
