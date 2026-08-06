# OpenOptima

Open-source parametric design optimisation. Define a part, say what it must
survive and what you want to minimise, and get back the trade-off surface —
built entirely on open-source CAD, meshing and finite element tools.

```
              ┌─────────────────────────────────────────┐
   parameters │  geometry → mesh → FEA → metrics        │  Pareto front
   ──────────►│         ▲                    │          ├──────────────►
              │         └──── optimiser ◄────┘          │  + knee point
              └─────────────────────────────────────────┘  + your preferences
```

> **New here?** Start with the
> [plain-English guide](docs/plain-english-guide.md) — the whole project
> explained without jargon, including what every term means and how to read the
> results.

> **Status: alpha.** The pipeline is verified against beam theory and a full
> optimisation runs end to end, but this is early software. Read
> [`docs/engineering-assumptions.md`](docs/engineering-assumptions.md) before
> sizing anything real from its output. It performs **linear static analysis
> only** — no buckling, fatigue, contact or plasticity.

## What it does

- **Parametric geometry** from built-in templates or your own CadQuery model
- **Automatic meshing** with size fields, quality gates and a retry ladder
- **Finite element analysis** via CalculiX, with automatic equilibrium checking
- **Design of experiments** (Sobol / Latin hypercube) with sensitivity ranking
- **Multi-objective optimisation** (NSGA-II) producing a real Pareto front
- **Decision support** — knee point, marginal exchange rates, and a preference
  model that understands "I'll pay 25 g for each 0.1 of factor of safety"
- **Full provenance** — every run keeps its geometry, mesh, deck, solver output
  and a manifest of tool versions

## Install

Two runtime dependencies: a Python wheel and a solver binary.

```bash
# CalculiX
sudo apt install calculix-ccx          # Debian/Ubuntu
brew install calculix                  # macOS

# OpenOptima
pip install -e ".[optimise]"
```

`gmsh` comes from PyPI and bundles its own OpenCASCADE kernel — no separate CAD
install. CadQuery is optional (`pip install -e ".[cadquery]"`) and only needed
if you want to author geometry with its API instead of a built-in template.

Verify:

```bash
openoptima doctor examples/l_bracket/project.yaml
```

## Try it

```bash
openoptima doctor   examples/l_bracket/project.yaml   # check the setup first
openoptima evaluate examples/l_bracket/project.yaml   # one design
openoptima doe      examples/l_bracket/project.yaml --evaluations 24
openoptima optimise examples/l_bracket/project.yaml
```

The example minimises the mass of an aluminium bracket carrying a 2.5 kN end
load, subject to a factor of safety of 2 and a 1 mm deflection limit.

## Defining a study

A project is one YAML file. The interesting part is how regions are defined:

```yaml
regions:
  - name: mounting_face
    selector:
      surface_type: plane
      normal: [-1.0, 0.0, 0.0]
      prefer_largest: true
```

That is not a face number. Face numbering changes whenever the model is rebuilt,
so a stored index eventually attaches your load to the wrong face and returns a
converged, plausible, wrong answer. OpenOptima re-resolves selectors against the
real geometry on **every** evaluation, and if two faces match equally well it
**stops** rather than guessing.

`openoptima doctor` builds the extremes of your design range and checks every
selector still resolves uniquely there — so you find setup mistakes before a
study, not 200 evaluations into one.

## Saying what you actually want

The hard question in optimisation is not finding good designs, it is choosing
between them. A weighted score (`0.6 × strength + 0.4 × weight`) hides the
trade-off, changes meaning when units change, and cannot reach concave parts of
the front.

So OpenOptima always produces the front, then lets you rank it four ways:

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

and it shows you what each step along the front actually costs:

| Mass paid | Deflection gained | Cost per unit |
|-----------|-------------------|---------------|
| 0.0159 kg | 0.0323 mm         | 0.49          |
| 0.1479 kg | 0.0189 mm         | 7.82          |

The first step is cheap, the second is sixteen times worse value. That is the
knee, and it is the sort of thing a single score would have buried.

## How it is verified

The cantilever benchmark compares a full geometry→mesh→solve→parse run against
Timoshenko beam theory:

```
tip deflection, FE      : -2.8519 mm
beam theory (EB+shear)  : -2.8799 mm
error                   : -0.98 %      (3% tolerance)
reaction force          :  1000.000 N  (exactly balances the applied load)
```

The model must land on the *stiff* side — a fully fixed end suppresses Poisson
contraction — so the test asserts the sign of the discrepancy as well as its
size. Every load case in every run also checks reaction against applied load,
which catches load-on-the-wrong-face and unit mistakes for free.

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

## Built with

[gmsh](https://gmsh.info/) · [CalculiX](http://www.calculix.de/) ·
[pymoo](https://pymoo.org/) · [SciPy](https://scipy.org/) ·
[CadQuery](https://cadquery.readthedocs.io/) (optional)

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

GPL was chosen for compatibility with gmsh (GPL-2.0-or-later) and CalculiX
(GPL-2.0-or-later), which OpenOptima builds on. This is a practical assessment,
not legal advice.
