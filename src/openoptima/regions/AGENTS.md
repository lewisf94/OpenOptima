# Semantic regions

This module exists because `Face12` before a fillet change is not `Face12`
after it. A load that silently migrates to the wrong face produces a converged,
plausible, wrong answer — the worst failure mode in the system.

## Rules

- Selectors are re-resolved against the real geometry on **every** evaluation.
  Never cache a face tag between designs.
- `SINGLE` mode with two candidates inside `ambiguity_margin` is an error, not a
  coin toss. The message must tell the user how to disambiguate.
- `REGION_NOT_FOUND` and `REGION_AMBIGUOUS` are classified as **errors**, not
  infeasible designs: they mean the project is set up wrong, and reporting them
  to the optimiser as a bad design would teach it something false.

## Normals

`gmsh.model.getNormal` already accounts for a face's orientation within the
solid, so its result is the outward normal. Do **not** additionally apply the
sign from `getBoundary(oriented=True)` — that flips every normal inward.

Rather than trusting either convention, `outward_normal_check` verifies the
whole set with the divergence theorem: for outward normals the surface integral
of `n . r` equals `3V`. If a future gmsh changes convention this catches it.

## Cylinders

Fit a circle in the plane perpendicular to the axis. Averaging the distance of
sampled points from their own centroid only works for a *full* cylinder; on a
fillet spanning 90 degrees the sampled centroid is nowhere near the axis and the
radius comes out badly wrong.

## Generating selectors (`describe.py`)

`matcher.py` takes a description and finds the face. `describe.py` goes the
other way, for click-to-pick in the GUI. Three rules, each of which came from a
measured defect rather than from reasoning:

- **A description written from one shape is a description nobody has tested.**
  Check every candidate against the part rebuilt at the extremes of its design
  range. The bracket's fillet radius is itself a design variable (3 to 25 mm),
  so a radius range written from its 8 mm default matched nothing at either end.
- **Never let a filter boundary sit inside numerical noise.** The two bolt holes
  measure 4.5 and 4.499999999999495 — a 5.05e-13 mm difference from the circle
  fit. Placing a radius boundary in that gap gives a filter that works by luck.
  `_MEANINGFUL_GAP` guards this.
- **Checking against more shapes cannot catch a deterministic defect.** The
  noise above is bit-identical at every design point, because it is the same
  kernel doing the same arithmetic. The range check passed it. Guard the cause,
  not just the symptom.

Reuse `passes_filters` from `matcher.py` rather than reimplementing the filter
logic. If the two ever disagree, a description generated from a click selects a
different set of faces from the one that resolves it.
