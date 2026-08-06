# OpenOptima in plain English

This guide avoids jargon. Where a technical term is unavoidable, this guide
explains it the first time it appears. Every term also appears again in the
[glossary](#7-glossary) at the end.

---

## 1. What does this software actually do?

You have a part. You know roughly what shape it should be, but not what
size every feature should be. You want it as light as possible, but it
must not break.

Normally, you would do this by hand:

1. Guess some dimensions.
2. Run a stress analysis.
3. Look at the result.
4. Adjust the dimensions.
5. Repeat.

This process is slow. In practice, people try five or ten versions, then
pick the best one they found.

OpenOptima runs that loop automatically, hundreds of times. Then it shows
you the best designs it found, and what each one costs you.

**The loop OpenOptima repeats:**

```
   pick some dimensions
          ↓
   build the 3D shape
          ↓
   chop it into small pieces the computer can do sums on
          ↓
   work out how much it bends and how hard it is being stressed
          ↓
   record the weight, the stress, the deflection
          ↓
   pick better dimensions based on what it learned
          ↓
        (repeat)
```

That loop is the core of OpenOptima. Everything else in this project makes
that loop reliable, or helps you choose between the answers it gives you.

---

## 2. Running it

### The app

On Windows:

1. Install OpenOptima.
2. Open **OpenOptima** from the Start menu.

OpenOptima opens in your browser. It shows four steps: choose a part,
check the setup, run it, read the results. You do not need to type
anything.

On other systems, running `openoptima-app` does the same thing.

The rest of this section covers the command line. It does exactly the same
work, if you prefer typing instead.

### The commands

This guide covers four commands. Run each one from your project folder.

### `openoptima doctor`

```bash
openoptima doctor examples/l_bracket/project.yaml
```

**Always run this command first.** It builds your part at three sizes: the
smallest allowed, the default, and the largest allowed. At each size, it
checks that OpenOptima can still find the faces where you push or hold the
part.

If something is wrong with your setup, `doctor` tells you within ten
seconds. Without this check, you might not find out until two hours into a
run. In the worst case, you never find out, and you trust an answer that
looks fine but is wrong.

### `openoptima evaluate`

```bash
openoptima evaluate examples/l_bracket/project.yaml
openoptima evaluate examples/l_bracket/project.yaml --set thickness_h=18
```

This command analyses **one** design and prints the results. Use it to
check that a single set of dimensions behaves as you expect, before you
start a large run.

### `openoptima doe`

```bash
openoptima doe examples/l_bracket/project.yaml --evaluations 24
```

This command tries a spread of designs across your whole allowed range, to
show what is possible. Engineers call this a **design of experiments**, or
**DOE** for short.

Use DOE to explore before you commit to a long optimisation run. It tells
you which dimensions actually matter, which combinations are impossible,
and roughly where the good designs are.

### `openoptima optimise`

```bash
openoptima optimise examples/l_bracket/project.yaml
```

This is the main command. It explores first, then searches properly, then
writes you a report.

---

## 3. Setting up your own part

Everything is in one file, `project.yaml`. This section explains what each
part means.

### The dimensions the software is allowed to change

```yaml
variables:
  - id: thickness_h
    minimum: 5.0
    maximum: 20.0
    default: 10.0
```

"The horizontal plate can be anywhere from 5 mm to 20 mm thick. Start at
10."

Together, these are called **design variables**. The set of every
combination they can form is the **design space** — every combination the
software is allowed to try.

### The faces you push and hold

Read this section carefully. It explains a part of OpenOptima that is easy
to miss, and important to understand.

```yaml
regions:
  - name: mounting_face
    selector:
      surface_type: plane
      normal: [-1.0, 0.0, 0.0]
      prefer_largest: true
```

That selector means: *"the mounting face is the biggest flat face pointing
in the negative X direction."*

It does **not** say "face number 6".

**Why this matters.** Every CAD program numbers the faces of a shape. When
you change a dimension, the shape rebuilds, and the face numbers change
too. The face that was "face 6" before the rebuild can be a different
surface after it.

If your load is attached to "face 6", it will eventually land on the wrong
face. The analysis still runs, and the numbers still look sensible. The
numbers are wrong all the same, and nothing warns you.

Instead, OpenOptima **describes** the face by what it is, and finds it
again every time. The difference is like this:

> "the third person from the left" — wrong as soon as people move.

and

> "the tallest person in the room" — still correct, no matter how people
> move.

The description is called a **selector**, and the named face is a
**region**.

**If the description is ambiguous, OpenOptima stops.** For example, if you
say "the round hole" and there are two identical round holes, OpenOptima
refuses to pick one. It tells you to be more specific instead. A guess
would be worse than a failure, because a wrong guess looks exactly like
success.

### The material

```yaml
material:
  name: Aluminium 6082-T6
  elastic_modulus_mpa: 70000.0     # how stiff it is
  poisson_ratio: 0.33              # how much it bulges sideways when squashed
  density_kg_m3: 2700.0            # how heavy it is
  allowable_stress_mpa: 160.0      # how hard you're willing to work it
  allowable_stress_basis: "0.2% proof stress 260 MPa with a 1.6 design factor"
```

**Allowable stress is your decision. It is not a property of aluminium.**
This material yields at 260 MPa. Choosing to work it at only 160 MPa
leaves a safety margin. The right size for that margin depends on several
things:

- how well you know the actual loads
- how the part is made
- how the part is inspected
- what happens if the part fails

OpenOptima will not guess this value for you. It asks you to write down
*why* you chose it, in `allowable_stress_basis`. OpenOptima then prints
that reason in every report. Anyone who reviews your work can then see
your reasoning.

### The loads

```yaml
load_cases:
  - id: vertical_end_load
    boundary_conditions:
      - region: mounting_face      # this face is bolted down, it can't move
    loads:
      - kind: force
        region: load_face
        vector: [0.0, 0.0, -2500.0]   # 2500 Newtons, straight down
```

A **load case** is one scenario your part must survive. You can define
several — lifting, braking, cornering — and OpenOptima checks every one of
them.

OpenOptima always uses the **worst** result across all load cases. It
never averages them. Averaging a failing scenario together with a passing
one would hide the failure.

### What you want

```yaml
objectives:
  - metric: mass_kg
    direction: minimise            # make it light

constraints:
  - metric: factor_of_safety
    operator: greater_than_or_equal
    value: 2.0                     # but never weaker than this
```

An **objective** is something to push as far as you can. A **constraint**
is a line you must not cross. "As light as possible, but never below a
factor of safety of 2."

**Factor of safety** is how much margin you have. A factor of safety of
2.0 means the stress is half of the allowable value you chose. A factor of
safety below 1.0 means the part is overloaded.

---

## 4. The results, and how to read them

### The main idea: there is usually no single best design

Suppose you want a part that is both light and strong. These two goals
conflict: removing metal makes the part lighter, but also weaker.

So there is no single winner. There is a **set** of designs where you
cannot improve one thing without giving up another. That set is called the
**Pareto front** (pronounced "pa-RAY-toe").

Think of it as a menu of the best available deals:

```
  strength
     ▲
     │        ● D  (very strong, very heavy)
     │      ●C
     │    ●B
     │  ●A     (light, only just strong enough)
     │
     └──────────────────────► weight
```

Every one of A, B, C, D is a legitimate answer. Which one is right depends
on what *you* care about. OpenOptima cannot know that, so it shows you all
of them instead of picking one.

OpenOptima discards any design below and to the right of that curve — such
a design is worse in every respect.

### Why not just give it a score?

The obvious shortcut is a formula, such as
`0.6 × strength + 0.4 × lightness`. OpenOptima deliberately does not do
this, for three reasons:

1. **The answer changes if you change units.** If you measure weight in
   grams instead of kilograms, the same formula picks a different design.
   That should worry you.
2. **It cannot reach some good designs at all.** No matter how you set the
   weights, some good designs on the curve stay permanently out of reach.
3. **It hides the decision.** You get a number, not an understanding.
   Nobody can argue with it, including you.

### Instead: what does each step actually cost?

This section answers that question directly. Here is an example, from a
real run:

| Extra mass paid | Deflection improvement bought | Cost per unit |
|-----------------|-------------------------------|---------------|
| 0.016 kg        | 0.032 mm                      | 0.49          |
| 0.148 kg        | 0.019 mm                      | **7.82**      |

Read that as: the first 16 grams of extra weight buys a lot of
improvement. The next 148 grams buys almost nothing — sixteen times worse
value.

That table shows exactly the trade-off you need to see. A single blended
score would hide this pattern. The point where the value drops sharply is
called the **knee point**. OpenOptima finds this point, and highlights it
for you.

### Telling it what you actually care about

OpenOptima offers four ways to state your preferences, from the bluntest
to the most subtle. Use as many, or as few, as you like.

**1. Hard limits — "never acceptable"**

```yaml
constraints:
  - metric: factor_of_safety
    operator: greater_than_or_equal
    value: 2.0
```

**2. Targets — "what I am aiming for"**

```yaml
targets:
  - metric: mass_kg
    operator: less_than_or_equal
    value: 0.55
```

**3. Saturation — "past this point, more is pointless"**

```yaml
desirability:
  - metric: factor_of_safety
    direction: maximise
    ideal: 3.0        # above 3.0, extra strength buys me nothing
    acceptable: 2.0   # below 2.0, no amount of lightness makes up for it
```

This setting stops OpenOptima from adding weight for strength you do not
need.

**4. Trade rules — "I will pay X for Y"**

```yaml
trade_rules:
  - give_metric: mass_kg
    give_amount: 0.025            # I'll accept 25 more grams
    gain_metric: factor_of_safety
    gain_amount: 0.1              # for each 0.1 of factor of safety
```

This rule writes down your own trade-off directly: a small cost, for a
stated gain. OpenOptima then walks along the menu of designs, one step at
a time. It takes each step while that step is still worth it, by your own
rule. It stops at the first step that is not worth it.

---

## 5. Things that will confuse you if nobody explains them

### "Mesh" and why elements are curved

A computer cannot calculate directly on a smooth shape. OpenOptima chops
the part into thousands of small tetrahedra (triangular pyramids), and
calculates on each one. That is the **mesh**. Each small piece is an
**element**.

Elements come in two kinds. **First-order** elements have straight edges.
**Second-order** elements have an extra point halfway along each edge, so
their edges can curve.

OpenOptima uses second-order elements. Straight-edged, first-order
elements are artificially stiff: they make the part look stronger than it
really is. This error is large enough to invalidate the whole result. This
is why OpenOptima checks the element type, and refuses to continue if
meshing accidentally produces the wrong kind.

### Stress "singularities" — the strangest thing in this document

Where a part has a perfectly sharp internal corner, or is held perfectly
rigidly, the mathematics says the stress there is **infinite**.

The stress is not just "very high" — it is genuinely infinite. This is not
a bug in the software. It is what the equations say, when you assume a
perfectly sharp corner. A perfectly sharp corner does not exist in
reality.

In practice, this means: if you chop the part into finer and finer pieces,
the peak stress there keeps climbing, without limit. It never settles on a
final answer.

**This matters a great deal for optimisation.** Suppose you tell
OpenOptima to minimise peak stress directly. Peak stress is then a
meaningless number: its value depends only on how finely you chopped the
part. OpenOptima would spend its whole budget of evaluations pursuing a
number that means nothing.

So, by default, OpenOptima ignores the worst 1% of the stress values, and
uses the next value down instead — the **99th percentile**. This number is
real and stable.

OpenOptima still reports the true peak stress on every result, in
`stress_raw_max_mpa`. Nothing is hidden from you. OpenOptima simply does
not use that peak value as the optimisation target.

Your part might have a *genuine* stress concentration: a real fillet, with
a real radius, not an idealised sharp corner. If so, model that fillet
accurately. Refine the mesh there as well. Do not rely on the percentile
to hide a real stress concentration.

### "Infeasible" versus "error" — a distinction that matters more than it sounds

When a design fails, OpenOptima distinguishes two different reasons:

| | What it means | What we do |
|---|---|---|
| **Infeasible** | The design is genuinely bad — too thin, too weak, impossible to make | Tell the optimiser. It should learn from this. |
| **Error** | OpenOptima could not find out — for example, the solver crashed, or the disk filled up | Retry. Tell the optimiser **nothing**. |

Why does this distinction matter? Imagine the solver crashes while testing
thick designs, purely by chance. If OpenOptima scored those crashes as
"terrible designs", the optimiser would learn the wrong lesson: "thick is
bad". It would then avoid thick designs for the rest of the run. It would
never find the right answer. Worse, it would look like it was working
correctly the whole time.

Your run summary shows both counts. If the **errors** count is not zero,
something is wrong with your setup — not with the design itself.

### The "cache" and why changing the material invalidates it

Analysing one design takes only seconds. So OpenOptima saves each result,
and reuses it if you ask the same question twice.

However, "the same question" must genuinely mean the same question. If you
change the material, the load, or the mesh settings, an old answer becomes
the answer to a *different question*. Reusing that old answer would
instantly give you a wrong number.

So OpenOptima stamps every saved result with everything that could affect
it: the material, the loads, the mesh, the stress settings, and the exact
program versions that produced it. If you change any of these, OpenOptima
correctly ignores the old result.

### The run folder

Every analysis leaves behind a folder. That folder contains:

- the 3D model
- the mesh
- the exact input sent to the solver
- the raw solver output
- a summary file listing every program version used

This folder can look like clutter. It is, however, the only way to answer
the question "where did this number come from?", months later. If disk
space becomes a problem, use `--discard-artifacts`. This keeps the summary
file, and deletes the large intermediate files.

---

## 6. When not to trust the answer

This section is important, so read it carefully. The analysis is **linear
static** — it works out how much the part bends, and how much stress it
carries, under one steady load. It calculates nothing else.

**Buckling is now included** (see below). It stays switched off unless you
turn it on. OpenOptima still cannot see any of the following:

- **Fatigue** — failing after millions of load cycles, at stresses it
  would survive indefinitely if applied once.
- **Bolts, joints, contact, friction** — OpenOptima treats the mounting
  face as held perfectly rigid, which is stiffer than any real bolted
  joint.
- **Impact, vibration, heat, and permanent bending.**

An optimiser will exploit every one of these blind spots, given the
chance.

### Buckling — worth understanding

**Buckling happens when something long and thin suddenly folds sideways.**
It does not get crushed straight down; it folds. Stand on an empty drinks
can, and it holds your weight. Press the side of the can, and it collapses
instantly. The metal in the can never came close to its strength limit.
The shape simply became unstable.

This matters a great deal when you minimise mass. "As light as possible"
usually means "as thin as possible" — and thin parts are exactly the parts
that buckle.

Turn it on like this:

```yaml
buckling:
  enabled: true
  modes: 3
```

OpenOptima reports a **buckling factor**: how many times the applied load
the part can carry before it folds. A factor of 3 means the part folds at
three times your load. A factor below 1.0 means the part folds under the
load you actually gave it.

The example at `examples/strut/project.yaml` exists to show why this
matters. Consider a 600 mm strut carrying 30 kN, at the lightest section
OpenOptima allows. This design has a **stress factor of safety of 4.8**,
which looks very safe. But it has a **buckling factor of only 1.08** —
within 8% of collapsing. Sizing the strut correctly costs about 50% more
mass. The stress check alone never comes close to noticing the real
danger.

**Engineers usually set a higher margin for buckling than for stress.**
This calculation assumes a perfectly straight strut, loaded exactly down
its centre. Real parts are always slightly bent. Real loads are always
slightly off-centre. Both effects make a real part buckle sooner than the
calculation predicts.

**One important limitation.** The method this software uses to chop a
part into small pieces stops being trustworthy for buckling, once a part
becomes very long and thin. It also fails in the *dangerous* direction: it
can report a part as safer than it really is. So OpenOptima checks its own
buckling answers against a hand-calculation theory. **OpenOptima refuses
to give you a number it does not trust**, instead of handing you a
reassuring, wrong one. If you see `result_unreliable`, OpenOptima is
telling you that it cannot answer. That refusal is the honest response.

**One more thing to know.** Every result in a single run comes from one
mesh setting. You can fairly compare these results against each other.
Before you trust any single number, re-run your chosen design with a
finer mesh. Check that the numbers have stopped moving.

See [`engineering-assumptions.md`](engineering-assumptions.md) for full
detail.

---

## 7. Glossary

| Term | Plain meaning |
|---|---|
| **Allowable stress** | How hard you have decided to work the material. This is your choice, not a property of the material. |
| **Boundary condition** | Somewhere the part is held, and cannot move. |
| **Buckling** | Something long and thin suddenly folding sideways, like an empty can when you press its side. |
| **Buckling factor** | How many times your load the part takes before it folds. Below 1.0 means it folds now. |
| **Cache** | Saved results, reused when you ask an identical question. |
| **CalculiX** | The free program that does the actual stress calculation. |
| **Cantilever** | A beam or part fixed at one end and free at the other, like a diving board. |
| **Constraint** | A line you must not cross. |
| **Design space** | Every combination of dimensions the software may try. |
| **Design variable** | One dimension the software is allowed to change. |
| **DOE** (design of experiments) | A planned spread of trial designs, used to survey what is possible. |
| **Element** | One small piece the part is chopped into. |
| **Factor of safety** | How much margin you have. 2.0 = stress is half the allowable. |
| **Gmsh** | The free program that builds the shape and chops it into pieces. |
| **Infeasible** | The design itself is no good. |
| **Knee point** | Where you stop getting good value for what you pay. |
| **Load case** | One scenario the part must survive. |
| **Mesh** | The part chopped into thousands of small pieces. |
| **NSGA-II** | The search method used. Keeps a population of designs and improves them, like breeding. |
| **Objective** | Something to push as far as you can. |
| **Pareto front** | The set of best available deals; no single winner. |
| **Percentile (99th)** | Ignore the top 1%, take the next value down. Avoids meaningless infinities. |
| **Poisson contraction** | The material narrowing sideways as it stretches lengthwise. Ordinary beam theory ignores this effect. |
| **Provenance** | The record of exactly what produced a number. |
| **Region** | A named face or set of faces you push or hold. |
| **Second-order element** | A piece with curved edges. More accurate than straight-edged. |
| **Selector** | The description used to find a face, e.g. "biggest flat face pointing at −X". |
| **Singularity** | A place where the maths says stress is infinite. An artefact of idealised sharp corners. |
| **Solver** | The program that does the stress calculation. |
| **Sobol / Latin hypercube** | Ways of spreading trial designs out evenly rather than randomly. |
| **Stress** | Force divided by the area carrying it. How hard the material is working. |
| **Trade rule** | Your stated exchange rate: "I will pay this much of X for that much of Y". |
| **von Mises stress** | The standard single number for "how hard is this metal working", combining stresses in all directions. |

---

## 8. If you only remember five things

1. **Run `doctor` before anything else.** It catches setup mistakes in
   seconds.
2. **OpenOptima describes faces; it does not number them.** This is what
   stops a load from silently attaching to the wrong place.
3. **There is no single best design.** You get a menu of designs, and a
   table showing what each step up that menu costs you.
4. **An "error" in a run summary means something is broken.** "Infeasible"
   just means those designs were no good. That is normal, and useful.
5. **OpenOptima does not know about buckling or fatigue by default.**
   Check those separately, before you trust a lightweight result.
