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
- forced vibration, resonant response, impact;
- residual stress, weld effects, or process history;
- temperature dependence and thermal stress;
- fracture and crack growth.

**OpenOptima can analyse buckling** and **natural frequencies** — each has
its own section below. Both are off by default. Buckling has a limited
range of validity; natural frequency analysis says which rates are
dangerous but never how hard a part actually shakes at one. Every other
item in the list above stays invisible to OpenOptima, and an optimiser
will exploit every one of them, given the chance. Minimising mass, under only a
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

**An imported CAD file carries its own units, and they are converted.** A
STEP file states what it was drawn in. OpenCASCADE converts that to
millimetres on import, and both mechanisms the format uses were measured
rather than assumed: a file declaring inches through a
`CONVERSION_BASED_UNIT` comes in at exactly 25.4× its stated numbers, and
one declaring metres through an SI prefix at exactly 1000×. So a part
drawn as 100 × 10 × 5 inches arrives as 2540 × 254 × 127 mm, which is the
same physical part.

Everything in the project file stays in the internal system regardless.
A region box, a load, a displacement limit: all millimetres and newtons,
whatever the CAD file was drawn in. Because a correct conversion still
produces numbers the user did not type, `openoptima doctor` reports the
size of an imported part so a mismatch is visible before a study starts.
See `tests/integration/test_step_import.py::TestTheDeclaredUnitIsHonoured`.

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

There used to be a serious defect here. It is worth knowing what it was,
because the fix changed what this software can be trusted to do.

**The defect.** CalculiX silently skipped the lowest buckling mode and
reported the second one instead — about **nine times too high**, in the
unsafe direction, with nothing in its output to say so. A part that folds
under half its load was reported with a comfortable margin of four.

**What actually triggered it.** Not slenderness, which is what OpenOptima
originally guarded against. The trigger is the buckling factor itself: if
the true factor against the applied load fell below about **0.52**,
CalculiX skipped the lowest mode. That threshold measured identically on
three different columns, at slenderness 69 and at 277 alike. Asking for
more modes did not help — at twenty modes, the true one was still absent.

That made the original slenderness guard wrong in both directions. It
refused correct answers on slender members, and it let the real failure
through on stubby ones: a 40 mm column at slenderness 69, far inside the
"safe" range, was reported 8.4 times too high with no warning at all.

**The fix.** A buckling factor is exactly inversely proportional to the
load it is measured against, so OpenOptima now solves the buckling step
against a load a thousand times smaller and divides the answer back. Every
factor lands far above the threshold, and the division is exact. A part
folding under a thousandth of its load still comes back correctly.

Every case that previously failed now measures within 0.15% of Euler,
including a column at slenderness 433. See V9 in
[`verification-plan.md`](verification-plan.md) for the full sweep.

**The limit that remains.** `buckling.slenderness_limit` still defaults to
150 and still refuses results beyond it. That limit was set against the
defect above, and is now deliberately conservative: the measurements go to
433 without trouble. It has been left where it was because widening a
verified range is an engineering decision, not one this software should
make for you. If your work needs slender members, raise it knowingly, or
use beam elements or a hand Euler calculation.

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

## Natural frequencies

OpenOptima can report the rates a part likes to vibrate at. Turn it on:

```yaml
modal:
  enabled: true
  modes: 6
```

The result is a list of frequencies in hertz, lowest first, and the lowest
becomes `natural_frequency_hz`. Constrain it in either direction: above a
limit for a part that must stay clear of something driving it, below one
for a mount deliberately tuned to isolate. Verified as V14 in
[`verification-plan.md`](verification-plan.md), to +0.27% against the
published cantilever answer.

**What it depends on.** Stiffness and mass, and nothing else. The load
does not enter the calculation, so two load cases holding the part the same
way have identical frequencies and OpenOptima solves once for both. This
was measured, not assumed: the same part at 1 N and at 5000 N returns the
same numbers to the last digit.

**What it does not cover.**

- **No stress stiffening.** A part under heavy tension is stiffer, and so
  vibrates faster — the reason a tightened guitar string rises in pitch. A
  part in compression goes the other way, and near its buckling load the
  effect is large. OpenOptima ignores it, which is accurate for a lightly
  loaded part and optimistic for a heavily compressed one.
- **No damping, so no amplitude.** This says which rates are dangerous. It
  says nothing about how far the part actually moves at one, because that
  depends on how quickly the part bleeds off energy — which is not
  modelled, and is dominated by joints and fixings rather than by the
  material.
- **No forced response and no fatigue life.** Knowing a frequency is close
  to a drive rate tells you there is a problem. It does not tell you how
  many cycles the part survives.
