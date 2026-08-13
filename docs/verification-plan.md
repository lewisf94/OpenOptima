# Verification plan

Verification asks a different question from testing. Testing asks: "does
the software do what it was told?" Verification asks: "does what it was
told produce real physics?" Both questions matter. Only the second one
gives anyone a reason to believe a number.

## Rule

**Never widen a verification tolerance to make a build pass.** If a
verification test fails, either the code has regressed, or the physics
has changed. Either way, a human must look. An agent that adjusts one of
these numbers just to make tests pass has destroyed the only evidence that
the software computes correctly.

## Implemented benchmarks

### V1 — Cantilever beam, tip deflection

`tests/verification/test_cantilever_beam.py`

| | |
|---|---|
| **Model** | 200 × 20 × 20 mm prismatic bar, fully fixed at x=0 |
| **Material** | E = 70 GPa, ν = 0.33 |
| **Load** | 1 kN transverse, distributed over the free end face |
| **Mesh** | C3D10, ~5 mm global size |
| **Reference** | Timoshenko: δ = FL³/3EI + FL/κGA, κ = 5/6 |
| **Reference value** | 2.857 mm bending + 0.023 mm shear = **2.880 mm** |
| **Tolerance** | 3% |
| **Measured** | **2.852 mm, −0.98%** |

The finite-element result must be slightly **stiffer** than beam theory
predicts. This is because fully fixing the end face suppresses the
Poisson contraction that beam theory allows for. A result on the *soft*
side indicates a real problem: the wrong element order, the wrong
constraint, or a load that is not doing what it should. It does not
merely indicate a coarse mesh. This test checks the sign of the
discrepancy, as well as its size.

This test also checks:

- reaction force equals the applied load to 1 part in 10⁴ (global
  equilibrium — this is exact arithmetic, not an approximation, so the
  tolerance is tight);
- no spurious transverse reaction;
- peak von Mises stress consistent with the 150 MPa nominal bending
  stress;
- mesh volume matches CAD volume;
- the mesh is genuinely C3D10 elements — if the mesher had silently
  fallen back to first order, the deflection comparison would be
  meaningless, so this test checks the element type explicitly.

### V2 — Analytic reference guard

The formulas in the test file are themselves checked against hard-coded
values. This means a typo in a reference value cannot silently change
what counts as a pass.

### V3 — Euler column buckling

`tests/verification/test_euler_buckling.py`

| | |
|---|---|
| **Model** | 400 mm cantilever column, 20 x 20 mm square section |
| **Material** | E = 70 GPa, nu = 0.33 |
| **Load** | 1 kN axial compression on the free end face |
| **Reference** | Euler, fixed-free: P_cr = pi^2 EI / (2L)^2 = **14 393 N** |
| **Tolerance** | 3% |
| **Measured** | **14 409 N, +0.11%** |

This test also checks:

- the first two modes are nearly equal (a square column can buckle about
  either axis)
- the higher modes are correctly ordered
- a column in **tension** returns no positive buckling factor, and says
  so, instead of reporting a negative number
- OpenOptima reports the paired-mode condition to the user

**Known limit.** This method is accurate to within 1% at a slenderness of
69–277, for a 20 mm section. But it is wrong by a factor of nine at a
slenderness of 195 or more, for smaller sections — and the error runs in
the optimistic direction. `results/buckling_check.py` checks every result
against beam theory, and refuses to report a result outside the validated
range. The next useful piece of work here is finding exactly where that
boundary lies. That needs a systematic sweep of section size against
slenderness.

### V4 — Plate with a central hole (stress concentration)

`tests/verification/test_plate_with_hole.py`

This checks a **real** stress concentration: a feature with a finite radius,
where the peak stress is a genuine physical number. V6 checks the opposite
case, a singularity where no such number exists. Together they draw the
line the whole stress-measure argument rests on, and V4 is the half that
had never been tested.

| | |
|---|---|
| **Model** | 300 × 100 × 5 mm plate, 20 mm central hole |
| **Load** | 50 kN tension along the length |
| **Reference** | Howland, in Heywood's form: Kt_net = 2 + (1 − d/W)³ |
| **Reference value** | Kt_net = 2.5120 (Kt_gross = 3.140; Howland tabulates 3.14) |
| **Expected peak** | 2.5120 × 125 MPa net = **314.0 MPa** |
| **Tolerance** | 4% |

Measured at four mesh densities (4 733 to 21 832 elements):

| Global / hole size | Peak at hole | Error |
|---|---|---|
| 8.0 / 4.0 mm | 309.48 MPa | −1.44% |
| 6.0 / 2.5 mm | 319.66 MPa | +1.80% |
| 5.0 / 1.6 mm | 316.11 MPa | +0.67% |
| 4.0 / 1.0 mm | 316.76 MPa | +0.88% |

