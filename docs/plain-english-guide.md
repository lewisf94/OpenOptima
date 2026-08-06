# OpenOptima in plain English

No jargon. Where a technical term is unavoidable, it is explained the first time
it appears, and every term is listed again in the [glossary](#glossary) at the end.

---

## 1. What does this software actually do?

You have a part. You know roughly what shape it should be, but not what size
every feature should be. You want it as light as possible without it breaking.

Normally you would guess some dimensions, run a stress analysis, look at the
result, adjust, and repeat. That is slow, so in practice people try five or ten
versions and pick the best one they found.

This software does that loop automatically, hundreds of times, and then shows
you the best designs it found and what each one costs you.

**The loop it runs, over and over:**

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

That is the whole idea. Everything else in this project exists to make that loop
reliable, and to help you choose between the answers it gives you.

---

## 2. Running it

Four commands. Run them from the project folder.

### `openoptima doctor`

```bash
openoptima doctor examples/l_bracket/project.yaml
```

**Run this first, always.** It builds your part at its smallest possible size,
its default size, and its largest possible size, and checks that the software
can still correctly find the faces you said to push and hold.

If something is wrong with your setup, this tells you in ten seconds. Without
it, you might find out two hours into a run — or worse, not find out at all,
and get an answer that looks fine but isn't.

### `openoptima evaluate`

```bash
openoptima evaluate examples/l_bracket/project.yaml
openoptima evaluate examples/l_bracket/project.yaml --set thickness_h=18
```

Analyses **one** design and prints the results. Useful for checking that a
single set of dimensions behaves the way you expect before you set a big run
going.

### `openoptima doe`

```bash
openoptima doe examples/l_bracket/project.yaml --evaluations 24
```

Tries a spread of designs across the whole range you allowed, to survey what is
possible. This is a **design of experiments**, usually shortened to **DOE**.

Think of it as scouting. It tells you which dimensions actually matter, which
combinations are impossible, and roughly where the good designs live — before
you commit to a long optimisation run.

### `openoptima optimise`

```bash
openoptima optimise examples/l_bracket/project.yaml
```

The main event. Scouts first, then hunts properly, then writes you a report.

---

## 3. Setting up your own part

Everything lives in one file, `project.yaml`. Here is what each part of it means.

### The dimensions the software is allowed to change

```yaml
variables:
  - id: thickness_h
    minimum: 5.0
    maximum: 20.0
    default: 10.0
```

"The horizontal plate can be anywhere from 5 mm to 20 mm thick. Start at 10."

These are called **design variables**. The set of all of them is the
**design space** — every combination the software is allowed to try.

### The faces you push and hold

This is the part most worth understanding, because it is where the software does
something clever that isn't obvious.

```yaml
regions:
  - name: mounting_face
    selector:
      surface_type: plane
      normal: [-1.0, 0.0, 0.0]
      prefer_largest: true
```

That says: *"the mounting face is the biggest flat face pointing in the negative
X direction."*

Notice it does **not** say "face number 6".

**Why this matters.** Every CAD program numbers the faces of a shape. But when
you change a dimension and the shape rebuilds, the numbers get shuffled. Face 6
before might be a completely different surface after.

If your load is attached to "face 6", it will eventually end up on the wrong
face. The analysis still runs. The numbers still look sensible. They are just
wrong, and nothing warns you.

So instead, we **describe** the face by what it is, and go and find it again
every single time. It is the difference between:

> "the third person from the left" — useless once everyone shuffles

and

> "the tallest person in the room" — still finds the right person

The description is called a **selector**, and the named face is a **region**.

**And if the description is ambiguous, the software stops.** If you say "the
round hole" and there are two identical round holes, it refuses to pick one and
tells you to be more specific. Guessing would be worse than failing, because a
guess looks exactly like success.

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

**Allowable stress is your decision, not a property of aluminium.** The material
yields at 260 MPa. Choosing to only work it to 160 leaves a safety margin. How
big that margin should be depends on how well you know the loads, how the part
is made, how it's inspected, and what happens if it fails.

The software will not guess this for you. It asks you to write down *why* you
chose it (`allowable_stress_basis`), and prints that in every report so anyone
reviewing your work can see your reasoning.

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

A **load case** is one scenario the part has to survive. You can have several —
lifting, braking, cornering — and the software checks all of them.

Crucially it takes the **worst** result, never the average. Averaging a scenario
that fails with one that passes would hide the failure.

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

An **objective** is something to push as far as you can. A **constraint** is a
line you must not cross. "As light as possible, but never below a factor of
safety of 2."

**Factor of safety** is simply how much margin you have. 2.0 means the stress is
half of what you said was allowable. Below 1.0 means it's overloaded.

---

## 4. The results, and how to read them

### The main idea: there is usually no single best design

Suppose you want a light part *and* a strong part. Those fight each other —
removing metal makes it lighter and weaker.

So there is no single winner. There is a **set** of designs where you cannot
improve one thing without giving up another. That set is called the
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

Every one of A, B, C, D is a legitimate answer. Which is right depends on what
*you* care about — and the software cannot know that, so it shows you all of
them rather than picking one.

Anything below and to the right of that curve is simply worse in every respect
and gets thrown away.

### Why not just give it a score?

The obvious shortcut is a formula like `0.6 × strength + 0.4 × lightness`.
We deliberately don't do this, for three reasons:

1. **The answer changes if you change units.** Measure weight in grams instead
   of kilograms and the same formula picks a different design. That should
   worry you.
2. **It can't reach some good designs.** If the curve bulges the wrong way, whole
   families of sensible designs become unreachable no matter what weights you use.
3. **It hides the decision.** You get a number, not an understanding. Nobody can
   argue with it, including you.

### Instead: what does each step actually cost?

This is the bit that answers the question you asked. From a real run:

| Extra mass paid | Deflection improvement bought | Cost per unit |
|-----------------|-------------------------------|---------------|
| 0.016 kg        | 0.032 mm                      | 0.49          |
| 0.148 kg        | 0.019 mm                      | **7.82**      |

Read that as: *the first 16 grams buys you a lot. The next 148 grams buys you
almost nothing — sixteen times worse value.*

That is exactly the insight you were asking about, and it's the sort of thing a
single score would bury. The point where the value collapses is called the
**knee point**, and the software finds and highlights it for you.

### Telling it what you actually care about

Four ways, from bluntest to most subtle. Use as many or as few as you like.

**1. Hard limits — "never acceptable"**

```yaml
constraints:
  - metric: factor_of_safety
    operator: greater_than_or_equal
    value: 2.0
```

**2. Targets — "what I'm aiming for"**

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

This stops the software adding weight to chase strength you don't need.

**4. Trade rules — "I'll pay X for Y"**

```yaml
trade_rules:
  - give_metric: mass_kg
    give_amount: 0.025            # I'll accept 25 more grams
    gain_metric: factor_of_safety
    gain_amount: 0.1              # for each 0.1 of factor of safety
```

This is your "small penalty for a big gain" question, written down. The software
walks up the menu of designs, taking each step for as long as it's worth it by
your own rule, and stops at the first step that isn't.

---

## 5. Things that will confuse you if nobody explains them

### "Mesh" and why elements are curved

The computer can't do sums on a smooth shape. It chops the part into thousands
of small tetrahedra (triangular pyramids) and does sums on each one. That is the
**mesh**, and each piece is an **element**.

Elements come in two flavours. **First-order** ones have straight edges.
**Second-order** ones have an extra point halfway along each edge, so they can
curve.

We use second-order, because straight-edged elements are artificially stiff —
they make the part look stronger than it is. That is a big enough error to
invalidate the whole answer, which is why the software refuses to continue if it
accidentally produces the wrong kind.

### Stress "singularities" — the strangest thing in this document

Where a part has a perfectly sharp internal corner, or is held perfectly rigidly,
the mathematics says the stress there is **infinite**.

Not "very high" — actually infinite. And it's not a bug in the software; it's
what the equations genuinely say when you assume a perfectly sharp corner, which
doesn't exist in reality.

The practical consequence: if you chop the part into finer and finer pieces, the
peak stress there just keeps climbing. Forever. It never settles on an answer.

**Why this matters enormously for optimisation.** If you tell the software to
minimise peak stress, and peak stress is a meaningless number that depends on how
finely you chopped the part, the software will spend its whole budget chasing a
number that means nothing.

So by default we ignore the worst 1% of the values and use the next one down —
the **99th percentile**. That is a real, stable number.

The true peak is still reported on every single result (`stress_raw_max_mpa`),
so nothing is hidden from you. It's just not what the optimiser chases.

If your part has a *genuine* stress concentration — a real fillet with a real
radius, not an idealised sharp corner — model the fillet properly and refine the
mesh there. Don't rely on the percentile to hide it.

### "Infeasible" versus "error" — a distinction that matters more than it sounds

When a design fails, there are two completely different reasons:

| | What it means | What we do |
|---|---|---|
| **Infeasible** | The design is genuinely bad — too thin, too weak, impossible to make | Tell the optimiser. It should learn from this. |
| **Error** | We couldn't find out — the solver crashed, the disk filled up | Retry. Tell the optimiser **nothing**. |

Why keep these apart? Imagine the solver crashes while testing thick designs,
purely by bad luck. If we score those as "terrible designs", the software learns
"thick is bad" and avoids thick designs for the rest of the run. It would never
find the right answer, and it would look like it was working perfectly the
whole time.

Your run summary shows both counts. If **errors** is anything other than zero,
something is wrong with the setup, not the design.

### The "cache" and why changing the material invalidates it

Analysing a design takes seconds, so results are saved and reused if you ask the
same question twice.

But "the same question" has to genuinely mean the same. If you change the
material, or the load, or the mesh settings, then an old answer is an answer to a
*different question*. Reusing it would give you a wrong number, instantly.

So the saved result is stamped with everything that could affect it — material,
loads, mesh, stress settings, and which versions of which programs produced it.
Change any of them and the old results are correctly ignored.

### The run folder

Every single analysis leaves a folder behind containing the 3D model, the mesh,
the input given to the solver, the raw output, and a summary file recording every
program version used.

This looks like clutter. It's the only thing that lets you answer "where did this
number come from?" six months later, when someone asks. Use `--discard-artifacts`
if disk space becomes a problem — it keeps the summaries and throws away the bulk.

---

## 6. When not to trust the answer

Genuinely important. The analysis is **linear static**, which means it works out
how much things bend and stress under a steady load, and nothing else.

It does **not** consider:

- **Buckling** — a thin panel suddenly folding, like standing on an empty can.
  This is the big one, because making something as light as possible pushes it
  towards thin sections, which is exactly what buckles. The software cannot see
  this at all.
- **Fatigue** — failing after millions of load cycles, at stresses it would
  survive indefinitely if applied once.
- **Bolts, joints, contact, friction** — the mounting face is treated as
  perfectly rigidly held, which is stiffer than any real bolted joint.
- **Impact, vibration, heat, permanent bending.**

An optimiser will happily exploit every one of these blind spots.

**Also:** all results come from one mesh setting. They're fair to compare against
each other, but you should re-run your chosen design with a finer mesh and check
the numbers have stopped moving before believing any single one.

Full detail in [`engineering-assumptions.md`](engineering-assumptions.md).

---

## 7. Glossary

| Term | Plain meaning |
|---|---|
| **Allowable stress** | How hard you've decided you're willing to work the material. Your choice, not the material's. |
| **Boundary condition** | Somewhere the part is held and can't move. |
| **Cache** | Saved results, reused when you ask an identical question. |
| **CalculiX** | The free program that does the actual stress calculation. |
| **Constraint** | A line you must not cross. |
| **Design space** | Every combination of dimensions the software may try. |
| **Design variable** | One dimension the software is allowed to change. |
| **DOE** (design of experiments) | A planned spread of trial designs, to survey what's possible. |
| **Element** | One small piece the part is chopped into. |
| **Factor of safety** | How much margin you have. 2.0 = stress is half the allowable. |
| **Gmsh** | The free program that builds the shape and chops it into pieces. |
| **Infeasible** | The design itself is no good. |
| **Knee point** | Where you stop getting good value for what you're paying. |
| **Load case** | One scenario the part must survive. |
| **Mesh** | The part chopped into thousands of small pieces. |
| **NSGA-II** | The search method used. Keeps a population of designs and improves them, like breeding. |
| **Objective** | Something to push as far as you can. |
| **Pareto front** | The set of best available deals; no single winner. |
| **Percentile (99th)** | Ignore the top 1%, take the next value down. Avoids meaningless infinities. |
| **Provenance** | The record of exactly what produced a number. |
| **Region** | A named face or set of faces you push or hold. |
| **Second-order element** | A piece with curved edges. More accurate than straight-edged. |
| **Selector** | The description used to find a face, e.g. "biggest flat face pointing at −X". |
| **Singularity** | A place where the maths says stress is infinite. An artefact of idealised sharp corners. |
| **Solver** | The program that does the stress calculation. |
| **Sobol / Latin hypercube** | Ways of spreading trial designs out evenly rather than randomly. |
| **Stress** | Force divided by the area carrying it. How hard the material is working. |
| **Trade rule** | Your stated exchange rate: "I'll pay this much of X for that much of Y". |
| **von Mises stress** | The standard single number for "how hard is this metal working", combining stresses in all directions. |

---

## 8. If you only remember five things

1. **Run `doctor` before anything else.** It catches setup mistakes in seconds.
2. **Faces are described, not numbered** — that's what stops loads silently
   attaching to the wrong place.
3. **There's no single best design.** You get a menu, and a table showing what
   each step up it costs you.
4. **"Errors" in a run summary mean something is broken.** "Infeasible" just
   means those designs were no good, which is normal and useful.
5. **It doesn't know about buckling or fatigue.** Check those separately before
   trusting a lightweight result.