- **A free part has no answer.** Six modes at zero hertz mean the supports
  do not hold the part still. OpenOptima refuses with `model_not_held`
  rather than reporting the next mode up, which would be the frequency of
  a part held in a way the project never described. Free-free analysis —
  the deliberate case, as for something in flight — is not supported.
- **Mode shapes are not written.** Only the frequencies. The deck stays in
  the run directory, so one chosen design can be re-run with mode-shape
  output for review.

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

## Where the time goes

Measured on one evaluation of the L-bracket at 55 272 elements, 92.1 s
total:

| | Time | Share |
|---|---|---|
| CalculiX solving | 75.1 s | **82%** |
| Gmsh meshing | 10.4 s | 11% |
| Reading solver output | 2.9 s | 3% |
| Writing the solver deck | 2.1 s | 2% |
| Building the geometry | 1.1 s | 1% |
| Everything else in Python | ~0.5 s | <1% |

**93% of the time is already inside compiled code.** OpenOptima never
calculates elements itself. It writes a text file, hands it to CalculiX,
and reads the answer back.

This is recorded because it settles a question that comes up often:
whether to speed the software up by rewriting its Python arithmetic into
array operations. The measurement says no. Even making every line of
Python instant would save under 7%, while running designs in parallel —
which already happens — gives a factor equal to the number of cores.

If a run is too slow, the levers that actually matter are the mesh
density, the number of designs, and the number of cores. Not the Python.

## Materials that are not equally strong in every direction

A 3D-printed part is weaker *between* its layers than *along* them, because
the layers are fused rather than continuous. It therefore has no single
allowable stress, and von Mises stress cannot describe it: von Mises
assumes equal strength in every direction, which is exactly what such a
material is not.

Where you give directional strengths, OpenOptima computes a factor of
safety from a proper criterion instead. Two are available.

| Criterion | Use when |
|---|---|
| `hoffman` (default) | general use. Accounts for stresses acting together, and for a material being stronger in compression than in tension |
| `max_stress` | Hoffman refuses your material, see below |

Declare one in the project file under `material.printed`:

```yaml
material:
  name: PLA, printed solid
  density_kg_m3: 1240.0
  failure_criterion: hoffman        # or max_stress
  printed:
    build_direction: [0.0, 0.0, 1.0]
    along_layers_modulus_mpa: 3500.0
    through_layers_modulus_mpa: 2600.0
    in_plane_poisson: 0.36
    through_layers_poisson: 0.33
    through_layers_shear_modulus_mpa: 1100.0
    strength:
      along_layers_tension_mpa: 22.0
      through_layers_tension_mpa: 11.0
      along_layers_compression_mpa: 30.0
      through_layers_compression_mpa: 28.0
      in_plane_shear_mpa: 16.0
      through_layers_shear_mpa: 9.0
      basis: "measured, divided by a design factor of 2.5"
```

An ordinary material and a printed one are mutually exclusive: giving both
`printed:` and `elastic_modulus_mpa` is refused, because a printed part has
no single modulus and no single allowable stress. Worked example:
`examples/drone_arm/project.yaml`.

**`build_direction` changes the answer, and the stress does not show it.**
Measured on that example — one arm of a quadcopter, identical shape,
identical loads, identical mesh, only the print direction changed:

| | printed flat `[0,0,1]` | printed upright `[1,0,0]` |
|---|---|---|
| 99th-percentile stress | 7.53 MPa | 7.54 MPa |
| Factor of safety | 3.07 | 1.55 |
| Tip displacement | 1.82 mm | 2.42 mm |
| Verdict | feasible | **infeasible** |

The stress is identical to three significant figures while the factor of
safety halves. Any check based on von Mises stress sees nothing.

**Hoffman has a hard limit, and OpenOptima refuses rather than hides it.**
Past that limit its failure surface stops being closed: it predicts that
one particular combination of stresses — pulling along the layers while
pressing across them — never causes failure at all, at any magnitude. That
is not a conservative error. It is an infinitely optimistic one, and
nothing in the arithmetic reveals it. So OpenOptima refuses the material
and tells you to use `max_stress`, which is always well posed.

**The limit binds on the product of tension and compression on each axis,
not on strength alone**, and the difference matters in practice. The
admissibility test requires the through-layer product to exceed **a
quarter** of the in-plane product. Where tension and compression fall
together, that is the familiar "weakest under half the strongest" — a
factor of 0.51 on both is accepted and 0.50 is refused, measured. But a
print is only bad at being *pulled apart*: layers press together perfectly
well, so through-layer compression stays high and holds the product up.
Measured with in-plane strengths at 22/30 MPa and through-layer
compression at 28 MPa, `hoffman` is accepted with through-layer tension as
low as 6.0 MPa (product ratio 0.2545) and refused at 5.0 MPa (0.2121).
That is a tension ratio of 0.27, not 0.5. Most real prints therefore stay
inside Hoffman, and only a badly bonded one needs `max_stress`.