Reaction equals the applied 50 kN exactly at every density.

**On the scatter.** These do not settle monotonically; they wobble inside a
3.2% band. That is expected, and it is not the same thing as V6's
singularity running away. The reported peak is whichever *node* lands
nearest the hottest point on the hole, and remeshing moves the nodes around
the arc. What matters is that the band is bounded and stays put: **3.2%
across a fourfold change in element count, against 19.8% and accelerating**
for the singularity in V6.

A real concentration has a real answer and the mesh scatters around it. A
singularity has no answer and the mesh climbs away from it. That contrast
is the measured evidence for reporting a percentile rather than the raw peak
by default — and for the advice that a genuine fillet should be modelled and
refined rather than hidden behind a percentile.

### V5 — Thick cylinder under internal pressure

`tests/verification/test_thick_cylinder.py`

This benchmark verifies the **pressure load path**. Until it existed,
pressure loading was covered only by unit tests of the element-face
lookup. No test had ever checked that a pressure applied through `*DLOAD`
produces the right stresses in a real part. Every force-loaded case in the
suite exercises a completely different code path.

| | |
|---|---|
| **Model** | Quarter of a thick-walled cylinder, bore 50 mm, outer 100 mm, 40 mm tall |
| **Material** | E = 70 GPa, nu = 0.33 |
| **Load** | 50 MPa internal pressure on the bore |
| **Restraints** | Symmetry on both cut faces, axial restraint on both ends (plane strain) |
| **Mesh** | C3D10, 6 mm global size, 5 712 elements |
| **Reference** | Lame, with the axial stress plane strain implies |

Only a quarter is modelled. This reproduces the whole cylinder at a
quarter of the cost, and removes a real difficulty: a complete ring under
internal pressure is in balance with itself, so nothing holds it in place
and the solve has no unique answer.

von Mises stress, checked at five radii through the wall rather than at
one point:

| Radius | FE | Lame | Error |
|---|---|---|---|
| 50.0 | 114.539 | 115.609 | **−0.93%** |
| 62.5 | 73.975 | 74.118 | −0.19% |
| 75.0 | 51.590 | 51.632 | −0.08% |
| 87.5 | 38.170 | 38.128 | +0.11% |
| 100.0 | 29.493 | 29.418 | +0.25% |

Radial displacement agrees to −0.33% at the bore and +0.07% at the outer
surface. Tolerance 3% on stress, 2% on displacement.

**The exact check.** The resultant of pressure over a quarter bore is
`p × a × h` = 100 000 N in each of x and y — the projected area is exactly
a rectangle, whatever the mesh does with the curve. Measured: −100 001.3 N
and −100 000.8 N, correct to about **one part in 100 000**. That single
number verifies the pressure magnitude, its direction, and the
shape-function integration behind it.

**A defect this benchmark found.** OpenOptima used to total the reaction
by adding every component of every restrained set. CalculiX reports a full
`(fx, fy, fz)` for each set, including the directions that set leaves
free, and those figures are not reactions. Here the x-symmetry set reports
its true fx of −100 001 N together with a spurious fy of +1 560 N. Adding
everything gave a total 1.7% short, so the equilibrium check reported a
1.7% error on an analysis correct to one part in 100 000 — on every model
that uses symmetry. A false alarm on a sound model is not harmless: it
trains people to ignore the check that exists to catch a load on the wrong
face. Reactions are now assembled one direction at a time, from the sets
that restrain that direction.

### V6 — Mesh convergence of the cantilever

`tests/verification/test_mesh_convergence.py`

Every other benchmark compares one mesh against theory. This one compares
a sequence of meshes against itself, and asks the question every result
in this software depends on: have the numbers stopped changing?

| | |
|---|---|
| **Model** | The V1 cantilever, run at four mesh densities |
| **Meshes** | 3.16, 2.51, 1.99, 1.61 mm average element size |
| **Elements** | 2 537, 5 045, 10 163, 19 298 (C3D10) |
| **Reference** | Timoshenko, as V1, plus the sequence against itself |

Two quantities from the same four solves behave in opposite ways:

| Quantity | Behaviour | Moved in total | Extrapolated |
|---|---|---|---|
| Tip deflection | settles | **0.058%** | **2.8658 mm** (−0.49% vs Timoshenko) |
| Raw peak von Mises | never settles | **19.8%** | none — no limit exists |

The deflection climbed 2.8620 → 2.8636 mm, softening with refinement as a
coarse mesh should. The raw peak climbed 147.3 → 183.7 MPa, and the steps
got **larger** with each refinement. That is the stress singularity at the
fully fixed face: the true elastic stress there is unbounded, so no mesh
will ever converge, and refining only makes the number bigger.

The ratio between those two spreads is about **340**. That single figure
is the measured evidence behind the rule that OpenOptima must not optimise
raw peak stress. A design search handed the peak would be searching the
mesh, not the design.

