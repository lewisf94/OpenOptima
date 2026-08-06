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
