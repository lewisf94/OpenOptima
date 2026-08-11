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

### The main mode: better dimensions for a shape you describe

OpenOptima's main mode changes the **dimensions** of a shape you describe.
It does not change the shape itself. If you describe a rectangular arm with
five dimensions, you get a rectangular arm with better dimensions. You
never get a different kind of arm.

This matters because of what people often expect. The organic, bone-like
shapes you may have seen from "generative design" come from a different
technique, called **topology optimisation**. That method starts from a
solid block and deletes every part of it that is not carrying its weight,
so it invents the shape rather than sizing one.

### The second mode: let it invent the shape

OpenOptima can do that too, as a separate command — `openoptima topology`.
You give it a block of space the part may occupy, say how much of the
material to keep, and it removes the rest.

**Read this before you use it.** What comes back is a *proposal*, not a
finished part:

- **It is a shape, not an answer.** Deleting material makes a part weaker,
  and how much weaker is not something the process itself tells you. You
  have to analyse the shape afterwards. Add `--analyse` and it does exactly
  that, and reports a real stress, deflection and factor of safety.
- **It very often fails.** On the test case in this repository, the shape
  that came back kept 49.7% of the material — and its factor of safety fell
  from 1.15 to **0.63**. That means it breaks. Nothing in the topology run
  says so, because it was asked for stiffness at a weight target and
  nobody asked it about stress.
- **It is blocky, and gets smoothed.** The raw result follows the edges of
  the pieces the block was chopped into, like stairs. Those stairs are
  smoothed off, which removes a little more material — measured at 0.3% on
  the test case, and reported every time.
- **Nobody can make it directly.** It comes out as a triangle mesh. You can
  3D print it, but for machining or casting somebody has to redraw it.

So there are two questions you can ask:

- *"I have a shape, what are the best dimensions for it?"* — `optimise`.
- *"I have a problem, roughly what shape should it be?"* — `topology`, then
  redraw it, then `optimise`.

---

## 2. Running it

### The app

On Windows:

1. Double-click `OpenOptima-setup.exe` and follow the prompts. It does not ask
   for an administrator password, and it only installs into your own account.
2. Press the Windows key, type `openoptima`, and press Enter.

To keep it handy, right-click it in the Start menu and choose **Pin to
taskbar**.

OpenOptima opens in its own window, with its own button on the taskbar. It
shows four steps: choose a part, check the setup, run it, read the results.
You do not need to type anything. Closing the window shuts OpenOptima down.

If it ever fails to start, you get a message box pointing at a log file. That
file is at `%LOCALAPPDATA%\OpenOptima\openoptima.log` — paste it into a bug
report and it will usually say exactly what went wrong.

The window is drawn by Microsoft Edge, which is already on every Windows
computer, but it is not a browser tab: there is no address bar and nothing
to get lost in. If Edge and Chrome are both missing, OpenOptima falls back to
opening a normal browser tab rather than refusing to start.

On other systems, running `openoptima-app` does the same thing.

### The first time: the stress solver

OpenOptima builds your part and chops it into small pieces itself, but the
stress calculation is done by a separate free program called **CalculiX**.
Without CalculiX, OpenOptima can show you a part but cannot work out a single
number about it.

If you do not have CalculiX, the app opens on a setup panel with two choices.
Neither needs an administrator password, and either one is a once-only job.

- **"Install it for me"** fetches CalculiX from the CalculiX project itself.
  About 26 MB to download, about 10 MB kept on disk, usually under a minute.
  OpenOptima keeps it in its own folder and does not change anything else on
  your computer.
- **"I already have it"** takes the location of a copy you already have. The
  folder is enough — it looks inside for the program.

Either way, OpenOptima **runs the program once** before accepting it, and
shows you the version it got back. This is worth knowing about, because there
is a way to have a copy of CalculiX that looks perfectly fine and does not
work: on Windows the solver is a small program that needs seven other files
next to it, and a copy moved away from those files exists, is the right size,
and dies instantly with no message. Running it is the only check worth
anything. It takes about a second and it happens here, on the setup screen,
rather than an hour into a run.

Your choice is remembered, so you will not see the panel again. If you ever
want to change it, the panel reappears with a **"Use a different one"**
button.

If you prefer the command line, you can point at a copy yourself by setting an
environment variable called `OPENOPTIMA_CCX` to the location of the program.

The rest of this section covers the command line. It does exactly the same
work, if you prefer typing instead.

### The commands

This guide covers five commands. Run each one from your project folder.

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

### `openoptima topology`

```bash
openoptima topology project.yaml --deck block.inp --keep 0.5 --analyse
```

Starts from a block of space and removes the material that is not earning
its place. `--keep 0.5` means keep half of it. `--deck` points at the block
of space, written as a CalculiX analysis file.