**A result neither V1 nor V6 could establish alone.** V1 measures −0.98%
against beam theory at a single 5 mm mesh. Extrapolating to zero mesh size
gives −0.49%. So roughly half of V1's discrepancy is mesh coarseness, and
the other half is the genuine physical stiffening from suppressing Poisson
contraction at the built-in end.

Also checked: every mesh used second-order elements; the reaction force
equalled the applied load at every density (equilibrium is exact
arithmetic and does not depend on the mesh); and the extrapolated
deflection still lands on the stiff side of beam theory, because refining
cannot remove a physical difference between the two models.

### V7 — Load cases are independent, and enveloped

`tests/verification/test_load_case_independence.py`, plus
`tests/unit/test_load_case_envelope.py` for the arithmetic without a solver.

Two properties, both of which fail silently if broken.

**Independence.** Each load case is written as its own `*STEP` with `OP=NEW`
on the loads and boundary conditions. Without `OP=NEW` the second case would
carry the first one's load as well, and every case after the first would be
analysed under a load nobody asked for. Nothing in the output would say so —
the numbers would simply be too high, consistently.

**Enveloping.** The reported metric is the worst case, never the mean.
Averaging a failing case against a passing one hides the failure.

The V1 cantilever is solved with two unrelated loads — 1 kN downwards and
3 kN sideways, differing in direction *and* magnitude — then each case is
solved again on its own. A two-case run and two single-case runs are the
same physics, so the per-case numbers must agree to solver precision. They
agree to within **1 part in 10⁶**, and each case reacts only against its own
load: no sideways reaction on the downward case, none downward on the
sideways one.

Until this landed, the "never average across load cases" invariant in
`AGENTS.md` had no test at all. The unit half pins the arithmetic with
numbers chosen so a mean would land in the *safe* range while the truth is
not: 60 and 180 MPa against a 200 MPa allowable give a true factor of safety
of 1.11, where the average would report 1.67.

### V9 — Buckling validity boundary

`tests/unit/test_buckling_load_scale.py`, plus the sweep recorded here.

This one was expected to refine a bound. It found a defect instead, and
the defect was in the most dangerous place in the software.

**What was believed.** That solid tetrahedral elements stop being reliable
for buckling once a member becomes slender, and that a limit of 150 on
slenderness kept results safe.

**What the sweep measured.** Slenderness has nothing to do with it. Three
columns of different sizes, swept over section, length, mesh density and
reference load:

| Case | Slenderness | True factor | Reported | Verdict |
|---|---|---|---|---|
| 20×20×800 | 277 | 1.00 | 1.0017 | correct |
| 20×20×800 | 277 | 0.36 | **8.98× high** | wrong |
| 40×40×400 | 69 | 0.90 | 0.9985 | correct |
| 40×40×400 | 69 | 0.50 | **8.44× high** | wrong |
| 30×30×300 | 69 | 0.50 | **8.44× high** | wrong |
| 8×8×500 | 433 | 2.36 | 1.0012 | correct |

Same geometry, same mesh, same slenderness — only the reference load
differs — and the answer flips. The trigger is the **buckling factor**:
below about **0.52**, CalculiX skips the lowest mode and returns the second
one. The threshold measured identically on every geometry tried. Mesh
density is irrelevant (1 to 4 elements across the section, no change), and
asking for more modes does not help — at twenty modes the true one is
still absent.

The tell is the mode series. A fixed-free column runs 1 : 9 : 25. When the
lowest mode is skipped, the reported series is 1 : 2.77 — which is 25/9,
the second and third modes with the first missing.

**Why this mattered urgently.** The old guard watched slenderness, so it
refused correct answers on slender members *and missed the real failure on
stubby ones*. A 40 mm column at slenderness 69 — far inside the range the
software called safe — reported a part that folds under half its load as
having a margin of 4.2, silently.

**The fix.** The buckling eigenvalue is exactly inversely proportional to
the reference load, so the `*BUCKLE` step is now written with its loads
divided by 1000 and the returned factors divided by 1000 again. Every
factor lands far clear of the threshold and the conversion is exact. A part
folding under a thousandth of its load still reports correctly.

**After the fix**, every previously failing case measures within 0.15% of
Euler:

| Case | Slenderness | Before | After |
|---|---|---|---|
| 40×40×400 at factor 0.50 | 69 | 8.44× high | **0.9985** |
| 40×40×400 at factor 0.30 | 69 | 8.44× high | **0.9985** |
| 30×30×300 at factor 0.50 | 69 | 8.44× high | **0.9986** |
| 20×20×800 at factor 0.36 | 277 | 8.98× high | **1.0017** |
| 8×8×400 at factor 0.37 | 346 | 8.99× high | **1.0015** |
| 8×8×500 at factor 0.24 | 433 | 9.00× high | **1.0012** |

