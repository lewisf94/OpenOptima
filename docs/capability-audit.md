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

**Verdict: do not write a topology optimiser.** Run beso as a separate
program. ADR 10 records why it must be a separate program rather than an
imported library: importing it would let its own solver loop reach around the
evaluation cache, the failure classification, the region resolution and the
buckling fix — which is most of what makes a number here trustworthy.

**Two things must be verified before any beso number is reported.**

1. **Its buckling objective.** CalculiX silently returns the second buckling
   mode instead of the first when the true factor falls below about 0.52.
   OpenOptima fixes this by scaling the reference load. beso uses the same
   solver and offers buckling as an objective. Whether it hits the same defect
   is **unknown and must be measured**, not assumed. The test: one column with
   a known Euler answer, solved three ways — by hand, through OpenOptima, and
   through beso — and compared.
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

### Modal analysis — Build (but nothing to reuse)

The frequencies a part naturally vibrates at.

CalculiX does this natively with a `*FREQUENCY` step. There is no library to
reuse and none is needed. The work is writing the step, parsing the
frequencies, verifying against a published beam answer, and exposing the result
as a constraint. Contained, and entirely ours.

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

### Telling a bad design from a broken run — Build

No library models this, because no library owns the optimisation loop. A design
that fails is information the optimiser can use. A solver that crashes is not,
and feeding it back as a poor result teaches the optimiser something false.

### Mesh convergence arithmetic — Build ✔ done

Richardson extrapolation and the Grid Convergence Index are published, standard
and about 150 lines. No dependency is worth adding.

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
