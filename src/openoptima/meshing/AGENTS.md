# Meshing

gmsh keeps process-global C state. Every entry point goes through
`geometry.gmsh_session`; never call `gmsh.initialize()` directly, and never
drive gmsh from two threads.

## Order of operations is load-bearing

```
generate(3)  ->  optimize("Netgen")  ->  setOrder(2)
```

Netgen optimisation only understands linear elements. Running it *after*
`setOrder(2)` silently drops the mesh back to first order and leaves the midside
nodes orphaned — a mesh that still passes quality checks but whose loaded nodes
belong to no element, giving zero displacement. This shipped as a bug once and
is now guarded by an explicit element-type assertion in `_extract`.

## Invariants

- The element type produced must match the requested order. Assert, do not hope.
- Every node written to a solver deck must be referenced by an element.
- Every surface node must belong to the volume mesh.
- Mesh volume must agree with CAD volume within `volume_tolerance`; disagreement
  means the mesh is not representing the geometry.

## The retry ladder

Change one thing per rung so a failure report identifies what rescued the mesh.
The final rung produces a first-order *diagnostic* mesh: it is always flagged
with a warning and must never be presented as an ordinary result.

Region resolution failures (`REGION_NOT_FOUND`, `REGION_AMBIGUOUS`) are project
setup problems. They are re-raised immediately rather than retried — no amount
of coarsening fixes an ambiguous selector.