V3 is unchanged by this: 14.4086 against Euler's 14.3932, as before.

**Still open, for a human.** `buckling.slenderness_limit` still defaults to
150. That limit was calibrated against the defect above and is now
conservative — the sweep goes to 433 without trouble. It has deliberately
**not** been widened, because that is an engineering judgement rather than
a measurement.

### V10 — Orthotropic material is directional

`tests/verification/test_orthotropic_material.py`

A 3D-printed part is weaker between its layers than along them. Until this
landed, OpenOptima assumed one stiffness in every direction, so a printed
part could be reported as safe and then peel apart along its layers.

The same bar, the same mesh, the same load — pulled once along the print
layers and once through them. Only the build direction changes. That
comparison is the strongest available: the ratio of the two answers is the
ratio of the two moduli **exactly**, independent of load, length, section
and mesh.

| | |
|---|---|
| **Model** | 200 × 20 × 20 mm bar, 5 kN axial tension |
| **Material** | 3500 MPa in the layer plane, 2100 MPa through the layers |
| **Reference** | Extension of a bar, δ = FL/AE, and the modulus ratio |

| | Measured | Expected | Error |
|---|---|---|---|
| Along the layers | 0.712080 mm | 0.714286 mm | **−0.31%** |
| Through the layers | 1.188450 mm | 1.190476 mm | **−0.17%** |
| Ratio | 1.668984 | 1.666667 | **+0.14%** |

Reaction equals the applied 5 kN exactly in both. Tolerance 2% on the
stretches, 1% on the ratio.

**What this does not claim.** It verifies the *stiffness* is directional.
It says nothing about strength: von Mises assumes equal strength in every
direction and is the wrong failure measure for such a material. An
orthotropic material without directional strengths therefore **withholds
the factor of safety** rather than reporting a misleading one. Stresses
and displacements are still reported and are correct.

**Also checked, without a solver** (`tests/unit/test_material_deck.py`):
an isotropic material still writes exactly the two-number `*ELASTIC` block
it always did, with no orientation. Every verified benchmark in this
document rests on those decks, and this feature must not move any of them.

## Planned benchmarks

This list is ordered by value. Each benchmark needs a documented source
and tolerance before anyone writes it.

*(V4, V5, V6 and V9 are implemented — see above.)*

*(V7 is implemented — see above.)*

### V11 — The buckling load scaling is load-bearing

`tests/verification/test_buckling_load_scaling.py`

V3 and V9 both check buckling where the answer is comfortable: a 1 kN load
on a column that carries 14.4 kN. That is well clear of the range where
CalculiX misbehaves, so **both would still pass if the fix were deleted.**
This benchmark sits inside the dangerous range instead.

**The measurement.** One column, one mesh, only the applied load changed.
The buckling factor is exactly inversely proportional to the load it is
measured against, so `factor x load` must give the same critical load
every time. Without the scaling, it does not:

| Applied load | Scaling | Factor | Implied critical load | Against Euler |
|---|---|---|---|---|
| 1 000 N | none | 14.4086 | 14 409 N | 1.00x |
| 1 000 N | 1000x | 14.4086 | 14 409 N | 1.00x |
| 30 000 N | none | 4.2523 | 127 569 N | **8.86x** |
| 30 000 N | 1000x | 0.4803 | 14 409 N | 1.00x |
| 60 000 N | none | 2.1261 | 127 569 N | **8.86x** |
| 60 000 N | 1000x | 0.2401 | 14 409 N | 1.00x |

A column that folds under 14.4 kN is reported as surviving 128 kN. The
error is **optimistic**, and an optimiser reads the number rather than any
warning attached to it.

Euler gives 14 393 N. Every scaled result is within 0.11%. Tolerance 3%.

**Do not remove the scaling.** One test here deliberately removes it and
asserts the wrong answer comes back, so that deleting the fix fails the
build loudly instead of quietly.

**What this settles for topology optimisation.** `beso` offers buckling as
an optimisation objective, drives the same CalculiX, and applies no such
scaling. It is therefore affected. Its buckling objective is refused in
`topology/config.py` rather than passed on, and that refusal is checked
here, next to the evidence for it.

### V12 — A topology run, end to end

`tests/verification/test_topology_cantilever.py`

**What this does not claim.** It does not compare against a published
compliance value from the topology literature. Those come from a different
method (density-based SIMP, usually two-dimensional) on a different mesh,
and compliance does not carry across formulations. Quoting one as
validation would be theatre.

It checks what this project owns: that the run produces a sound structure,
that turning it into a solid keeps that structure, and that its
performance is measured rather than assumed.

**Material goes where bending needs it.** A short cantilever, 60 x 20 x 4
mm, loaded at the free corner. Beam theory says material earns its place
furthest from the middle of the section. Measured at 40% material kept:

