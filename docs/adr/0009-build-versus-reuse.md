# 9. What to reuse from other projects, and what to build here

**Status:** accepted

## Context

OpenOptima is roughly 11 000 lines of Python. A fair question, asked before
committing to the next tranche of work, is how much of that already exists as a
maintained library, and how much of the *planned* work — topology optimisation,
fatigue, print rules — is about to be written a second time.

The question matters most for topology optimisation. Writing a density-based
optimiser from scratch is months of work, and there is an established
CalculiX-based one already.

A survey was done. Findings below.

## What already exists and should be used

| Need | Library | Licence | Notes |
|---|---|---|---|
| Topology optimisation on CalculiX | [`calculix/beso`](https://github.com/calculix/beso) | LGPL-3.0 | Takes a prepared linear static `.inp` file. Objectives: stiffness, failure index, buckling, heat. Has sensitivity filters including a **casting-direction filter** — the same maths a print-overhang rule needs. |
| Fatigue | [`pylife`](https://github.com/boschresearch/pylife) | Apache-2.0 | Wöhler (S-N) curves, damage summation, FKM guideline, works on FE mesh results. |
| Overhang and printability geometry | [`trimesh`](https://github.com/mikedh/trimesh) | MIT | Face-normal against build direction, watertightness, connectivity. |
| Modal analysis | CalculiX `*FREQUENCY` | — | Native. Nothing to reuse; it is deck writing and parsing. |

Apache-2.0 and MIT are both usable inside a GPL-3.0 project. LGPL-3.0 is as
well.

`beso` takes an `.inp` file as its input, which is exactly what
`solvers/calculix/deck.py` already writes. That makes it a natural fit behind an
adapter rather than a rewrite.

## What does not exist

- **Resolving a named region across a changing shape.** This is the topological
  naming problem. The best open-source attempt is FreeCAD's `ElementMap`, which
  its own documentation calls a mitigation rather than a solution, and which is
  tied to FreeCAD's document model. `regions/signature.py` is not a reinvention.
- **Separating an infeasible design from a broken run** for an optimiser
  (ADR 2). No library models this distinction because no library owns the loop.
- **The CalculiX buckling load-scaling fix.** CalculiX silently returns the
  second mode when the true factor falls below about 0.52. Nothing found in the
  ecosystem guards against it. Note that `beso` offers buckling as an
  optimisation objective; whether it is exposed to the same defect is unknown and
  must be checked before that objective is trusted here.
- **Failure criteria for an orthotropic solid.** The Python composite libraries
  found (`composipy`, `lamipy`, `compositeslib`) all implement classical laminate
  theory for thin plates. They do not apply to a 3D stress state in a solid
  element. Hoffman is about forty lines of algebra from a textbook — small enough
  that a dependency would cost more than it saves.
- **Grid convergence arithmetic.** Richardson extrapolation and the Grid
  Convergence Index are published, standard, and about 150 lines. No dependency
  is worth adding for them.

## What was built here that a library could have done

Honest accounting: [`pyccx`](https://github.com/drlukeparry/pyccx) (BSD-2)
writes CalculiX decks, meshes through gmsh, and parses `.frd` and `.dat`. It
overlaps `solvers/calculix/` and part of `meshing/`. It slices results by fixed
column width, so it does not have the defect in trap 4.

It was not used, and switching now is not proposed. It does not cover buckling,
consistent nodal loads, orthotropic materials, the equilibrium check, or the
element-order assertion — all of which exist here and are verified against
published references. Replacing verified code with unverified code, and then
re-adding the missing half, costs more than it saves.

That is a result about this particular library at this particular time, not a
principle. It would have been a reasonable starting point on day one.

## Decision

**OpenOptima is an orchestration, validation and design-space layer around
existing engineering software. It is not a place to implement engineering
mathematics.**

It owns: CAD parameterisation, design-space definition, region tracking,
optimisation strategy, analysis orchestration, failure classification,
constraint handling, engineering validation, result provenance, conversion
between tools, and how a result is presented.

Existing tools own: solving, topology algorithms, fatigue mathematics, mesh
and geometry mathematics, and any established published calculation with a
maintained implementation.

Before building any non-trivial engineering capability, audit it in
`docs/capability-audit.md` and record one of four verdicts:

1. **Use** — run the existing tool as a separate program (topology
   optimisation).
2. **Wrap** — depend on the library and adapt its input and output (fatigue).
3. **Build on** — use it as a component for one calculation (mesh geometry).
4. **Build** — write it here, because nothing suitable exists, or because
   adopting something would mean giving up a verification already held.

Two rules qualify all four:

- **Read the licence in the package metadata, not the README badge.** A
  surrogate-optimisation library was found advertising Apache-2.0 in its
  README while declaring PolyForm Noncommercial in `setup.py`. Depending on it
  would have made this project non-commercial, which the roadmap rules out by
  name.
- **Verification never transfers.** See below.

**Verification does not transfer.** A number from a library gets the same
benchmark test as a number written here. Reuse saves writing the code; it does
not save proving the answer. An external tool is a calculation provider, never
an authority: "beso reports this topology as optimal" is not the same claim as
"OpenOptima verified this design", and the second one is the only one this
software may make.

## Consequences

- No SIMP or BESO solver will be written in this repository. Topology
  optimisation becomes an adapter around an existing one.
- No S-N curve or damage-summation code will be written. Fatigue becomes a
  binding to `pylife`.
- No overhang geometry maths will be written. Print rules use `trimesh`.
- Each of those is an optional dependency, in the pattern already used for
  `pymoo` and `cadquery`. A user who does not run topology optimisation does not
  install its solver.
- Every one of them still needs a verification benchmark here before any number
  it produces is reported.