**The refusal happens when the project file is read**, not after a solve,
so a material Hoffman cannot bound costs seconds rather than a whole
optimisation run.

Two further points that are easy to get wrong, and are handled for you:

- **The stress is rotated into the material's own axes first.** "Pulled
  across the layers" is meaningless in the model's global axes. CalculiX
  writes its results file in global axes even when the material has a
  local orientation, so the rotation happens in OpenOptima.
- **The factor of safety is not one over the square root of the failure
  index.** The criterion mixes squared and plain stress terms, so the
  multiplier that reaches failure comes from solving a quadratic. The
  shortcut is wrong in the unsafe direction, by about 6 per cent on a
  typical printed material and more as tension and compression diverge.

The directional strengths are **design decisions**, exactly like
`allowable_stress_mpa`. OpenOptima will not infer them.

## Things the part carries but is not made of

A motor on the end of an arm, a camera on a mount, a battery on a tray.
Declare them under `point_masses`:

```yaml
point_masses:
  - name: motor
    region: motor_pad
    mass_kg: 0.035
```

**They add mass and weight, never stiffness.** The mass is spread evenly
over the nodes of the named face.

**This exists mostly because of natural frequency.** A frequency comes
from stiffness and mass, and on a part whose job is to carry something the
carried thing is most of the mass. Measured on `examples/drone_arm`, a
150 mm arm carrying a 35 g motor:

| | First natural frequency |
|---|---|
| Motor in the model | 121.5 Hz |
| Motor left out | 191.4 Hz |

The bare figure is 58% high, in the direction that looks safe.

Three details that each change a number:

- **A carried mass is not part of `mass_kg`.** That metric is the mass of
  the part itself, which is what an optimiser can reduce. Including the
  motor would change what "minimise mass" means and put an unremovable
  35 g into every result.
- **It has weight under an acceleration load.** A CalculiX `MASS` element
  is not part of the `Eall` element set, so a gravity load naming only
  `Eall` leaves it weightless — measured at 0.3843 N against 2.3463 N on
  the V15 benchmark, with a clean exit and nothing in the log. OpenOptima
  names every mass element set in every gravity load for that reason.
- **The mass is split by node count, not by the consistent rule used for a
  surface load.** The consistent weights integrate the element shape
  functions, and for a quadratic face those integrals are zero at the
  corner nodes and negative for some element types. That is correct for a
  load. Applied to a mass it would put zero mass on the corners and a
  negative mass elsewhere, and a negative mass makes an eigenvalue solve
  meaningless. The total is exact either way, and the total is what sets
  the frequency.

**What is not modelled**, and both make the true frequency *lower* than
the reported one, so treat the answer as an upper bound:

- **The carried thing has no size.** Its centre of gravity is taken to lie
  on the mounting face. A real motor's centre sits above its pad.
- **No rotary inertia.** A carried item resists being turned as well as
  being moved, and only the second is counted.

Neither is large for a compact item on a slender part.

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

## Corners OpenOptima adds to an imported shape

An imported CAD file holds no dimensions, so a project can ask OpenOptima
to add a rounded corner (`fillet`) or a corner cut off flat (`chamfer`) on
top of it and vary that. Three things about those are engineering facts
rather than software details, and each one can change a number.

**The corner is real material, and it changes the answer.** It is applied
before meshing, so the stress, the deflection and the mass are all those of
the part *with* the corner. Nothing is approximated or added afterwards.

**The corner shrinks the faces beside it, and your loads stay on them.**
This is the one to watch. A load or a support attached to a face that the
corner has trimmed back is now spread over a smaller area, at a
correspondingly higher stress, and the analysis is a correct analysis of a
part you may not have intended. Measured on the example bracket: the loaded
end face falls from 1140 mm² to 240 mm² at a 15 mm radius and to 0.6 mm² at
18.99 mm, with the load resolving onto it at every step and no warning
raised.

Set `min_area_mm2` on any region whose area you are relying on. Below it,
the design is rejected as infeasible and the optimiser learns to stay
away. **There is no default, and OpenOptima will not choose one for you**:
how small is too small depends on what the face represents — a bolted
joint, a bearing pad, a pressure face — and that is your judgement.
`openoptima doctor` reports every region's area at both ends of the design
range whether or not you set a floor, so you can see a face collapsing
before a study starts.

**A corner that cannot be built is a bad design, not a broken run.** Asking
for a round larger than the material around the corner is refused by the
CAD kernel, and OpenOptima reports that as infeasible so the search treats
it as a boundary. Note that the refusal comes later than you might expect:
on a 19 mm tall face, an 18.99 mm round builds without complaint and only
19.0 mm is refused. The kernel refusing is not a safety net for the case
above.

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