| Depth band | Material kept |
|---|---|
| y = 15-20 mm | 57.8% (top flange) |
| y = 10-15 mm | 34.2% |
| y = 5-10 mm | **22.2% (thin web)** |
| y = 0-5 mm | 45.0% (bottom flange) |

That is an I-beam, which is the right answer.

**The result must be re-analysed, and here is why.** Re-analysed properly,
with the void elements removed and the same load applied:

| | Strain energy |
|---|---|
| Solid block, 600 elements | 71.6 mJ |
| Optimised, 239 elements (39.8% of the material) | 650.7 mJ |

**9.1 times less stiff for 40% of the material.** Whether that trade is
worth taking is the engineer's judgement. What matters here is that the
number cannot be predicted from the optimiser's own figure: beso's
`energy_density_mean` *rises* through the run, from 0.015 to 0.34, because
less material means each remaining piece works harder. It measures
sensitivity inside beso's own model and says nothing about the extracted
solid.

Much of the 9x is local. The load is a point load at a corner and the tip
was thinned to 24%, so a large share of the energy is the structure
squashing under the load rather than bending as a beam. That is a real
property of point loads in topology problems, and a reason to prefer a
distributed load.

**The run is pinned to one processor core, and must stay that way.** On
several cores the identical problem produced two different shapes. On one
core it is bit-identical. CalculiX's threaded arithmetic differs in its
last bits depending on how work lands on threads, and this optimiser turns
those bits into keep-or-remove decisions that compound over dozens of
rounds. A design that cannot be reproduced from its own inputs cannot be
defended, cached or verified.

**The benchmark runs at 50% material, not 40%, for a measured reason.** At
40% this shape pinches — two parts meet at a single edge, which is not a
solid — and the conversion refuses it. That refusal is correct and is
tested separately. It is not monotonic: 50% comes out sound and 60% pinches
again, so it depends on the shape rather than on how much material is left.

**The loop closes: the shape is analysed and reports real numbers.** The
optimised shape and the untouched block both go through the ordinary
pipeline — re-meshed into solid elements, same loads and supports resolved
by the same selectors, same solver:

| | Solid block | Optimised |
|---|---|---|
| Volume | 4800.0 mm³ | 2384.5 mm³ |
| Deflection | 0.1424 mm | 0.3310 mm |
| Stiffness | 7022 N/mm | 3021 N/mm |
| Stored energy | 69.1 mJ | 161.8 mJ |
| Peak stress | 217.0 MPa | 400.0 MPa |
| **Factor of safety** | **1.15** | **0.63** |

It keeps 49.7% of the material and 43.0% of the stiffness — and it **stops
passing**. At a 250 MPa allowable the factor of safety falls from 1.15 to
0.63. Nothing in the topology run says that: beso was asked for stiffness
at a mass target and delivered it, and stress was never part of the
question. Whether to accept the trade is the engineer's decision, and this
is the measurement that decision needs.

The block figure here (69.1 mJ) is a separate measurement from the 71.6 mJ
above. That one used beso's own hex mesh with the void elements deleted;
this one uses a fresh tetrahedral mesh built from the surface. They agree
to about 3%, which is a useful cross-check between two independent meshes.
The optimised figures are **not** comparable across the two, because they
are at different amounts of material kept.

One check inside this deserves naming. The face the part bolts to comes
back as **two separate pads**, because the optimiser removed the material
between them. Both must be held. If the selectors had found only one, the
part would be supported on half its mounting face and every number above
would be wrong while looking entirely reasonable.

### V13 — A shape made of triangles gives the same answer as CAD

`tests/verification/test_discrete_surface_agreement.py`

A shape reaches the analysis by one of two routes. The ordinary one is a
CAD model, which knows that this face *is* a plane and that one *is* a 6 mm
hole, because it was built that way. The other is a bag of triangles —
what a topology optimisation hands back — where nothing is known and every
face has to be measured.

That second route exists so a topology result can be analysed at all. It
is worth nothing unless it gives the same answer. A 60 x 20 x 4 mm steel
bar is therefore meshed and solved twice, once from a BREP file and once
from an STL of the same bar, with nothing else different:

| Compared with CAD | 4 mm triangles | 2 mm triangles | 1 mm triangles |
|---|---|---|---|
| Volume | 0.000% | 0.000% | 0.000% |
| Deflection | −0.004% | −0.006% | −0.008% |
| Stored energy | −0.003% | −0.005% | −0.007% |
| Peak stress | +1.691% | +1.832% | +0.233% |

Deflection and stored energy agree to under a hundredth of a per cent.
That is the important pair: they use the whole displacement field, so they
say the two routes built the same structure and loaded it the same way.

