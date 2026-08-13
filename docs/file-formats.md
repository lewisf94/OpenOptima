# File formats

## Project file (`project.yaml`)

`project.yaml` is the only file a user writes. OpenOptima versions it with
`schema_version`, and rejects unknown keys. This way, a typo cannot
silently fall back to a default value and change the physics. See
`examples/l_bracket/project.yaml` for a commented example.

Top-level sections:

| Section | Purpose |
|---|---|
| `geometry` | provider, template, fixed parameters, design `variables` |
| `regions` | named selectors — never face indices |
| `material` | properties plus `allowable_stress_basis`, or a `printed` block for a 3D-printed part |
| `point_masses` | heavy things the part carries but is not made of — a motor, a camera |
| `printing` | overhang and build-volume checks for a 3D-printed part |
| `load_cases` | independent analyses, each with loads and constraints |
| `mesh` | sizes, order, algorithm, local refinements, quality gates |
| `stress_evaluation` | which stress measure drives the objective |
| `objectives` / `constraints` | what to minimise and what must hold |
| `preferences` | hard limits, targets, desirability, trade rules |
| `optimisation` | sampling, algorithm, budget, parallelism |

## Workspace layout

```
openoptima_work/
├── openoptima.sqlite          index of every evaluation
├── runs/
│   ├── 000001/
│   │   ├── evaluation_manifest.json
│   │   ├── geometry/   model.brep, model.step
│   │   ├── mesh/       mesh.msh, regions.json
│   │   ├── solver/     job.inp, mesh.inp, job.frd, job.dat, logs
│   │   └── results/    metrics.json
│   └── 000002/ ...
└── reports/
    ├── optimisation.md
    └── optimisation.json
```

`--discard-artifacts` deletes the geometry and mesh after each evaluation,
and keeps the manifests and metrics. Without this flag, a 500-design study
keeps all 500 meshes on disk.

## `evaluation_manifest.json`

This is the **provenance** record — the record of exactly what produced a
result. It holds everything needed to explain a number later:

```json
{
  "run_id": "000031",
  "setup_digest": "4a0423508ce93989",
  "design": {"thickness_h": 17.47, "...": "..."},
  "evaluation_hash": "...",
  "unit_system": "mm_N_MPa_t",
  "versions": {"openoptima": "0.1.0", "gmsh": "4.15.2",
               "calculix": "This is Version 2.21", "python": "3.11.15"},
  "geometry": {"volume_mm3": 119551.7, "...": "..."},
  "regions":  {"mounting_face": {"face_tags": [6], "total_area": 5272.8,
                                  "margin": null, "...": "..."}},
  "mesh":     {"element_type": "C3D10", "min_scaled_jacobian": 0.283, "...": "..."},
  "solver":   {"name": "calculix", "warnings": []},
  "outcome": "ok",
  "metrics":  {"mass_kg": 0.4826, "...": "..."}
}
```

If anyone ever questions a result, this file answers exactly that. It
records which tool versions ran, which regions resolved to which faces
(and with what confidence), which mesh was used, and whether anything
raised a warning.

## Database

OpenOptima uses SQLite, with two tables: `evaluations` (one row per unique
evaluation hash) and `studies`. The database is an **index**, not an
archive — the bulk data stays on disk, under `runs/`.

`evaluation_hash` is unique. It covers the design vector, the project's
setup digest, and the tool versions together. So OpenOptima can never
serve a result computed under different physics as a cache hit.

## Solver deck

OpenOptima splits the solver deck across several files, instead of
writing one single file. This is because the first question when
something looks wrong is always: "is it the mesh, the material, or the
loads?"

| File | Contents |
|---|---|
| `job.inp` | includes, steps, boundary conditions, loads, output requests |
| `mesh.inp` | `*NODE` and `*ELEMENT` |
| `sets.inp` | one `*NSET` per region |
| `material.inp` | `*MATERIAL`, `*ELASTIC`, `*DENSITY`, `*SOLID SECTION` |

OpenOptima writes one `*STEP` per load case, with `OP=NEW` on the loads
and boundary conditions. This keeps each load case independent, instead
of letting effects accumulate across cases.
