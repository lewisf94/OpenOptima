# OpenOptima

OpenOptima is open-source software for parametric design optimisation —
trying many versions of a part's shape and keeping the best ones. You
describe the part, say what it must survive, and say what you want to
minimise. OpenOptima returns the trade-off between those goals. It uses only
open-source tools for the shape, the mesh, and the structural calculation.

```
              ┌─────────────────────────────────────────┐
   parameters │  geometry → mesh → FEA → metrics        │  Pareto front
   ──────────►│         ▲                    │          ├──────────────►
              │         └──── optimiser ◄────┘          │  + knee point
              └─────────────────────────────────────────┘  + your preferences
```

> **New to OpenOptima?** Read the
> [plain-English guide](docs/plain-english-guide.md) first. It explains the
> whole project without jargon: what every term means, and how to read your
> results.

> **Status: alpha.** This is early software. The calculation pipeline is
> verified against beam theory, and a full optimisation run works end to
> end. Read [`docs/engineering-assumptions.md`](docs/engineering-assumptions.md)
> before you size a real part from its output. OpenOptima performs
> **linear static analysis only**, with optional linear buckling. It does
> not cover fatigue, contact, or plasticity.

## What it does

- **Parametric geometry**, from a built-in template or your own CadQuery model
- **Automatic meshing**, with quality checks and an automatic coarser retry
  when meshing fails
- **Structural calculation (FEA)** using CalculiX, with an automatic check
  that the reaction force matches the applied load
- **Linear buckling** as a limit you can set, verified to within 0.11% of
  Euler's column formula. OpenOptima refuses to report a buckling result it
  cannot trust, instead of an optimistic one
- **Design of experiments (DOE)**, using Sobol or Latin hypercube sampling,
  with sensitivity ranking
- **Multi-objective optimisation** (NSGA-II), which produces a real Pareto
  front — not a single blended score
- **Decision support** — the knee point, marginal exchange rates, and a
  preference model that understands "I will pay 25 g for each 0.1 of factor
  of safety"
- **A full record of every run** (its geometry, mesh, solver deck, solver
  output, and every tool version used) — engineers call this provenance

## Install

### Windows — the app

To install:

1. Download the installer.
2. Run the installer.
3. Open OpenOptima from the Start menu.

OpenOptima opens in your browser as a step-by-step app. Inside the app:

1. Choose a part.
2. Check the setup.
3. Run it.
4. Read the results.

You do not need a terminal or a Python installation.

To build the installer yourself, run `packaging\build_windows.ps1`. See
[`packaging/README.md`](packaging/README.md) for the steps.

### Everything else — pip

OpenOptima needs two runtime components: a Python package and a solver
program.

```bash
# CalculiX
sudo apt install calculix-ccx          # Debian/Ubuntu
brew install calculix                  # macOS

# OpenOptima
pip install -e ".[optimise]"
```

Gmsh comes from PyPI and includes its own OpenCASCADE geometry engine, so
you do not need a separate CAD installation. CadQuery is optional
(`pip install -e ".[cadquery]"`). Install it only if you want to write
geometry in CadQuery's own code, instead of using a built-in template.

Check the install:

```bash
openoptima doctor examples/l_bracket/project.yaml
```

## Try it

If you prefer a visual app to typed commands, run:

```bash
openoptima-app
```

If you prefer the command line, run these commands in order:

```bash
openoptima doctor   examples/l_bracket/project.yaml   # check the setup first
openoptima evaluate examples/l_bracket/project.yaml   # one design
openoptima doe      examples/l_bracket/project.yaml --evaluations 24
openoptima optimise examples/l_bracket/project.yaml
```

This example minimises the mass of an aluminium bracket. The bracket
carries a 2.5 kN load at its end. The design must keep a factor of safety
of at least 2, and a deflection under 1 mm.

## Defining a study

A project is one YAML file. This is how you define a region — a named
face, or group of faces, that you push or hold:

```yaml
regions:
  - name: mounting_face
    selector:
      surface_type: plane
      normal: [-1.0, 0.0, 0.0]
      prefer_largest: true
```

That is not a face number. Face numbering changes every time OpenOptima
rebuilds the model. A stored face number can end up pointing at the wrong
face. Your load then lands in the wrong place, and OpenOptima still reports
an answer that looks correct and is wrong.

Instead, OpenOptima finds each region again from your written description —
its selector — every time it evaluates a design. If two faces match equally
well, OpenOptima stops instead of guessing.

