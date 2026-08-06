# Verification plan

Verification asks a different question from testing. Testing asks "does the
software do what it was told?"; verification asks "does what it was told produce
physics?". Both are needed; only the second gives anyone a reason to believe a
number.

## Rule

**A verification tolerance is never widened to make a build pass.** If a
verification test fails, either the code regressed or the physics changed. Both
require a human to look. An agent that adjusts one of these numbers to get green
has destroyed the only evidence that the software computes correctly.

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

The finite element result must be slightly **stiffer** than beam theory, because
fully fixing the end face suppresses the Poisson contraction beam theory allows.
A result on the *soft* side indicates a real problem — wrong element order, wrong
constraint, or a load that is not doing what it should — not merely a coarse
mesh. The test asserts the sign of the discrepancy as well as its size.

Also asserted in the same case:

- reaction force equals the applied load to 1 part in 10⁴ (global equilibrium —
  this is exact arithmetic, not an approximation, so the tolerance is tight);
- no spurious transverse reaction;
- peak von Mises consistent with the 150 MPa nominal bending stress;
- mesh volume matches CAD volume;
- the mesh is genuinely C3D10 — if the mesher silently fell back to first order
  the deflection comparison would be meaningless, so this is asserted explicitly.

### V2 — Analytic reference guard

The formulas in the test file are themselves checked against hard-coded values,
so a typo in the reference cannot silently move the goalposts.

## Planned benchmarks

Ordered by value. Each needs a documented source and tolerance before it is
written.

### V3 — Plate with a central hole (stress concentration)

The `plate_with_hole` template exists for this. Compare the peak stress against
the finite-width Howland correction to Kt ≈ 3. Expect slow convergence — this is
the case that demonstrates why raw peak stress is a poor optimisation target,
and it should be run at several mesh densities with the convergence recorded.

### V4 — Thick cylinder under internal pressure

Lamé solution. Exercises the pressure load path (`*DLOAD` element faces), which
V1 does not touch. High value: pressure loading is currently verified only by
unit tests of the face lookup.

### V5 — Mesh convergence study

Not a comparison against theory but against itself: run one design at 4–5 mesh
densities and record how displacement, strain energy, reaction and the various
stress measures converge. This produces the evidence for the guidance in
`engineering-assumptions.md` and gives users a defensible default mesh size.

### V6 — Multiple load cases

Confirm cases are enveloped rather than averaged, and that per-case metrics
match single-case runs of the same loading.

### V7 — NAFEMS benchmarks

NAFEMS publishes standard linear-elastic benchmarks with agreed reference
values. LE1 (elliptic membrane) and LE10 (thick plate under pressure) are the
usual starting points, and being externally defined they are stronger evidence
than self-derived comparisons.

### V8 — Buckling

Euler column, once linear buckling is supported. Until then, the omission is
documented in `engineering-assumptions.md` as the most likely way a minimum-mass
result is unsafe.

## Software regression tests

Distinct from verification. These pin behaviour that has broken before:

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

## Running

```bash
pytest tests/unit                    # fast, no CAE tools required
pytest tests/integration -m gmsh     # needs gmsh
pytest tests/verification            # needs gmsh and CalculiX
```

## Environment of record

The measured values above were produced with:

| | |
|---|---|
| gmsh | 4.15.2 |
| CalculiX | 2.21 |
| Python | 3.11 |
| Platform | Linux x86-64 |

Verification results are only meaningful against a stated toolchain. Every run
directory records these versions in its manifest.
