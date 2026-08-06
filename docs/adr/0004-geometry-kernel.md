# 4. gmsh's OpenCASCADE kernel is the default geometry provider

**Status:** accepted

## Context

Parametric geometry needs a CAD kernel. The obvious candidates were FreeCAD
(driven headless via `FreeCADCmd`), CadQuery, and gmsh's built-in OpenCASCADE
bindings.

## Decision

The built-in `occ` provider uses gmsh's OpenCASCADE kernel. CadQuery is
supported as an optional provider behind the same protocol. FreeCAD is not on
the critical path.

## Rationale

- **No extra dependency.** gmsh is already required for meshing, and its wheel
  ships the OCC kernel. The runtime is two things: a pip wheel and a solver
  binary.
- **One kernel, no round-trip.** Geometry and meshing share a kernel, so there
  is no STEP round-trip inside the optimisation loop where tolerance differences
  could quietly change a model.
- **Testable by an agent.** A GUI workbench is very hard for a coding agent to
  drive and verify; a Python API is trivial.
- **Avoids inheriting a naming problem.** Driving FreeCAD would mean depending
  on its topological naming behaviour — precisely the problem ADR 3 exists to
  sidestep.
- **Verified.** Booleans, fillets and STEP/BREP export were tested on the
  L-bracket before this was adopted.

## Alternatives rejected

- **FreeCAD as the geometry worker and first UI** — was the original plan.
  Rejected on the reasoning above; it also could not be installed from the
  package manager on the target platform, which made CI impossible.
- **CadQuery as the default** — nicer authoring API, but its OCP wheel is ~68 MB
  and would be mandatory for every user and every CI run. Available as an extra
  for users who want it.

## Consequences

Built-in templates are written against a lower-level API than CadQuery's fluent
one. Users authoring their own complex parts may prefer the CadQuery provider,
which is why it exists. If gmsh's OCC bindings ever prove limiting, the
`GeometryProvider` protocol is the seam to replace behind.
