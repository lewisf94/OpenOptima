# CalculiX adapter

CalculiX is unitless. Everything reaching it is already in `mm, N, MPa, t`.

## Element ordering

gmsh's tet10 and CalculiX's C3D10 differ in the last two midside nodes:
gmsh lists edge (2,3) before (1,3), CalculiX expects the reverse. The
permutation lives in `meshing/gmsh_mesher.py` as `_TET10_TO_CCX`. Getting it
wrong does not crash — it produces a distorted stiffness and slightly wrong
answers.

## Loads

- **Pressure** becomes `*DLOAD` with an element face id, so CalculiX does the
  surface integration in the element's own curved geometry with the correct
  direction.
- **A directional force** becomes `*CLOAD` with a *consistent* nodal load
  vector. Do not lump it evenly: the exact integral of a corner shape function
  over a flat 6-node triangle is zero, so all the load belongs on the midside
  nodes. Even lumping puts spurious force exactly where peak stress is read.

`tests/unit/test_loads.py` asserts both the total and the zero-at-corners
property.

## Equilibrium

Every load case compares the applied load against the reaction total from the
`.dat` file. This is a free global check that catches load-on-the-wrong-face,
missing-constraint and unit mistakes that otherwise pass silently. Do not remove
it, and do not downgrade it below a warning.

## Parsing

FRD is fixed-width FORTRAN output. Slice by column. CalculiX writes
`-1.23456E+05-9.87654E+04` with no separator when a value fills its field, and
`str.split()` merges those into one number.

## Process safety

Argument lists, never `shell=True`. Every run has a timeout and its process
group is killed on expiry. A non-zero exit, a missing result file and a
convergence failure are three different failure codes because only some are
worth retrying.
