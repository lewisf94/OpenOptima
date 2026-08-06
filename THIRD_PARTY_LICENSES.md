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
| [OpenCASCADE](https://dev.opencascade.org/) | LGPL-2.1 with exception | Ships inside the gmsh and CadQuery wheels |

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
- If you bundle binaries into an installer, you must also make the corresponding
  source available under the terms of each licence.
- Keep this file, `LICENSE`, and the upstream copyright notices intact in any
  redistribution.