**Peak stress agrees less closely, and that is expected rather than
tolerated.** It is a high percentile of a field that peaks where the bar is
held, and the two runs place their mesh points differently, so they sample
that peak slightly differently. The error also does not fall steadily as
the triangles get finer, which is what sampling noise looks like; a real
error in the triangle route would grow or shrink with the faceting.
Tolerances: 0.2% on deflection and stored energy, 4% on stress.

**A hole must survive the trip**, or a selector written for a bolt hole
stops matching once the part comes back from a topology run. A plate with
a 3.000 mm hole comes back with exactly the seven faces CAD would report,
and the hole measures **3.0000 mm**. Fitting the middles of the triangles
instead of their corner points gives 2.967 mm, and a 1.1% error in a hole
radius is enough to pick the wrong hole.

**What this route cannot do**, stated because it is a real limit and not a
tolerance: a rounded blend between two faces cannot be found at all. A
blend runs smoothly into the faces it joins, so there is no crease in the
triangles to find it by, and it is measured as part of its neighbours. A
selector that asks for a blend by its radius will not match on a shape made
of triangles.

### V14 — The rates a part vibrates at

`tests/verification/test_natural_frequency.py`

Every object has rates it prefers to vibrate at. Flick a wine glass and it
rings at one note. If something drives a part at one of those rates, small
pushes add up into large movements, and the part can shake itself apart
under a load it would carry all day if the load were steady. A drone arm
beside a propeller is the standard case. A static analysis cannot see this
at all, so before this benchmark the software was blind to it.

A 100 x 10 x 5 mm steel cantilever, held at one end, against the published
beam formula:

| Mode | OpenOptima | Published | Difference | What it is |
|---|---|---|---|---|
| 1 | 418.9 Hz | 417.8 Hz | +0.27% | bending the easy way |
| 2 | 831.5 Hz | 835.5 Hz | −0.48% | bending the hard way |
| 3 | 2595.0 Hz | 2618.0 Hz | −0.88% | bending the easy way again |

The section is deliberately 10 x 5 rather than square. A square bar bends
identically two ways, so its first two modes land on the same frequency
and the test could not tell which one it had been given — nor notice if
one went missing.

**The difference is not mesh error and does not shrink.** Measured across
four mesh sizes from 4.0 mm down to 1.5 mm, the first mode moved from
+0.34% to +0.26% and then stopped. What is left is the difference between
a real three-dimensional part and the ideal beam the formula describes.
The tolerance is 2%.

**The mode ratios are the sharper check.** Bending the hard way is exactly
four times stiffer here, so that frequency is exactly twice the first —
and that follows from the shape alone. It does not depend on the steel,
the density, or the unit conversion, so it stays true even if every
absolute number were scaled by a units mistake. Measured: 1.9852 against
2.0000, and 6.1952 against 6.2669.

**The question that had to be answered first.** CalculiX silently skips
the lowest *buckling* mode when the answer falls below about 0.52, and
returns the second one — nine times too high, in the unsafe direction (V9,
and trap 7 in `AGENTS.md`). Anything solving an eigenvalue problem in the
same program has to be suspected of the same defect until measured. It was
measured: a long thin beam whose first frequency is **18.6 Hz** came back
as accurate (+0.20%) as the stubby one at 419 Hz (+0.27%), with the mode
ratios holding to 0.02%. There is no magnitude-dependent defect in the
frequency solve.

**The load makes no difference, and that is checked rather than claimed.**
A natural frequency comes from stiffness and mass; what pushes on the part
does not enter. The same part is run at 1 N and at 5000 N and must return
the same frequencies to the last digit. That is what allows load cases
held the same way to share one solve.

**A part that is not held has no frequency to report.** Held against the
load only, four of six modes came back at exactly 0 Hz with the first real
one at 1821 Hz — the part drifting and spinning freely rather than
vibrating. CalculiX reports that with no error and a successful exit code.
OpenOptima stops with `MODEL_NOT_HELD` and says which supports to check,
rather than discarding the zeros and answering a question nobody asked.

**What this does not cover**, stated plainly:

- **No load effect on the frequency.** A tightened guitar string rises in
  pitch, and a part under heavy tension or compression shifts the same
  way. That needs a different analysis and is not done here. For most
  parts the shift is small, but for a slender part near buckling it is
  not.
- **No damping.** Real parts lose energy as they vibrate, which limits how
  large the movement grows at resonance. This says which rates are
  dangerous, never how bad the shaking gets.
- **No answer for how long it survives.** Vibration is what drives the
  load cycles that break a part by fatigue, and fatigue is not built yet.

### V15 — Something heavy bolted on really is carried

`tests/verification/test_point_mass.py`

V14 above answers for a part carrying only itself. Most parts that need a
vibration check are not like that: a drone arm carries a motor, a mount
carries a camera, a tray carries a battery. That carried weight is usually
most of the moving mass, so leaving it out does not shift the answer
slightly — it changes it completely, in the direction that looks safe.