`--analyse` is the important flag. Without it you get a shape and nothing
else — no stress, no deflection, no factor of safety, because none has been
worked out. With it, the shape is chopped up again and solved properly, and
you get the same numbers you would get from `evaluate`.

Two more options worth knowing:

- `--feature-size 16` is the thinnest piece you will accept, in mm. Set it
  to something you could actually make. Too small and you get spider webs.
- `--cores 1` is the default and you should leave it there. On more than
  one core, running the same problem twice gives two different shapes. That
  is not a bug in this software — the stress solver's arithmetic differs in
  its last few digits depending on how the work is shared out, and this
  process turns those digits into keep-or-delete decisions that compound
  over dozens of rounds.

---

## 3. Setting up your own part

Everything is in one file, `project.yaml`. This section explains what each
part means.

### Where the shape comes from

Most of this guide assumes you are using one of OpenOptima's own shapes —
an L-bracket, a strut, a plate — and changing its dimensions. You can also
bring in a part **you** drew, in another program:

```yaml
geometry:
  provider: step
  source: bracket.step
```

`bracket.step` is a file exported from SolidWorks, Fusion 360, or almost
any other CAD package — look for "export" or "save as", then choose STEP.
Everything else in this guide still works: you still pick faces by what
they look like, not by a number, and `openoptima doctor` still checks your
setup before you run anything.

**If you drew your part in inches, read this.** OpenOptima works in
millimetres throughout. A STEP file says which units it was drawn in, and
that gets converted on the way in — correctly; it has been measured. A
4 inch bracket becomes a 101.6 mm bracket, which is the same physical
part.

What that means for you: **everything you type into `project.yaml` is in
millimetres**, whatever you drew in. A load of 100 is 100 newtons. A
region box from 0 to 50 is 0 to 50 millimetres, not inches.

To check you have this right, run `openoptima doctor`. It prints the size
of the part it read:

```
The part measures 120 x 60 x 90 mm; if that is not the size you expect,
check the units your CAD package exported in.
```

If those numbers are not roughly your part, something is wrong with the
export — and you have found out in ten seconds rather than after a run.

**One thing an imported part cannot do.** A STEP file is a finished shape.
The dimensions whoever drew it typed in — this wall is 5 mm, this hole is
8 mm — are not saved in the file, only the resulting surfaces. So there is
nothing for OpenOptima to search over: `openoptima evaluate` works on an
imported part, but `openoptima optimise` has no dimensions to try. If you
want OpenOptima to search for the best size of something, that something
needs to be a design variable, and design variables are described in the
next section — which only works with OpenOptima's own shapes, not an
import, unless you add a new feature (a fillet, a hole) on top of it
yourself.

See `examples/imported_bracket` for a complete, working example.

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

**Factor of safety** is how much margin you have. **Bigger is safer.** A
factor of safety of 2.0 means the stress is half of the allowable value
you chose. A factor of safety below 1.0 means the part is overloaded —
0.64 means the part is loaded to 155% of your limit.

Watch the direction of the stress number too, because it is the opposite
way round. The limit is what the material may take; the stress is what the
part is actually being asked to take. **A higher stress is worse, not
better.** Your limit of 250 with a stress of 400 means the part is being
asked for more than you said it may give.

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

### A size marked ↓ or ↑: the answer hit your limit

Sometimes a winning design has a size sitting exactly on the smallest or
largest value you allowed. OpenOptima marks those with ↓ or ↑ in the results
table, and says so in the report.

This matters more than it looks. There is a real difference between:

- "3 mm is the best fillet radius", and
- "3 mm is the sharpest corner you let me cut, and I would have gone
  sharper."

Only the second one is true when the value is on the limit. The number was
chosen by your limit, not by the physics. **Widen the range and run it again**
to find out whether something better is sitting just outside.

If the limit is there for a good reason — the smallest radius your supplier's
cutter can make, a maximum size that has to fit in a housing — then the answer
is right, and the mark is only telling you which of your own decisions
produced it.

Watch this one especially on fillet radii. Making a part lighter means taking
material out, and an internal corner fillet is material. So the search always
pushes fillets towards the smallest you allow — which is also the corner shape
where the stress numbers are least trustworthy. See the singularity section
below.

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

### A warning if you plan to 3D print the part

**OpenOptima assumes a material that is equally strong in every
direction. A printed part is not.** A printed part is made from stacked
layers that are fused together, and it is typically 30 to 50 per cent
weaker *between* those layers than *along* them. OpenOptima cannot see
this. It can report a part as safe that will peel apart along its print
layers under a load it appeared to survive.

It also knows nothing about what a printer can make. There is no check on
overhang angles, no check that a wall is thicker than your nozzle can
produce, and no check that the part fits your build volume.

Both of these are planned — see [`roadmap.md`](roadmap.md) — but neither
exists today. Until they do, treat any result for a printed part with
real caution, and remember that fatigue is invisible as well.

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

