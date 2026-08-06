# Engineering assumptions

This document states plainly everything OpenOptima assumes on your behalf.
Read it before you size a real part from its output.

## What the analysis is

**The analysis is linear static, isotropic, linear-elastic, and
small-displacement.** It calculates nothing else. In plain terms:

- **Linear static** — the load is steady, not changing over time.
- **Isotropic** — the material is assumed equally strong in every
  direction.
- **Linear-elastic** — the material springs back fully, and double the
  load gives exactly double the result.
- **Small-displacement** — the part is assumed to move too little for its
  own deflection to change how the load acts on it.

OpenOptima specifically does **not** model:

- fatigue and any cyclic loading;
- plasticity, creep, or any nonlinear material behaviour;
- contact, friction, preload, bolt or joint behaviour;
- large displacement or stress stiffening;
- dynamics, vibration, resonance, impact;
- residual stress, weld effects, or process history;
- temperature dependence and thermal stress;
- fracture and crack growth.

**OpenOptima can analyse buckling** — see its own section, below. Buckling
is off by default, and has a limited range of validity. Every other item
in the list above stays invisible to OpenOptima, and an optimiser will
exploit every one of them, given the chance. Minimising mass, under only a
stress limit and a displacement limit, drives a design towards thin,
slender sections. Those sections are precisely the geometry most at risk
of the failures OpenOptima cannot see.

## Units

Internally, OpenOptima uses one consistent set of units: `mm, N, MPa, t`
(millimetres, newtons, megapascals, tonnes). This is the standard
consistent unit system for structural calculations. A solver has no
concept of units — it will multiply whatever numbers it is given, correct
or not. So OpenOptima converts your input into this internal system
exactly once, in `domain/units.py`, and tests that conversion. You enter
values in ordinary engineering units: kg/m³, MPa, mm.

In the internal system, density is in tonnes per cubic millimetre.
Aluminium, at 2700 kg/m³, becomes `2.7e-9` in that system.

## Allowable stress

**`allowable_stress_mpa` is a design decision. It is not a property of the
material.** OpenOptima will not infer this value for you. The right value
depends on:

- whether yield strength or ultimate strength governs, and which of the
  two applies
- how well you know the actual loads
- the material's condition, and its strength in the loading direction
- the manufacturing process
- temperature
- the inspection regime
- the design code that applies to your part

State your basis in `allowable_stress_basis`. OpenOptima prints it in
every report, so a reviewer can check your reasoning.

Factor of safety is `allowable_stress / stress_measure`. The stress
measure is the one you configured — see "Stress measurement", below.

## Stress measurement

**This is the assumption most likely to surprise you.**

Raw peak von Mises stress is a poor optimisation target. At a re-entrant
corner (an inward-pointing corner, such as the inside of an L-shape), at a
point support, or at a fully fixed face, the true elastic stress is
*unbounded*. The computed peak simply grows with every mesh refinement,
and never converges on a final value. An optimiser given that number
learns to pursue mesh artefacts — numbers produced by the mesh itself, not
by the real part. A mesh-convergence study of the winning design then
contradicts the very optimisation that produced it.