The same 100 x 10 x 5 mm steel cantilever as V14, now carrying 0.2 kg on
its free end. Against the closed form for a cantilever with a tip mass,
where `M_eff` adds the standard 0.2235 share of the beam's own mass:

    f = (1 / 2 pi) sqrt( 3 E I / (L^3 M_eff) )

| | OpenOptima | Published | Difference |
|---|---|---|---|
| Bare (V14) | 418.88 Hz | 417.8 Hz | +0.26% |
| Carrying 0.2 kg | 89.332 Hz | 89.232 Hz | **+0.11%** |

**The bare answer is 4.69 times the real one.** That is the whole case for
this benchmark existing.

The second mode is the same beam bending the stiff way. The section is
10 x 5, so its second moment of area differs by exactly 4 and its
frequency by exactly 2 — measured 1.9901. That ratio depends only on the
shape, so it catches an error in the material, the units or the mass that
a single absolute frequency could not.

**The second half of this benchmark is gravity, and it is the half that
failed silently.** A CalculiX `MASS` element is not in the `Eall` element
set, so a gravity load naming only `Eall` never reaches it:

| Gravity applied to | Support reaction |
|---|---|
| `Eall` only | 0.3843 N — the beam alone |
| `Eall` and the mass set | 2.3463 N (hand calculation 2.3470) |

The first figure comes back with exit code 0 and nothing in the solver
log. A part sized against an acceleration case would have been sized
without the thing it is carrying. The test fails with exactly that number
if the fix is removed.

**What this does not cover.** The carried thing has no size here: its mass
is spread over the face it bolts to, so its middle is taken to lie in that
face and it is taken not to resist being turned. Both leave the reported
frequency higher than the real one. **V16 below covers that**, and a
carried item that still has no size is reported as one.

### V16 — A carried item has a size, and the size changes the answer

`tests/verification/test_carried_size.py`

V15 proves something bolted on is carried at all. It was still carried
flat. A real motor's middle sits above its pad and it is a solid object
that resists being turned, and both make the part vibrate more slowly.

This matters more than its size suggests, because the error only ever runs
one way — the reported frequency is too high — and a search converges onto
a constraint boundary by construction. So every design it returns sits
exactly where that error bites. Measured on `examples/drone_arm`, a 35 g
motor 28 mm across and 32 mm tall reads **169.8 Hz flat and 165.5 Hz where
it really sits**, across the 170 Hz limit that example holds the arm to.

The same 100 x 10 x 5 mm steel cantilever, carrying 0.2 kg on its free end,
now with a shape and a height. Two freedoms at the tip — how far it moves
and how far it tilts — with the standard cantilever flexibility:

    w = F L^3/3EI + M L^2/2EI
    t = F L^2/2EI + M L  /EI

An item whose middle sits `e` beyond the tip and resists turning by `J`
about its own middle moves by `w + e t`, so it weighs
`[[m, m e], [m e, m e^2 + J]]` in those two freedoms. The lowest frequency
is the largest eigenvalue of flexibility times mass.

| Carried item | OpenOptima | Closed form | Difference |
|---|---|---|---|
| No size (V15 again) | 89.3320 Hz | 89.2315 Hz | +0.11% |
| Cylinder 4 across, 10 tall | 83.2489 Hz | 83.1035 Hz | +0.17% |
| Cylinder 4 across, 20 tall | 77.7067 Hz | 77.5513 Hz | +0.20% |
| Cylinder 4 across, 40 tall | 68.1554 Hz | 67.9979 Hz | +0.23% |
| Box 8 x 8 x 20 | 77.6771 Hz | 77.5217 Hz | **+0.20%** |

The residual is the beam's own mass, added as a fixed 0.2235 share at the
tip while the real share moves with the mode shape. It is the only
approximation in the comparison, which is why every row sits about 0.2%
above rather than scattering.

**A published cross-check needing no solver.** A slender item standing on
the face resists turning *about that face* by exactly `m h^2 / 3` — the
textbook value for a uniform rod about its end. That figure is the sum of
two separate pieces of code, the shift of the middle away from the face and
the item's own resistance about its middle, so an error in either fails it.

**What this does not cover.** The item is treated as a uniform solid, and a
real motor is not. Assuming uniform puts its middle half way up, which is
**not guaranteed to be on the safe side**: a motor carrying a propeller on
top has its weight higher than that, and the true frequency is lower than
the reported one. `centre_height_mm` exists for when that is known.

### V17 — How thin the thinnest wall is

`tests/verification/test_wall_thickness.py`

A printer lays plastic in beads of a fixed width. A wall thinner than about
two of them either does not print or prints as one unfused line, and no
stress calculation notices — the shape is perfectly sound on paper.

Checked against boxes, fins and tubes whose wall thickness is known by
construction, never against an earlier run of the same code.

