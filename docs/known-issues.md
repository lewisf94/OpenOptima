# Known issues

## L-bracket and compression strut: implausible stress and displacement — FIXED

**Status:** root cause found and fixed. The FRD parser in
`solvers/calculix/frd.py` misread node values whenever a negative number
followed a positive one on the same line, on any CalculiX build that writes a
3-digit exponent (`E-003`, the Windows Fortran convention) rather than 2
(`E-03`, the Linux convention this project's fixed-width slicing was written
against). The extra exponent digit shifted every later fixed-width field on
the line, and the shifted numbers were large enough to look like an alarming
but plausible result rather than obvious garbage — the strut's `factor_of_safety`
at its smallest section came out 0.75 instead of the correct 4.77, and its
`displacement_max_mm` came out 82 mm instead of 0.29 mm.

The fix replaces the fixed-width column slicing with a pattern that finds
each number by its own shape (a sign, one digit, a decimal point, then an
exponent) rather than assuming how wide the field is. It works for either
exponent width and needs no per-platform configuration. Regression test:
`tests/unit/test_frd_parser.py::test_three_digit_exponent_does_not_corrupt_adjacent_values`.

Re-run after the fix, against the example's own documented expectation
(a factor of safety of "4.8" at the lightest section, in
`examples/strut/project.yaml`'s header comment):

| Design | Metric | Before the fix | After the fix |
|---|---|---|---|
| Strut, 30×30 mm section | `factor_of_safety` | 0.75 | **4.77** |
| Strut, 30×30 mm section | `displacement_max_mm` | 81.9 | **0.285** (hand calc: 0.286) |
| Strut, 30×30 mm section | `stress_max_mpa` | 213.7 | **33.5** (nominal: 33.3) |
| L-bracket, default thicknesses | `displacement_max_mm` | 93.9 | **3.15** |
| L-bracket, max thicknesses (20/20/20) | outcome | infeasible | **feasible**, FoS 2.70 |

Buckling factors were unaffected — they come from `dat.py`, a separate code
path — and all 83 solver-backed verification tests and 34 integration tests
pass with the fix.

### Resolved: the L-bracket's default design was genuinely infeasible

With the parsing bug fixed, the L-bracket's old default thicknesses
(10/10/8 mm) failed the 1 mm tip-deflection limit for a physically plausible
reason (3.15 mm, not the old 93.9 mm) — the default point had been chosen
before the parsing bug was found, using numbers that were themselves wrong.
The default is now 19/19/17 mm (`examples/l_bracket/project.yaml`), which
evaluates as feasible with some margin (0.37 mm deflection, factor of safety
2.30) without sitting at the top of the allowed range, so the optimiser still
has room to explore both lighter and stronger designs. The minimum/maximum
range was left unchanged.

## Windows installer: CalculiX is a manual prerequisite

**Status:** packaging gap.

The Windows app can start with Gmsh available, but it cannot run an analysis
until a separate CalculiX `ccx.exe` installation is found or the user sets
`OPENOPTIMA_CCX`.  This led to a blocking "CalculiX not found" error on first
use.  The installer should either ship a licence-compliant CalculiX runtime
alongside OpenOptima, or provide an explicit first-run setup flow that installs
or locates it and verifies the solver before enabling a study.

Any bundled solver must retain its licence and notices and satisfy the GPL
source-availability obligations for the version redistributed.