### Vibration — the other thing a stress check cannot see

**Everything has rates it likes to vibrate at.** Flick a wine glass and it
rings at one note, not a random noise. Push a child on a swing at the
right moment each time and they go higher and higher, while pushing at the
wrong moments does almost nothing.

A part is the same. If something drives it at one of its own rates, small
pushes add up into large movements, and it can shake itself apart under a
load it would carry all day if that load were steady.

A drone arm is the clearest case. The propeller spins at a known rate. An
arm whose own rate sits near it will fail from vibration, however strong
it looks on a stress check — and the stress check will never mention it,
because nothing about a steady load reveals this.

Turn it on like this:

```yaml
modal:
  enabled: true
  modes: 6
```

OpenOptima reports **natural frequencies** in hertz — cycles per second.
The lowest one usually matters most, because it is the easiest to set off.
Keep it away from whatever drives your part:

```yaml
constraints:
  - metric: natural_frequency_hz
    operator: greater_than_or_equal
    value: 300.0
```

**Both directions are useful, which is why neither is assumed.** Staying
*above* what shakes you is the common case: make the part stiff enough
that nothing can reach its rate. Going deliberately *below* is the other:
that is how an anti-vibration mount works. OpenOptima reports the number
and you say which side of what limit it must fall.

**The load does not change the answer.** A natural frequency comes from
how stiff the part is and how heavy it is, and nothing else. The same part
under 1 N and under 5000 N gives the same frequencies — that is measured,
not assumed. So you do not need a separate load case for this.

**If the part is not properly held, OpenOptima stops.** A part that can
drift or spin freely has no natural frequency to report — it has zero, and
zero is not an answer. The solver reports that without complaint, so
OpenOptima checks for it and refuses with `model_not_held`, naming the
supports to look at. Getting a number here instead of a refusal would mean
being told the frequency of a part held in a way you never described.

**What this does not tell you**, and none of it is a small print detail:

- **How hard it shakes.** This says which rates are dangerous. It says
  nothing about how big the movement gets, because that depends on damping
  — how quickly a part bleeds off energy as it vibrates — which OpenOptima
  does not model.
- **How long it lasts.** Vibration is what causes the repeated load cycles
  that break parts by fatigue. Fatigue is not built yet.
- **The effect of a heavy load on the frequency.** Tightening a guitar
  string raises its pitch, and a part under heavy tension or compression
  shifts the same way. That needs a different calculation and is not done
  here. For most parts the shift is small; for a slender part close to
  buckling it is not.

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
| **Mode** | One of the shapes a part waves in when it vibrates. Each mode has its own frequency. |
| **Natural frequency** | A rate the part likes to vibrate at, in cycles per second. Shake it at that rate and small pushes build into big movements. |
| **NSGA-II** | The search method used. Keeps a population of designs and improves them, like breeding. |
| **Objective** | Something to push as far as you can. |
| **Pareto front** | The set of best available deals; no single winner. |
| **Percentile (99th)** | Ignore the top 1%, take the next value down. Avoids meaningless infinities. |
| **Poisson contraction** | The material narrowing sideways as it stretches lengthwise. Ordinary beam theory ignores this effect. |
| **Provenance** | The record of exactly what produced a number. |
| **Region** | A named face or set of faces you push or hold. |
| **Resonance** | What happens when something drives a part at one of its natural frequencies. Small pushes add up and the movement grows. |
| **Rigid-body mode** | The part drifting or spinning as a whole rather than bending, at zero cycles per second. It means the supports do not hold it. |
| **Second-order element** | A piece with curved edges. More accurate than straight-edged. |
| **Selector** | The description used to find a face, e.g. "biggest flat face pointing at −X". |
| **Singularity** | A place where the maths says stress is infinite. An artefact of idealised sharp corners. |
| **Smoothing** | Taking the stair-steps off a shape a topology run produced. It removes a little material, and that is always reported. |
| **Solver** | The program that does the stress calculation. |
| **Sobol / Latin hypercube** | Ways of spreading trial designs out evenly rather than randomly. |
| **Stress** | Force divided by the area carrying it. How hard the material is working. |
| **STEP file** | A finished 3D shape saved out of a CAD program, with no dimensions left inside it -- only the resulting surfaces. |
| **Topology optimisation** | Starting from a block of space and deleting whatever is not carrying its weight, so the shape is invented rather than sized. |
| **Trade rule** | Your stated exchange rate: "I will pay this much of X for that much of Y". |
| **Triangle mesh (STL)** | A shape described only as a skin of triangles. No CAD behind it, so its faces have to be measured rather than looked up. |
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

And one more, if you use `topology`: **a shape is not a result.** Pass
`--analyse`, or you have a picture and no idea whether it holds.