**Flat walls are exact and do not depend on the triangles.** A block
carrying a 0.6 mm fin, tessellated at 10, 5, 3, 1.5 and 0.8 mm — 576 to
24 008 triangles — read 0.6000 every time. That is what separates a real
measurement from one that is really about the mesh.

**Curved walls read low, by an amount the triangle size sets.** A flat
facet cuts the corner off a curve, so the chord across is shorter than the
wall. Measured on walls of 0.6, 1.2 and 2.0 mm wrapped round small radii:

| Triangle size | Error |
|---|---|
| 5 x the wall | −16% to −34% |
| 2 x the wall | −8% to −11% |
| **1 x the wall** | **−1.7% to −3.0%** |
| 0.5 x the wall | −0.6% to −0.8% |

Every one **low**, which is the safe direction: a coarse measurement
over-rejects rather than over-accepts. The tessellation is tied to the
`min_wall_check_mm` the project declares, putting it on the 1x row.

**Two method choices, both settled by measurement rather than by
argument.** `trimesh` also offers a largest-inscribed-sphere method, which
reads a third low on a plate of known thickness — 0.5333 for 0.8 mm, and
the same 33% at 2 mm and 5 mm, where the ray reads 0.8000, 2.0000 and
5.0000 exactly. And sampling at triangle corners, which sit on the true
surface rather than inside a curve, looks like the obvious improvement and
is worse: 0.8567 against 0.9106 on a 1.000 mm tube wall, because a corner's
direction is averaged from the faces around it and points off the true
normal.

**What it does not find.** The thin end of a taper. The thinnest point of
anything that tapers is its edge, where the thickness is zero by
definition. Measured on a plate running from 6 mm down to 0.5 mm it reads
0.5502, about 10% **high** — the unsafe direction. Reporting zero for every
chamfer on every part would make the number useless, so this measures walls,
and a wall is a run of material of roughly even thickness.

### V8 — NAFEMS benchmarks

NAFEMS publishes standard linear-elastic benchmarks, with agreed reference
values. LE1 (elliptic membrane) and LE10 (thick plate under pressure) are
the usual starting points. Because these values come from an external
source, they are stronger evidence than a comparison against our own
derivation.

*(V9 is implemented — see above.)*

## Software regression tests

These tests are distinct from verification. They pin down behaviour that
has broken before:

| Guard | Test |
|---|---|
| Consistent nodal loads sum to the applied total | `tests/unit/test_loads.py` |
| Quadratic face corners carry zero load | `tests/unit/test_loads.py` |
| FRD columns split correctly when values touch | `tests/unit/test_frd_parser.py` |
| Element order is what was requested | `tests/integration/test_pipeline.py` |
| Normals point out of the solid | `tests/integration/test_geometry_and_regions.py` |
| Cylinder radius correct on a partial (fillet) surface | `tests/integration/test_geometry_and_regions.py` |
| Selectors survive the full design range | `tests/integration/test_geometry_and_regions.py` |
| Ambiguous selectors stop rather than guess | `tests/integration/test_geometry_and_regions.py` |
| Cache invalidates when physics changes | `tests/integration/test_pipeline.py` |
| Infeasible ≠ error, throughout | `tests/unit/test_failures_and_schema.py` |
| Negative buckling eigenvalues are not failures | `tests/unit/test_buckling.py` |
| Buckle-step reactions excluded from the equilibrium check | `tests/unit/test_buckling.py` |
| Untrustworthy buckling refused, not reported | `tests/unit/test_buckling.py` |
| A runaway quantity is never reported as converging | `tests/unit/test_convergence_maths.py` |
| Convergence levels never share a cache identity | `tests/unit/test_convergence_study.py` |
| An infeasible design is still a convergence data point | `tests/unit/test_convergence_study.py` |
| A cached result keeps its mesh summary and load cases | `tests/unit/test_result_store_roundtrip.py` |
| Reactions are summed per direction, not across free ones | `tests/unit/test_reaction_assembly.py` |
| Load cases are enveloped, never averaged | `tests/unit/test_load_case_envelope.py` |
| Strain energy equals the work the load did (Clapeyron) | `tests/verification/test_strain_energy.py` |
| A gentler extra load case cannot improve a result | `tests/unit/test_load_case_envelope.py` |
| Buckle-step loads are scaled and scaled back exactly | `tests/unit/test_buckling_load_scale.py` |

## Running

```bash
pytest tests/unit                    # fast, no CAE tools required
pytest tests/integration -m gmsh     # needs gmsh
pytest tests/verification            # needs gmsh and CalculiX
```

## Environment of record

This toolchain produced the measured values above:

| | |
|---|---|
| gmsh | 4.15.2 |
| CalculiX | 2.21 |
| Python | 3.11 |
| Platform | Linux x86-64 |

A verification result is only meaningful when you know the toolchain that
produced it. Every run directory records these tool versions in its
manifest.