So, by default, OpenOptima measures the **99th percentile of the nodal
field** — the stress values calculated at every mesh point — with any
**singular** region excluded (a region where the maths predicts an
impossible, infinite stress; see the
[glossary](plain-english-guide.md#7-glossary) in the plain-English guide).
You can choose a different measure instead:

| Measure | Use when |
|---|---|
| `percentile` (default) | general use; not thrown off by singularities |
| `pnorm` | you want one smooth number, with no sharp jumps between similar designs |
| `region_max` | the true peak matters, and you have already excluded the singular regions |
| `raw_max` | your part has no singularities, or you are deliberately inspecting them |

OpenOptima always computes and reports the raw peak, as
`stress_raw_max_mpa`, whatever measure drives the objective. Nothing is
hidden from you. The choice only changes what the optimiser pursues.

**One consequence follows from this.** The reported factor of safety is
based on the percentile. It is therefore *less conservative* than a
factor of safety based on the true peak. If your part has a genuine
stress concentration that matters (a real feature, not a modelling
singularity), model that fillet accurately, and refine the mesh there. Do
not rely on the percentile to hide a real stress concentration.

## Buckling

OpenOptima can perform linear buckling analysis — a fast, approximate
calculation, technically called an *eigenvalue* calculation. It is **off
by default**. Turn it on for anything slender:

```yaml
buckling:
  enabled: true
  modes: 3
```

OpenOptima then reports a **buckling factor**: the multiple of the applied
load at which the structure becomes unstable. You can set a constraint on
it, like any other metric.

### Range of validity — read this before trusting a number

Solid tetrahedral elements (small, four-sided solid shapes) stop being
reliable for buckling once a structural member becomes slender, and **the
error runs in the optimistic direction**.

This was measured during development, against Euler's formula:

| Section | Slenderness | Result |
|---|---|---|
| 20 mm square, 200 / 400 / 800 mm long | 69–277 | within 1%, mode series correct at 1 : 9 |
| 22 mm square, 600 mm long | 195 | **9x too high** |
| 8 mm square, 400 / 500 mm long | 346 / 444 | **9x too high** |

In the failing cases, the returned mode series was 1 : 1.95 : 3.20. A real
column's mode series looks nothing like that. The eigenvalue solve had
missed the true lowest mode entirely. Refining the mesh only moved the
wrong answer around, without ever converging on the right one.

A buckling factor that reads too high tells you a strut is safe, when it
will actually fold. So OpenOptima **checks every buckling result against
beam theory, calculated from the same mesh, and refuses to report a
result it cannot trust**. Instead of a number with a footnote, you get an
explicit `result_unreliable` error — because an optimiser acts on the
number, and ignores the footnote.

The limit is set by `buckling.slenderness_limit`, with a default of 150.
Raising this limit does not make the analysis more accurate. It only
turns off the check. For genuinely slender members, use beam elements
instead, or a hand calculation using Euler's formula.

### Other buckling caveats

- **Linear buckling assumes a perfect part.** A real part has some
  initial bow (a slight existing curve) and some eccentric loading (a
  load that is not perfectly centred). Both effects reduce the real
  buckling load below the calculated one. For this reason, engineers
  conventionally set a buckling margin well above the stress margin. How
  far above is your judgement to make, not this software's.
- **A negative eigenvalue is not a failure.** It means the load would
  need to reverse before the part buckles at all — the member is in
  tension, not compression. OpenOptima reports this case as "does not
  buckle", not as a dangerously low factor.
- **Two nearly equal modes** mean the part is symmetric, and can buckle
  in either of two directions. OpenOptima flags this case, because the
  real margin is thinner than a single mode would suggest.
- OpenOptima does not write mode shapes to the results file, to keep file
  sizes manageable across a large study. The solver deck stays in the run
  directory, so you can re-run one chosen design with mode-shape output,
  for a closer review.

## Load application

- **Forces.** OpenOptima applies a force as a **consistent nodal load**: a
  set of node-by-node loads, calculated by integrating the true, curved
  shape of each element's face — not simply divided evenly. (This
  calculation uses the element's "shape functions": the maths that
  describes how an element deforms between its nodes.) On a quadratic
  (second-order) face, this correctly places zero load at the corner
  nodes, and all of the load at the midside nodes. The result is a
  *distributed* load spread over the whole region, not a load
  concentrated at a point. This avoids creating an artificial stress
  concentration that is not really there.
- **Pressure** becomes a `*DLOAD` element-face pressure. CalculiX then
  integrates this pressure over the element's own curved geometry, in the
  correct direction.
- **Fixed supports** restrain all three translations (movement in the x,
  y, and z directions) across the whole region. This is stiffer than
  reality: a real bolted joint is never perfectly rigid. Expect two
  things at a fixed support: the model will be stiffer than the real
  part, and the stress at the edge of the support will be singular. This
  is why the mounting face usually belongs in
  `stress_evaluation.excluded_regions`.

## Equilibrium checking

For every load case, OpenOptima compares the applied load against the
solver's total reaction force. A mismatch greater than 1% raises a
warning. This is an automatic, whole-model check. It catches a load on
the wrong face, a missing constraint, and unit mistakes — all of which
would otherwise pass silently, with no warning. **If you see this
warning, do not proceed.**

## Mesh

- OpenOptima uses second-order tetrahedra (element type `C3D10`) by
  default. First-order tetrahedra are far too stiff for stress work.
  OpenOptima checks the mesh, and stops if the mesher did not produce the
  element order it asked for.
- OpenOptima checks mesh quality: no inverted elements; a minimum "scaled
  Jacobian" above a configured floor (a standard 0-to-1 score for how
  distorted an element's shape is, where 1.0 is perfect); and the mesh
  volume within tolerance of the true CAD volume.
- **OpenOptima uses one mesh setting for the whole study.** You can
  fairly compare results against each other, but they are not
  **converged** — the numbers have not yet stopped changing as you refine
  the mesh further. Before you trust any one value, re-run the chosen
  design at finer mesh settings, and confirm the numbers have settled.
  Displacement, and strain energy (a single number summarising how much
  the whole part deforms), converge quickly. Peak stress at a real stress
  concentration converges slowly. Peak stress at a singularity never
  converges at all.

## Multiple load cases

OpenOptima always **envelopes** load cases — it takes the worst result
across all of them — and never averages them. The reported constraint
metric is always the worst case across every load case. OpenOptima also
reports each load case's own value alongside it, as `metric.load_case_id`.
Averaging a failing case together with a passing one would hide the
failure.

## What the optimiser will and will not do

OpenOptima searches only the design space you defined — the dimensions you
allowed it to change. It cannot invent a new rib, change the part's basic
topology (its fundamental layout — where material exists at all, not just
how much), or notice that a completely different concept would work
better. A "minimum mass" result is the minimum mass *within your chosen
parameterisation*, subject to *your* constraints, under *this* analysis
only.

## Before trusting a result

1. `openoptima doctor` passes, with every region resolving uniquely across
   the whole design range.
2. No equilibrium warnings appear.
3. You have checked the mesh convergence of the chosen design.
4. The allowable stress and its basis are ones you would defend.
5. The load cases genuinely bound the real loading the part will see in
   service.
6. Buckling is either enabled and within its validity range, or you have
   assessed it separately. You have assessed fatigue, and the other
   omitted phenomena, separately, regardless of the buckling result.
7. A qualified person has looked at the deformed shape, and the full
   stress pattern across the part — not just the summary numbers.

The software's job is to compute and present. Judging is the engineer's.
