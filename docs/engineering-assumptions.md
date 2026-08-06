# Engineering assumptions

Everything OpenOptima assumes on your behalf, stated plainly. Read this before
sizing anything real from its output.

## What the analysis is

**Linear static, isotropic linear-elastic, small displacement.** Nothing else.

Specifically **not** modelled:

- buckling (a thin optimised web can be stable in this analysis and buckle in
  reality — this is the most likely way a minimum-mass result is unsafe);
- fatigue and any cyclic loading;
- plasticity, creep, or any nonlinear material behaviour;
- contact, friction, preload, bolt or joint behaviour;
- large displacement or stress stiffening;
- dynamics, vibration, resonance, impact;
- residual stress, weld effects, or process history;
- temperature dependence and thermal stress;
- fracture and crack growth.

An optimiser will exploit every one of these omissions. Minimising mass under
stress and displacement constraints alone will drive a design towards thin
slender sections — precisely the geometry most at risk from the phenomena in
that list.

## Units

Internally everything is `mm, N, MPa, t` — the standard consistent structural
millimetre system. Solvers are unitless and will happily multiply whatever
numbers they are given, so conversion happens once, in `domain/units.py`, with
tests. User input is in engineering units (kg/m³, MPa, mm).

Density in the internal system is tonnes per cubic millimetre: aluminium at
2700 kg/m³ is `2.7e-9`.

## Allowable stress

`allowable_stress_mpa` is a **design decision, not a material property**, and
OpenOptima will not infer it. It depends on yield or ultimate strength, which
of those governs, load uncertainty, material condition and direction,
manufacturing process, temperature, inspection regime, and the design code in
force.

State the basis in `allowable_stress_basis`. It appears in every report so a
reviewer can check it.

Factor of safety is simply `allowable_stress / stress_measure`, where the stress
measure is the one you configured — see below.

## Stress measurement

**This is the assumption most likely to surprise you.**

Raw peak von Mises stress is a poor optimisation target. At a re-entrant corner,
a point support or a fully fixed face, the true elastic stress is *unbounded*:
the computed peak simply grows with every mesh refinement and never converges.
An optimiser handed that number learns to chase mesh artefacts, and a
mesh-convergence study of the winning design then contradicts the optimisation
that produced it.

So the default measure is the **99th percentile of the nodal field with
user-nominated singular regions excluded**. Options:

| Measure | Use when |
|---|---|
| `percentile` (default) | general use; robust to singularities |
| `pnorm` | you want a smooth differentiable aggregate |
| `region_max` | the peak matters and you have excluded the singular regions |
| `raw_max` | you have no singularities, or you are deliberately inspecting them |

The raw peak is **always** computed and reported as `stress_raw_max_mpa`,
whichever measure drives the objective. Nothing is hidden; the choice is about
what the optimiser chases.

Consequence: the reported factor of safety is based on the percentile, so it is
*less conservative* than one based on the true peak. If your part has a genuine
stress concentration that matters (not a modelling singularity), model the
fillet properly and refine it, rather than relying on the percentile to hide it.

## Load application

- **Forces** are applied as a consistent nodal load vector obtained by
  integrating the element shape functions over the loaded surface. On quadratic
  faces this correctly places zero load at corner nodes and all of it at the
  midsides. It is a *distributed* traction over the region, not a point load —
  no artificial local stress concentration is introduced.
- **Pressure** becomes a `*DLOAD` element face pressure, so CalculiX integrates
  it in the element's own curved geometry with the correct direction.
- **Fixed supports** restrain all three translations over the whole region. This
  is stiffer than reality: a bolted joint is not a rigid encastre. Expect the
  model to be stiffer than the part and the stress at the constraint boundary to
  be singular — which is why the mounting face is usually in
  `stress_evaluation.excluded_regions`.

## Equilibrium checking

Every load case compares the applied load against the solver's reaction total.
A mismatch above 1% raises a warning. This is a free global check that catches
load-on-the-wrong-face, missing-constraint and unit mistakes which would
otherwise pass silently. If you see this warning, do not proceed.

## Mesh

- Second-order tetrahedra (C3D10) by default. First-order tets are far too stiff
  for stress work; the mesher asserts it produced the order you asked for.
- Quality gates: no inverted elements, minimum scaled Jacobian above the
  configured floor, and mesh volume within tolerance of CAD volume.
- **A single mesh setting is used for the whole study.** Results are comparable
  to each other but are not converged. Re-run the chosen design at finer meshes
  and confirm the numbers have settled before trusting a value. Displacement and
  strain energy converge quickly; peak stress at a concentration converges
  slowly, and at a singularity never.

## Multiple load cases

Enveloped, never averaged. The reported constraint metric is the worst case, and
per-case values are reported alongside as `metric.load_case_id`. Averaging a
failing case against a passing one would hide the failure.

## What the optimiser will and will not do

It searches the parametric space you defined. It cannot invent a rib, change the
topology, or notice that a different concept would be better. A "minimum mass"
result is minimum mass *within your parameterisation*, subject to *your*
constraints, under *this* analysis.

## Before trusting a result

1. `openoptima doctor` passes, with every region resolving uniquely across the
   whole design range.
2. No equilibrium warnings.
3. The mesh convergence of the chosen design has been checked.
4. The allowable stress and its basis are ones you would defend.
5. The load cases genuinely bound the service loading.
6. Buckling, fatigue and any other omitted phenomenon have been assessed
   separately.
7. Someone qualified has looked at the deformed shape and stress field, not just
   the numbers.

The software computes and presents. Judging is the engineer's job.
