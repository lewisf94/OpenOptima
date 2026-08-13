# OpenOptima

OpenOptima is open-source software for parametric design optimisation —
trying many versions of a part's dimensions and keeping the best ones. You
describe the part, say what it must survive, and say what you want to
minimise. OpenOptima returns the trade-off between those goals. It uses
only open-source tools for the shape, the mesh, and the calculation.

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

> **Status: alpha.** This is early software. Its calculations are verified
> against nine published reference solutions, and a full optimisation run
> works end to end. Read
> [`docs/engineering-assumptions.md`](docs/engineering-assumptions.md)
> before you size a real part from its output. OpenOptima performs
> **linear static analysis only**, with optional linear buckling and
> natural frequencies. It does not cover fatigue, contact, or plasticity.

---

## Contents

- [What it does](#what-it-does)
- [What it does not do](#what-it-does-not-do)
- [Install](#install)
- [The eight commands](#the-eight-commands)
- [Defining a study](#defining-a-study)
- [Saying what you actually want](#saying-what-you-actually-want)
- [How it is verified](#how-it-is-verified)
- [Defects the benchmarks found](#defects-the-benchmarks-found)
- [How it is built](#how-it-is-built)
- [Documentation](#documentation)

---

## What it does

### Geometry and meshing

- **Parametric geometry** from a built-in template, or from your own model
  written with CadQuery. Five templates exist today: `cantilever_box`,
  `drone_arm`, `l_bracket`, `plate_with_hole` and `thick_cylinder`.
- **Regions found by shape, never by face number.** You describe a face —
  "the biggest flat face pointing at −X" — and OpenOptima finds it again
  every time it rebuilds the part. See
  [Defining a study](#defining-a-study) for why this matters more than it
  sounds.
- **Automatic meshing**, where the part is chopped into small pieces the
  computer can calculate on. Includes quality checks and an automatic
  retry at a coarser size when meshing fails.

### Analysis

- **Structural calculation** using CalculiX, with an automatic check that
  the reaction force balances the applied load. This check catches a load
  placed on the wrong face, and unit mistakes, for free.
- **Linear buckling** — where a long thin part folds sideways rather than
  being crushed. Verified to within 0.11% of Euler's column formula.
- **Natural frequencies** — the rates a part likes to vibrate at. Drive a
  part at one of them and small pushes build into large movements, which a
  stress check cannot see at all. Verified to within 0.27% of the published
  cantilever answer. OpenOptima refuses to report a frequency for a part
  the supports do not hold still, rather than reporting the zero it gets.
- **Orthotropic material** — material that is stronger in some directions
  than others, which is what a 3D-printed part is. See
  [3D printing](#3d-printing-partly-supported) below.
- **Multiple load cases**, one for each scenario the part must survive.
  OpenOptima always takes the **worst** case and never the average.
  Averaging a failing scenario against a passing one would hide the
  failure.
- **Strain energy**, the work the load does on the part. This is the
  best-behaved measure for checking whether a result has settled.

### Search and decision

- **Design of experiments (DOE)** — a planned spread of trial designs that
  surveys what is possible, using Sobol or Latin hypercube sampling. It
  reports which dimensions actually matter.
- **Multi-objective optimisation** using NSGA-II, which produces a real
  Pareto front: the set of designs where you cannot improve one thing
  without giving up another.
- **Decision support** — the knee point (where extra weight stops buying
  useful strength), the cost of each step along the front, and a
  preference model that understands "I will pay 25 g for each 0.1 of
  factor of safety".

### Trust

- **`openoptima converge`** re-runs one design at several mesh densities
  and reports whether its numbers have stopped changing. This answers the
  question every result depends on.
- **Refusal instead of a wrong number.** Where OpenOptima cannot trust a
  result, it reports an error rather than a number with a warning
  attached. An automatic optimiser reads the number and ignores the
  warning.
- **A full record of every run** — its geometry, mesh, solver input,
  solver output, and every tool version used. Engineers call this
  provenance. It is the only thing that answers "where did this number
  come from?" months later.

---

## What it does not do

Read this section before you trust a number. Every item here is invisible
to OpenOptima, and an optimiser will exploit every blind spot it is given.

### Not modelled at all

| | Why it matters |
|---|---|
| **Fatigue** | Failure after many load cycles, at a stress the part survives easily once. This is often what actually breaks a part in service. |
| **Vibration and resonance** | If something shakes a part at a frequency it prefers, small forces build into large ones. Planned; see the roadmap. |
| **Contact, friction, bolt preload** | A mounting face is treated as held perfectly rigid, which is stiffer than any real bolted joint. |
| **Plasticity and permanent bending** | The material is assumed to spring back fully. |
| **Impact, heat, and thermal stress** | Not covered. |

### Sizing a shape and inventing one are two different commands

`openoptima optimise` changes the **dimensions** of a shape you describe.
It does not change the shape itself. Describe a rectangular arm with five
dimensions, and you get a rectangular arm with better dimensions.

The organic, bone-like shapes associated with "generative design" come
from a different method, called topology optimisation, which decides where
material should exist at all. That is `openoptima topology`. It runs
[`beso`](https://github.com/calculix/beso) rather than a new optimiser of
our own, turns the result into a sealed shape, and — with `--analyse` —
puts it back through the ordinary analysis so it reports a real stress and
factor of safety instead of only a picture.

**Do not skip that last step.** On the test case in this repository the
shape that came back kept 49.7 per cent of the material and its factor of
safety fell from 1.15 to **0.63**. It breaks. Nothing in the topology run
says so, because it was asked for stiffness at a weight target and nobody
asked it about stress.

Two limits worth knowing. The result is a triangle mesh, so it can be
printed but needs redrawing before it can be machined or cast. And a
rounded blend cannot be found on a triangle mesh — it meets the faces it
joins smoothly, leaving no crease to find it by — so a region selector
that asks for a blend by its radius will not match on a topology result.

The two modes combine well, and that is probably the most useful
workflow: find a shape with `topology`, redraw it as a parametric model,
then size and verify it with `optimise`.

### 3D printing: partly supported

A printed part is made from stacked layers, fused together rather than
continuous. It is commonly 30 to 50 per cent weaker *between* those layers
than *along* them.

**What works.** OpenOptima now models this directional stiffness. You give
the stiffness along the layers, the stiffness through them, and the
direction the part was built in. Verified to within 0.14% — see
[V10](#the-benchmarks).

**What does not work yet.** Two things:

- **Directional strength.** The standard measure of how hard a material is
  working, called von Mises stress, assumes equal strength in every
  direction — which is exactly what a printed part is not. So OpenOptima
  **withholds the factor of safety** for a directional material rather
  than reporting a misleading one. Stresses and displacements are still
  reported, and are correct.
- **Manufacturability.** There is no check on overhang angles, no check
  that a wall is thicker than your nozzle can produce, and no check that
  the part fits your build volume.

---

## Install

### Windows: the app

1. Download `OpenOptima-setup.exe` and run it. No administrator password is
   needed — it installs into your own account.
2. Press the Windows key, type `openoptima`, press Enter. Right-click it in
   the Start menu to pin it to the taskbar.

OpenOptima opens in **its own window**, with its own taskbar button — not a
browser tab. It shows four steps: choose a part, check the setup, run it, read
the results. You need no terminal and no Python installation. Closing the
window shuts it down.

**The first time, it will offer to set up the stress solver.** OpenOptima
builds the part and meshes it itself, but the structural calculation is done by
CalculiX, a separate free program. If you do not have it, the app offers to
download it for you — about 26 MB, under a minute, no administrator password —
or to use a copy you already have. Either way it runs the program once to check
it works before accepting it, and remembers the choice, so this is a one-off.

If the app ever fails to start, it says where to find its log:
`%LOCALAPPDATA%\OpenOptima\openoptima.log`.

To build the installer yourself, run `packaging\build_windows.ps1` and then
`iscc packaging\installer.iss`. See [`packaging/README.md`](packaging/README.md).

### Everything else: pip

OpenOptima needs two runtime components: a Python package and a solver
program.

```bash
# CalculiX, the program that does the structural calculation
sudo apt install calculix-ccx          # Debian/Ubuntu
brew install calculix                  # macOS

# OpenOptima
pip install -e ".[optimise]"
```

On Windows you do not have to find CalculiX yourself: run `openoptima-app` and
it will offer to install one. If you already have a copy anywhere, point at it
with the `OPENOPTIMA_CCX` environment variable or `solver.executable` in the
project file.

Gmsh comes from PyPI and includes its own OpenCASCADE geometry engine. You
do not need a separate CAD installation. CadQuery is optional
(`pip install -e ".[cadquery]"`). Install it only if you want to write
geometry in CadQuery's own code instead of using a built-in template.

Check the install:

```bash
openoptima doctor examples/l_bracket/project.yaml
```

---

## The eight commands

Run each one from your project folder.

| Command | What it does |
|---|---|
| `doctor` | Checks your setup. **Run this first, always.** |
| `faces` | Lists a part's faces with a description that finds each one. |
| `evaluate` | Analyses one design and prints the results. |
| `doe` | Tries a spread of designs to survey what is possible. |
| `optimise` | Searches for the best designs and writes a report. |
| `converge` | Re-runs one design at several mesh densities. |
| `report` | Rebuilds a report from stored results. |
| `templates` | Lists the built-in geometry templates. |

### The two that save you the most time

**`openoptima doctor`** builds your part at the smallest and largest sizes
in your design range. At each size it checks that OpenOptima can still
find every face you push or hold. If something is wrong, `doctor` tells
you within ten seconds. Without it you might not find out until two hours
into a run, or never.

**`openoptima converge`** answers the question every result depends on:
have the numbers stopped changing? It re-runs one design at several mesh
densities and reports, for each quantity, what the value did and how far
it might still move.

```
  mass_kg                 0.3228   unchanged
  displacement_max_mm     3.1584   settling to within 0.0036%, moved 0.162%
  stress_max_mpa        273.6095   running away, moved 3.45%
```

Read that as: the stiffness numbers are settled, and the stress numbers
are not. It is the second line that stops somebody sizing a part from a
number that is still moving.

### Try it

The app, if you prefer clicking to typing:

```bash
openoptima-app
```

Or the command line:

```bash
openoptima doctor   examples/l_bracket/project.yaml
openoptima evaluate examples/l_bracket/project.yaml
openoptima doe      examples/l_bracket/project.yaml --evaluations 24
openoptima optimise examples/l_bracket/project.yaml
openoptima converge examples/l_bracket/project.yaml
```

Five examples ship with OpenOptima:

- **`l_bracket`** minimises the mass of an aluminium bracket carrying a
  2.5 kN load, with a factor of safety of at least 2 and under 1 mm of
  deflection.
- **`drone_arm`** is one arm of a quadcopter, **3D printed in PLA**, and it
  makes two points a stress check cannot.

  A printed part is weaker between its layers than along them, so it has no
  single strength and von Mises stress cannot describe it. Changing only
  the print direction moves the factor of safety from 3.07 to 1.55 — while
  the stress stays at 7.53 against 7.54 MPa. The part is half as strong and
  the stress does not move. Which way up to print it is itself one of the
  things the optimiser decides here. It rules out the orientation that
  halves the strength — the lightest arm it can find that way is 106 g
  against about 71 — and is indifferent between the other two.

  It also carries a motor, and the motor is most of the moving mass. With
  it the arm vibrates at 121.5 Hz and without it at 191.4 Hz, so leaving it
  out reads 58% high in the direction that looks safe. The arm that stress
  alone would choose passes at a factor of safety of 3.07 and sits right in
  the range the propeller turns at. Sizing it clear of that takes it from
  40.7 g to 72.7 g — nearly double, for a failure no stress check can see.
- **`strut`** demonstrates buckling. At its lightest allowed section the
  stress factor of safety is 4.8, which looks very safe, while the
  buckling factor is 1.08 — within 8% of collapsing.
- **`imported_bracket`** is the same bracket as `l_bracket`, reached by
  reading a STEP file instead of a built-in template — the same file a
  SolidWorks or Fusion 360 "export as STEP" would produce. It shows that
  picking a face by what it looks like works the same way on a part you
  drew elsewhere. See [Bringing in your own part](#bringing-in-your-own-part)
  below.
- **`imported_bracket_fillet`** is that same imported part with something
  to optimise: OpenOptima rounds off one corner and searches for the
  largest radius that still leaves enough face to put the load on.

---

## Defining a study

A project is one YAML file. This is how you define a region, meaning a
named face or group of faces that you push or hold:

```yaml
regions:
  - name: mounting_face
    selector:
      surface_type: plane
      normal: [-1.0, 0.0, 0.0]
      prefer_largest: true
```

That is not a face number, and the difference matters.

**Why.** Every CAD program numbers the faces of a shape. When you change a
dimension, the shape rebuilds and those numbers change. A face that was
"face 6" before the rebuild can be a different surface afterwards. If your
load is attached to "face 6", it will eventually land on the wrong face.
The analysis still runs. The numbers still look sensible. They are wrong,
and nothing warns you.

So OpenOptima describes each face by what it is, and finds it again on
every evaluation. If two faces match equally well, OpenOptima **stops**
instead of guessing. A wrong guess looks exactly like success.

`openoptima doctor` checks every description at the extremes of your
design range, before you start a study.

**You do not have to write them by hand.** `openoptima faces` lists every
face of your part with a description that finds it, and `--yaml` prints it
ready to paste. Each one is checked against the part rebuilt at its
smallest and largest sizes before it is offered, and a face that genuinely
cannot be told apart from its neighbours is reported as such rather than
given a description that would quietly pick the wrong one later.

That check earns its keep. Describing the example bracket's two bolt holes
by their 4.5 mm radius looks entirely reasonable, and at the smallest
fillet setting the same description also selects the fillet, which by then
has shrunk to 3 mm — three faces where two were meant, and no error
anywhere.

**Or click instead of typing.** `openoptima-app` has a 3D panel — turn the
part, click a face, get the same description and YAML back with a name
field and a copy button. It runs the identical extremes check first, so a
description from a click is exactly as trustworthy as one from the
command line.

### Bringing in your own part

You do not have to build a shape from one of OpenOptima's own templates.
Export a STEP file from SolidWorks, Fusion 360 or almost any other CAD
package, and point a project at it:

```yaml
geometry:
  provider: step
  source: bracket.step
```

Everything above still applies. A region is still found by what a face
looks like, not by a number, and it works exactly the same way on an
imported shape — see `examples/imported_bracket`.

**Units are converted, and that is measured rather than assumed.** A STEP
file declares what it was drawn in. Both ways the format states that are
honoured exactly: a file declaring inches comes in at 25.4× its stated
numbers, one declaring metres at 1000×. A 100 × 10 × 5 inch box arrives as
2540 × 254 × 127 mm. Everything you then write in the project file is in
millimetres regardless, so `openoptima doctor` prints the size it read as
a check that the export was what you meant.

**A STEP file holds no dimensions, so bring your own.** The numbers whoever
drew the part typed in are not saved in the file — only the resulting
surfaces. There is therefore nothing *in the file* for `openoptima
optimise` to search over. What you can do is have OpenOptima add a corner
of its own on top of the imported shape and vary that:

```yaml
geometry:
  provider: step
  source: bracket.step
  variables:
    - id: corner_radius
      minimum: 2.0
      maximum: 16.0
      default: 6.0
  features:
    - name: outer_corner
      kind: fillet            # or `chamfer`, which cuts the corner off flat
      between: [arm_top, load_face]
      size: corner_radius     # a design variable id, or a plain number
```

The imported shape never changes; the corner is the only thing that moves.
`size` naming a design variable is what turns it into something to search
over.

**You name the two faces, never the edge.** Adding one fillet to the
example bracket renumbered every face of the part: the top of the arm went
from face 5 to face 2, the loaded end from 7 to 5, the base from 8 to 7.
So the edges are worked out from the two named regions on every build, by
the same resolver the loads use.

**A corner eats the faces beside it, and the selector keeps finding what is
left.** Measured on the example bracket, whose loaded end face starts at
1140 mm²: a 15 mm round leaves 240 mm², 18.9 mm leaves 6 mm², and 18.99 mm
leaves **0.6 mm²** — with the load still resolving onto it, at a stress to
match, and no error anywhere. The kernel refusing the round outright at
19 mm is no protection, because the dangerous band is just below it.

Two things address that. `openoptima doctor` prints every region's area at
each end of the design range and says so out loud when one moves by more
than about ten times. And a region can carry `min_area_mm2`, below which
the design is refused as infeasible so the optimiser learns the boundary.
There is no default: the right figure depends on what the face is for, and
that is the engineer's call. See `examples/imported_bracket_fillet`.

---

## Saying what you actually want

The hard question in optimisation is not finding good designs. It is
choosing between them.

A weighted score, such as `0.6 × strength + 0.4 × weight`, hides the
trade-off. Its result also changes meaning whenever your units change, and
it cannot reach some good designs at all.

So OpenOptima always produces the full set of trade-offs first, then lets
you rank it four ways:

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

OpenOptima also shows what each step along the front costs:

| Mass paid | Deflection gained | Cost per unit |
|-----------|-------------------|---------------|
| 0.0159 kg | 0.0323 mm         | 0.49          |
| 0.1479 kg | 0.0189 mm         | 7.82          |

The first step is cheap. The second is sixteen times worse value. That
point is the knee point, and a single blended score would have hidden it.

---

## How it is verified

Testing asks whether the software does what it was told. Verification asks
whether what it was told produces real physics. Only the second gives
anybody a reason to believe a number.

**377 tests run in total.** 260 of them need no CAE tool and run in
seconds. The rest need Gmsh and CalculiX.

### The benchmarks

| | Case | Reference | Measured |
|---|---|---|---|
| **V1** | Cantilever deflection | Timoshenko beam theory | **−0.98%** |
| **V3** | Column buckling | Euler, fixed-free | **+0.11%** |
| **V4** | Plate with a hole | Howland stress concentration | **−1.44% to +1.80%** |
| **V5** | Thick cylinder under pressure | Lamé, at five radii | **−0.93% to +0.25%** |
| **V6** | Mesh convergence | The sequence against itself | see below |
| **V7** | Multiple load cases | Enveloped, never averaged | exact |
| **V9** | Buckling validity | Sweep of 68 cases | see below |
| **V10** | Directional material | Ratio of the two moduli | **+0.14%** |
| **V14** | Natural frequencies | Euler-Bernoulli, first 3 modes | **+0.27% to −0.88%** |
| | Strain energy | Clapeyron's theorem | **2 parts in 1000** |

Full detail, including every tolerance and how it was chosen, is in
[`docs/verification-plan.md`](docs/verification-plan.md).

### The two results worth understanding

**V6 shows why raw peak stress is never optimised.** The same four solves
produce two numbers that behave in opposite ways. Tip deflection moved
0.058% in total and settled. Raw peak stress climbed 147.3 → 183.7 MPa,
moved 19.8%, and the steps grew larger with every refinement.

That second number is a singularity. At a perfectly sharp corner the
mathematics says the stress is infinite, so no mesh will ever converge on
it. The ratio between the two spreads is about **340**. An optimiser given
the peak would search the mesh rather than the design.

**V4 shows the other half of that argument.** A *real* rounded feature
does settle: it scattered inside a 3.2% band across a fourfold change in
element count. A real stress concentration has a real answer. A
singularity has none.

### The exact checks

Some checks are exact arithmetic rather than approximations, so they are
held very tight:

- **Reaction against applied load**, on every load case in every run. In
  V5 the reaction matched the exact pressure resultant to **one part in
  100 000**.
- **Strain energy against the work the load does.** These are identical
  for a linear elastic solve, on any mesh however coarse.
- **Mesh volume against CAD volume.**

---

## Defects the benchmarks found

Writing the benchmarks found four real defects. Every one produced a
confident, plausible, **wrong** answer rather than an error — which is the
failure this project exists to prevent.

**1. Buckling was wrong by nine times, in the unsafe direction.** CalculiX
silently skipped the lowest buckling mode and returned the second one. The
trigger was never slenderness, which is what OpenOptima originally guarded
against. It was the buckling factor itself: below about 0.52, the lowest
mode was skipped. A stubby column well inside the "safe" range reported a
part that folds under half its load as having a margin of 4.2.

Fixed at the root. A buckling factor is exactly inversely proportional to
the load it is measured against. So the buckling step now solves against a
load a thousand times smaller, then divides the answer back. Every
previously failing case now measures within 0.15% of Euler.

**2. Reactions were summed across directions that were not restrained.**
CalculiX reports a full force vector for every restrained set, including
the directions that set leaves free. Those figures are not reactions.
Adding them made the total 1.7% short. The equilibrium check then
reported an error on an analysis that was correct to one part in
100 000. It did so on every model using symmetry, which is the standard
way to analyse a pressure vessel.

**3. Cached results silently lost their mesh summary.** The data was
written to the database and never read back. A second run then served
every result from the cache and lost its element counts. It reported "not
enough data" while looking exactly like a run that had worked.

**4. An admissibility check rejected aluminium.** A threshold was absolute
where it needed to be relative. This one was caught by its own test before
it shipped.

Each defect now has a test that fails without its fix.

---

## How it is built

### The rules that shape it

- **`domain/` imports no solver, no mesher, no numpy and no database.** It
  is plain Python data and rules, so it loads in milliseconds and can be
  reasoned about without a CAE stack. A test enforces this.
- **External tools live behind adapters.** Adding a second solver must not
  require touching the optimiser, the DOE, the storage layer or the
  command line.
- **The optimiser reads one result type and nothing else.** It never opens
  a solver file, a mesh, or a log.
- **A bad design and a broken run are different things.** A bad design
  teaches the optimiser something. A solver crash teaches it nothing, and
  must never be fed back as a poor result.
- **Anything that can change a number belongs in the evaluation
  fingerprint.** Otherwise a stale cached result is served as a fresh one.

### Built with

[Gmsh](https://gmsh.info/) · [CalculiX](http://www.calculix.de/) ·
[pymoo](https://pymoo.org/) · [SciPy](https://scipy.org/) ·
[CadQuery](https://cadquery.readthedocs.io/) (optional)

---

## Documentation

| | |
|---|---|
| [**Plain-English guide**](docs/plain-english-guide.md) | **start here** — no jargon, explains every term |
| [Engineering assumptions](docs/engineering-assumptions.md) | **read before trusting a number** |
| [Verification plan](docs/verification-plan.md) | every benchmark, tolerance and measured value |
| [Known issues](docs/known-issues.md) | defects found, and what was done about each |
| [Architecture](docs/architecture.md) | how the pieces fit, and why |
| [Roadmap](docs/roadmap.md) | what is next |
| [Capability audit](docs/capability-audit.md) | what we reuse, what we build, and why |
| [Agent backlog](docs/agent-backlog.md) | scoped work, each with a definition of done |
| [File formats](docs/file-formats.md) | the project file and the workspace layout |
| [Technical writing standard](docs/technical-writing-standard.md) | the language rules all project text follows |
| [ADRs](docs/adr/) | decisions and the reasoning behind them |
| [AGENTS.md](AGENTS.md) | contributing, including with coding agents |

---

## Licence

OpenOptima is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE) and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

OpenOptima builds on Gmsh (GPL-2.0-or-later) and CalculiX
(GPL-2.0-or-later). We chose GPL-3.0-or-later for compatibility with both.
This is a practical assessment, not legal advice.
