# Third-party licences

OpenOptima is distributed under GPL-3.0-or-later. It builds on the projects
below, each of which remains under its own licence. Nothing here is legal
advice; if you plan to redistribute a bundled build, get the packaging reviewed.

## Required at runtime

| Project | Licence | How OpenOptima uses it |
|---|---|---|
| [gmsh](https://gmsh.info/) | GPL-2.0-or-later | Imported as a Python module for CAD geometry (OpenCASCADE kernel) and meshing |
| [CalculiX](http://www.calculix.de/) (`ccx`) | GPL-2.0-or-later | Executed as a separate process; communication is via files and a command line |
| [NumPy](https://numpy.org/) | BSD-3-Clause | Arrays and linear algebra |
| [SciPy](https://scipy.org/) | BSD-3-Clause | Sobol/Latin hypercube sampling, statistics |
| [pydantic](https://docs.pydantic.dev/) | MIT | Project file validation |
| [PyYAML](https://pyyaml.org/) | MIT | Project file parsing |

## Optional

| Project | Licence | Extra |
|---|---|---|
| [pymoo](https://pymoo.org/) | Apache-2.0 | `[optimise]` — NSGA-II |
| [CadQuery](https://cadquery.readthedocs.io/) | Apache-2.0 | `[cadquery]` — alternative geometry provider |
| [trimesh](https://github.com/mikedh/trimesh) | MIT | `[printing]`, `[topology]` — overhang, build-volume fit, wall thickness, and sealing a topology result |
| [rtree](https://github.com/Toblerity/rtree) | MIT | `[printing]` — every spatial query in trimesh routes through it, including the ray engine the wall check uses |
| [libspatialindex](https://libspatialindex.org/) | MIT | Ships inside the rtree wheel |
| [pyLife](https://github.com/boschresearch/pylife) | Apache-2.0 | `[fatigue]` — equivalent stress for a load cycle, from Bosch Research |
| [pandas](https://pandas.pydata.org/) | BSD-3-Clause | Pulled in by pyLife |
| [h5py](https://www.h5py.org/) | BSD-3-Clause | Pulled in by pyLife |
| [Matplotlib](https://matplotlib.org/) | PSF-based (Matplotlib licence) | `[topology]` — not our choice; beso imports it at module scope |
| [beso](https://github.com/calculix/beso) | GPL-3.0 | Fetched at a pinned commit at run time, never redistributed. See `openoptima.topology.fetch` |
| [OpenCASCADE](https://dev.opencascade.org/) | LGPL-2.1 with exception | Ships inside the gmsh and CadQuery wheels |

Licences above are read from each package's own metadata rather than from a
README badge. That is not pedantry: one surrogate-optimisation library
advertises Apache-2.0 and ships PolyForm Noncommercial, and depending on it
would have made this project non-commercial. See `docs/capability-audit.md`.

## Why GPL-3.0-or-later

gmsh and CalculiX are both GPL-2.0-**or-later**, and OpenOptima depends on gmsh
as a linked Python module. GPL-3.0-or-later is compatible with "or later"
GPL-2.0 code and is the least ambiguous choice for a project in this position.

A permissive licence would not be honest about the gmsh dependency. A
"non-commercial use only" restriction would make the project source-available
rather than open source under the OSI definition, so it is not used.

## Distribution notes

- CalculiX runs as a **separate process**. OpenOptima does not link it, and
  communicates only through files and a command line.
- gmsh is **imported as a library**, which is the stronger coupling and the main
  driver of the licence choice.
- **OpenOptima does not redistribute CalculiX.** The desktop app can offer to
  download it, but the file comes from the CalculiX project's own repository
  straight to the user's machine, exactly as it would if they fetched it by
  hand. The download keeps CalculiX's `LICENSE.txt` alongside the program. See
  `app/solver_setup.py` and `packaging/README.md`.
- If you bundle binaries into an installer, you must also make the corresponding
  source available under the terms of each licence — for that exact build,
  including any packager patches, published where the binary is published and
  kept there for as long as the binary is.
- Keep this file, `LICENSE`, and the upstream copyright notices intact in any
  redistribution.
