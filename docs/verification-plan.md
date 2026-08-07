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

## Planned benchmarks

This list is ordered by value. Each benchmark needs a documented source
and tolerance before anyone writes it.

### V4 — Plate with a central hole (stress concentration)

The `plate_with_hole` template already exists for this benchmark. Compare
the peak stress against the finite-width Howland correction, at Kt ≈ 3.
Expect slow convergence: this is the case that demonstrates why raw peak
stress is a poor optimisation target. Run this benchmark at several mesh
densities, and record how the answer converges.

### V5 — Thick cylinder under internal pressure

This benchmark uses the Lamé solution. It exercises the pressure load
path (`*DLOAD` element faces), which V1 does not touch. This benchmark has
high value, because pressure loading is currently verified only by unit
tests of the face lookup.

### V7 — Multiple load cases

Confirm that OpenOptima envelopes load cases, rather than averaging them.
Also confirm that per-case metrics match single-case runs of the same
loading.

### V8 — NAFEMS benchmarks

NAFEMS publishes standard linear-elastic benchmarks, with agreed reference
values. LE1 (elliptic membrane) and LE10 (thick plate under pressure) are
the usual starting points. Because these values come from an external
source, they are stronger evidence than a comparison against our own
derivation.

### V9 — Buckling validity boundary

Sweep section size against slenderness. Find exactly where CalculiX's
solid-element buckling stops being reliable. This would let
`slenderness_limit` rest on a measured boundary, instead of the handful of
points behind the current default.

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
