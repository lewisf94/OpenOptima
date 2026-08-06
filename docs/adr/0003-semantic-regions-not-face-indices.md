# 3. Regions are resolved from geometry, never stored as face indices

**Status:** accepted

## Context

CAD kernels renumber faces when a model is rebuilt. `Face12` before a fillet
change is generally not `Face12` after it. This is the topological naming
problem, and it is unsolved in the general case.

A parametric optimisation rebuilds the model thousands of times. If a load is
attached to a face *index*, it will eventually move to the wrong face. The
analysis still converges. The result is still plausible. It is wrong, and
nothing announces it.

## Decision

A region is a **selector**: required properties (surface type, normal, position,
area, radius) plus a scoring rule. It is re-resolved against the real geometry
on every single evaluation.

If two candidates score within `ambiguity_margin`, the evaluation **stops** with
`REGION_AMBIGUOUS`. It does not pick the better one.

The mesher re-resolves selectors independently after reloading the BREP and
compares measured areas with what the geometry stage found, so a tag shuffle
across the file boundary surfaces as an error.

`openoptima doctor` builds the extremes of the design space and checks every
selector still resolves uniquely there.

## Alternatives rejected

- **Store face indices** — the failure mode above.
- **Rely on FreeCAD 1.0's topological naming mitigation** — helps, but ties the
  project to one CAD application's release cycle, and inherits a partial
  solution to a problem we can sidestep entirely.
- **Guess when ambiguous** — the whole point is that guessing here is
  indistinguishable from working, right up until someone builds the part.

## Consequences

Selectors take more thought to write than clicking a face. That is the cost, and
`doctor` plus the extremes-testing in the integration suite are what make it
manageable. In exchange, a design range can be swept without a silent
misattachment.
