# 10. Topology optimisation runs an existing optimiser, in its own process

**Status:** accepted.

## Context

Topology optimisation answers a different question from the parametric
workflow. Parametric asks "I have a shape, what are the best dimensions?"
Topology asks "I have a problem, what shape should it be?" Both are legitimate
and neither subsumes the other, so this is an addition, not a replacement.

[ADR 9](0009-build-versus-reuse.md) says not to write established engineering
mathematics when a maintained implementation exists.
[`beso`](https://github.com/calculix/beso) is that implementation: bidirectional
evolutionary structural optimisation, built around CalculiX, LGPL-3.0.

Before designing anything, its actual interface was checked rather than
assumed. Four things matter, and two of them change the design.

**1. It does not need FreeCAD.** The repository lists FreeCAD as a
prerequisite, which would have been disqualifying — OpenOptima deliberately
uses gmsh's OpenCASCADE kernel and has no FreeCAD dependency. But FreeCAD is
only needed for its optional GUI. `beso_main.py` imports numpy, matplotlib and
its own modules, and nothing else of consequence.

**2. It takes a CalculiX `.inp` deck as its input.** That is exactly what
`solvers/calculix/deck.py` already writes. The roadmap's claim holds: this is an
adapter around an existing tool, not a new solver.

**3. It drives CalculiX itself, in its own loop.** It is not a function that
takes a mesh and returns a density field. It writes a deck, calls the solver,
reads the results, updates the densities and repeats, for as many iterations as
it takes. This is the important one, and it is dealt with below.

**4. It calls the solver with `shell=True` on Windows.**

```python
else:
    exit_status = subprocess.call([...], cwd=path, shell=True)
```

That is the one thing `AGENTS.md` forbids outright, and it is not a style
preference. On Windows, `shell=True` with an argument list joins the arguments
into a single string, so **any path containing a space breaks it** — and the
default install path for a Windows user is `C:\Users\First Last\Documents\...`.
It is a real defect in `beso`, not a theoretical one.

It also imports `matplotlib.pyplot` at module scope, and the PyInstaller spec
explicitly excludes matplotlib because it is large and drags in a GUI toolkit.

## Decision

**Run `beso` as a separate process, not as an imported library.**

The alternative — importing it and letting it call into our code — was
rejected. It owns a solver loop and OpenOptima owns a solver loop, and merging
the two would mean `beso` reaching around the evaluation cache, the failure
classification, the region resolution and the buckling load-scaling fix. Those
are not incidental; they are most of what makes a number from this project
trustworthy. Its `shell=True` and its matplotlib import would also become ours,
inside the frozen application.

As a separate process it is a tool we hand a deck to and collect output from,
which is the same relationship we already have with CalculiX itself. Its
internals stay its own problem.

**A density field is never a reported result.** Whatever `beso` produces goes
back through the ordinary evaluation pipeline, on a body-fitted mesh, before
any number from it is shown to anyone. A density field is a fuzzy map of how
much material belongs at each point. It is an idea, not a part. This is the
same rule as everywhere else in the project: the thing that gets reported is
the thing that was actually analysed.

**The conversion is ours to build.** Turning a density field into a watertight
solid with a minimum feature size the process can actually make is where most
open-source topology tools stop being useful, and it is the part that decides
whether any of this produces something manufacturable. That is the piece worth
building here; the optimisation itself is the piece worth borrowing.

## Consequences

`beso` is **not on PyPI**. It is a GitHub repository of scripts, so it cannot
simply be added to `pyproject.toml`. It has to be fetched, in the same shape as
the CalculiX download already in `app/solver_setup.py`: pinned to a commit,
checked against a hash, and never bundled into the installer without the
licence review that would require.

Running it out of process means paying to write files and read them back. That
cost is acceptable and buys the isolation described above.

Its buckling mode must not be used until it is checked against the defect this
project already found in CalculiX's buckling solve, where the eigenvalue solver
silently returns the second mode instead of the first, roughly nine times too
high in the unsafe direction. `beso` offers buckling as an objective, drives the
same solver, and nothing in the wider ecosystem appears to guard against it.
Assume it is affected until measured. See [ADR 8](0008-buckling-reliability.md).

**Measured, and it is affected.** A column whose real critical load is 14 409 N
was reported as surviving 127 569 N — 8.86 times too high, in the unsafe
direction. `topology/config.py` refuses the objective outright, with the reason.
See V11 in [`../verification-plan.md`](../verification-plan.md).

## How the "never a reported result" rule is kept

`openoptima topology --analyse` does it. The shape goes back through the
ordinary pipeline: re-meshed into solid elements, the same region selectors
re-resolved to put the same loads and supports back on, the same solver, the
same metrics, the same constraint checks.

Doing that needed one thing the project did not have. The pipeline took a CAD
model and asked OpenCASCADE what each face was; a topology result has no CAD, so
its faces are measured from the triangles instead (`regions/discrete.py`). The
obvious alternative — rebuilding the shape as a BREP so the existing path could
be reused — does not work: gmsh reconstructs an imported STL as its own discrete
surfaces, and there is no OpenCASCADE model to export. That route is closed, not
merely awkward.

The two routes are checked against each other rather than assumed to agree.
The same bar analysed both ways matches to under 0.01% on deflection and stored
energy (V13). And the rule earns its keep on the first real case: the optimised
cantilever kept 49.7% of the material and its factor of safety fell from 1.15 to
**0.63**. It fails. Nothing in the topology run says so, because `beso` was
asked for stiffness at a mass target and stress was never part of the question.

## Decision on the `shell=True` defect

The `shell=True` defect breaks `beso` for any user whose path contains a
space, which is most Windows users. Three ways out existed, and two of them
carry licence obligations, so this one was left for the project owner rather
than chosen by an agent:

1. **Run it in a space-free working directory.** Cheapest, no modification, but
   it is a workaround and it leaves the defect in place for everyone else.
2. **Patch it locally.** LGPL-3.0 permits modification, but distributing the
   modified version brings obligations, and we then carry a fork.
3. **Fix it upstream.** Best outcome for everyone and costs a pull request, but
   it is not within our control how quickly it lands, if at all.

**Decided: option 1 now, option 3 in parallel.** The adapter that eventually
calls `beso` must force *its* working directory to a path with no spaces,
independent of where the OpenOptima project itself lives on disk — the two are
allowed to differ. A pull request carrying the underlying fix should also be
opened upstream. Option 2 stays available if upstream does not take the fix,
but is not the starting position.

Nothing to build yet: no adapter calls `beso` as of this decision. This is
recorded ahead of that code so the choice is not reopened when it is written.
