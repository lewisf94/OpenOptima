# 6. Optimise a stress percentile, not the raw peak

**Status:** accepted

## Context

The natural objective is peak von Mises stress. At a re-entrant corner, a point
support or a fully fixed face, the true elastic stress is unbounded: the
computed peak grows with every mesh refinement and never converges.

## Decision

The default measure is the 99th percentile of the nodal field, with
user-nominated singular regions excluded. `pnorm`, `region_max` and `raw_max`
are available. The raw peak is **always** computed and reported as
`stress_raw_max_mpa` whichever measure drives the objective.

## Rationale

An optimiser handed a mesh-dependent number optimises the mesh. Worse, the
result is self-refuting: a mesh-convergence study of the winning design
contradicts the optimisation that produced it.

Excluding the singular region is honest — the singularity is a modelling
artefact of an idealised encastre, not a real stress — provided the exclusion is
visible. Hence reporting the raw peak always, naming the measure on every
result, and stating the excluded regions in every report.

## Consequences

The reported factor of safety is less conservative than one based on the true
peak, which `engineering-assumptions.md` states plainly. A genuine stress
concentration (a real fillet, not a modelling singularity) must be modelled and
refined properly rather than hidden by the percentile — the documentation says
this too, because the distinction is the user's to make and they need to know it
exists.