`openoptima doctor` builds your part at the smallest and largest sizes in
your design range. It checks that every selector still finds exactly one
face at each size. This finds a setup mistake in seconds, before you start
a study — not 200 designs into one.

## Saying what you actually want

The hard question in optimisation is not finding good designs. It is
choosing between them. A weighted score, for example
`0.6 × strength + 0.4 × weight`, hides the trade-off between the two goals.
Its result also changes meaning whenever your units change. And it cannot
reach some good designs at all, no matter how you set the weights.

So OpenOptima always produces the full set of trade-offs — the Pareto front
— first. Then it lets you rank the designs on that front in four ways:

```yaml
preferences:
  desirability:
    - metric: factor_of_safety
      direction: maximise
      ideal: 3.0        # above this, extra strength buys nothing
      acceptable: 2.0   # below this, unacceptable at any weight
  trade_rules:
    - give_metric: mass_kg          # "I'll pay 25 g
      give_amount: 0.025
      gain_metric: factor_of_safety #  for each 0.1 of
      gain_amount: 0.1              #  factor of safety"
```

OpenOptima also shows you what each step along the front actually costs:

| Mass paid | Deflection gained | Cost per unit |
|-----------|-------------------|---------------|
| 0.0159 kg | 0.0323 mm         | 0.49          |
| 0.1479 kg | 0.0189 mm         | 7.82          |

The first step is cheap. The second step is sixteen times worse value.
That point is called the knee point — where extra strength stops being
worth its weight. A single blended score would have hidden this pattern.

## How it is verified

The cantilever benchmark checks a full run — shape, mesh, solve, and read
the result — against Timoshenko beam theory. A cantilever is a beam fixed
at one end and free at the other, like a diving board:

```
tip deflection, FE      : -2.8519 mm
beam theory (EB+shear)  : -2.8799 mm
error                   : -0.98 %      (3% tolerance)
reaction force          :  1000.000 N  (exactly balances the applied load)
```

The finite-element answer must be slightly stiffer than beam theory
predicts: a smaller deflection, never a larger one. A fully fixed end stops
the material narrowing sideways as it stretches, an effect called Poisson
contraction. Beam theory ignores this effect, so the finite-element model
comes out a little stiffer. The test checks two things: the size of the
error, and its direction. A verified model may be a little too stiff. It
must never be too soft.

Every load case — one loading scenario your part must survive — is checked
the same way. OpenOptima compares the reaction force to the applied load in
every run. This check finds a load placed on the wrong face, and unit
mistakes, automatically.

Buckling is verified the same way, against Euler's column formula:

```
buckling factor, FE     : 14.4086
Euler, fixed-free       : 14.3932
error                   : +0.11 %      (3% tolerance)
```

Buckling is also where OpenOptima sometimes refuses to answer, on purpose.
For very slender parts, solid elements stop giving a reliable buckling
number. The failure is optimistic: the calculation can report a strut as
safe when it will actually fold. So OpenOptima checks every buckling result
against beam theory, calculated from the same mesh. A result outside the
range OpenOptima has verified is reported as an error, not as a number with
a warning attached. An automatic optimiser reads the number, and ignores a
warning next to it.

See [`docs/verification-plan.md`](docs/verification-plan.md).

## Documentation

| | |
|---|---|
| [**Plain-English guide**](docs/plain-english-guide.md) | **start here** — no jargon, explains every term |
| [Architecture](docs/architecture.md) | how the pieces fit and why |
| [Engineering assumptions](docs/engineering-assumptions.md) | **read before trusting a number** |
| [Verification plan](docs/verification-plan.md) | benchmarks and tolerances |
| [Roadmap](docs/roadmap.md) | what is next |
| [ADRs](docs/adr/) | decisions and the reasoning behind them |
| [AGENTS.md](AGENTS.md) | contributing, including with coding agents |
| [Technical writing standard](docs/technical-writing-standard.md) | the language rules all project text follows |

## Built with

[Gmsh](https://gmsh.info/) · [CalculiX](http://www.calculix.de/) ·
[pymoo](https://pymoo.org/) · [SciPy](https://scipy.org/) ·
[CadQuery](https://cadquery.readthedocs.io/) (optional)

## Licence

OpenOptima is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

OpenOptima builds on Gmsh (GPL-2.0-or-later) and CalculiX
(GPL-2.0-or-later). We chose GPL-3.0-or-later for compatibility with both.
This is a practical assessment, not legal advice.
