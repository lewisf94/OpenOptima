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

This guide covers six commands. Run each one from your project folder.

### `openoptima faces`

```bash
openoptima faces examples/l_bracket/project.yaml
openoptima faces examples/l_bracket/project.yaml --yaml
```

**Use this when you are working out how to describe a face.** It lists
every face of your part and, for each one, the description that would find
it again:

```
  2. plane        5272.8 mm2   the flat face pointing -X
 10. cylinder      537.2 mm2   the round face about 4.5 mm in radius in
                               this part of the model
```

Add `--yaml` and it prints each description in the form you paste straight
into your project file, so you do not have to work out the wording
yourself.

**It does the hard part for you.** A description has to keep finding the
right face when the part changes shape, which is harder than it sounds —
see [the faces you push and hold](#the-faces-you-push-and-hold) below.
This builds your part at its smallest and largest allowed sizes and checks
each description still works on all of them. If a face genuinely cannot be
told apart from its neighbours, it says so rather than giving you a
description that would quietly pick the wrong one later.

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
nothing *in the file* for OpenOptima to search over. On an import by
itself, `openoptima evaluate` works and `openoptima optimise` has nothing
to try.

See `examples/imported_bracket` for a complete, working example of that.

### Giving an imported part something to change

You do not have to go back to SolidWorks. OpenOptima can add its own
corner to the shape you imported, and search for the best size of *that*.
The imported part never changes; the corner is the only thing that moves.

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
      kind: fillet
      between: [arm_top, load_face]
      size: corner_radius
```

That reads: "round off the corner where the top of the arm meets the end
face, and try radii between 2 and 16 mm."

Two kinds exist:

| `kind` | What it does | What `size` means |
|---|---|---|
| `fillet` | Rounds the corner off | The radius of the round |
| `chamfer` | Cuts the corner off flat, at 45 degrees | How far back the cut reaches |

`size` is either a plain number, or the `id` of a design variable — that
is what makes it something to optimise rather than a fixed change.

**You never say which edge.** You say which two faces the corner lies
*between*, using the names you already gave those faces. OpenOptima works
out the edge from the shape itself, every time it builds. This is the same
rule as everywhere else in the software, and here is why it matters:
adding one fillet to the example bracket **renumbered every single face of
the part**. The top of the arm went from face 5 to face 2, the loaded end
from 7 to 5, the base from 8 to 7. Anything that had remembered a number
would now be pointing at the wrong surface.

**A corner OpenOptima adds is a face you can then use.** It shows up in
the 3D picker and in `openoptima faces` like any other, so you can refine
the mesh on it or leave it out of the stress measure.

#### The thing to watch out for

A corner eats into the faces beside it. Round it enough and a face you are
loading gets small — and OpenOptima will still find it, because it is
still there.

Measured on the example bracket, where the loaded end face starts at
1140 mm²:

| Radius | What is left of the loaded face |
|---|---|
| 2 mm | 1020 mm² |
| 15 mm | 240 mm² |
| 18.9 mm | 6 mm² |
| 18.99 mm | **0.6 mm²** |
| 19 mm | the corner cannot be built at all |

At 18.99 mm the whole 2.5 kN load goes onto a strip **1900 times smaller**
than the face you clicked, at a stress to match, and nothing anywhere says
so. The part builds, meshes, solves and reports a number.

Two things protect you, and you should use both:

1. **`openoptima doctor` prints each face's area at the smallest and
   largest settings**, so you can see a face collapsing before you run
   anything. If a face changes by more than about ten times across the
   range, it says so out loud.
2. **You can set a floor.** On any region:

   ```yaml
   - name: load_face
     min_area_mm2: 300.0
     selector: ...
   ```

   Below that, the design is refused as a bad design — which is exactly
   what the optimiser needs, because it then learns to stay away. There is
   no default. The right number depends on what the face is *for*, and
   that is your decision, not the software's.

See `examples/imported_bracket_fillet` for the complete working version.

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

**You do not have to write these by hand.** Run `openoptima faces` and it
lists every face with a description that finds it, ready to paste. It also
checks each one against your part at its smallest and largest sizes, which
is the check that matters: a description can look perfectly sensible on the
part in front of you and still stop working — or start finding a different
face — once a dimension changes.

That is not hypothetical. On the example bracket, describing the two bolt
holes by their 4.5 mm radius looked right, and at the smallest fillet
setting that same description also picked up the fillet, because the fillet
had shrunk to 3 mm. Three faces where two were meant, with no error. A load
would have been applied to the fillet as though it were a bolt hole.
`openoptima faces` now catches that by trying the description on the part
at both extremes before offering it to you.

**If you would rather click than read a list, the app can show you the
part.** Open a project, and under "Check the setup" there is a **"Look at
the part and pick faces"** button. Turn the part with the mouse, click the
face you want, and it shows the same wording and the same YAML you would
get from typing `openoptima faces` — with a box to type a name and a
button to copy the result straight into your project file. Shift-click
adds more faces to the selection, for something like two bolt holes that
should be described together. It runs the exact same check against your
smallest and largest allowed sizes first, so a description you get this
way is exactly as trustworthy as one from the command line — this is a
different way of asking the same question, not a shortcut that skips the
checking.

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

### If the part is 3D printed, say so

A printed part is built from stacked layers that are fused together rather
than being one continuous lump of plastic. It is much weaker **between**
those layers than **along** them. So it has no single strength, and the
block above cannot describe it. Use `printed:` instead:

```yaml
material:
  name: PLA, printed solid
  density_kg_m3: 1240.0
  failure_criterion: hoffman

  printed:
    build_direction: [0.0, 0.0, 1.0]   # which way the layers stack

    # How stiff it is, along the layers and through them.
    along_layers_modulus_mpa: 3500.0
    through_layers_modulus_mpa: 2600.0
    in_plane_poisson: 0.36
    through_layers_poisson: 0.33
    through_layers_shear_modulus_mpa: 1100.0

    # How hard you are willing to work it, in each direction.
    strength:
      along_layers_tension_mpa: 22.0        # pulling along the layers
      through_layers_tension_mpa: 11.0      # pulling them apart — the weak one
      along_layers_compression_mpa: 30.0
      through_layers_compression_mpa: 28.0  # pressing them together — barely weaker
      in_plane_shear_mpa: 16.0
      through_layers_shear_mpa: 9.0
      basis: "measured strengths divided by a design factor of 2.5, at room temperature"
```

Everything said above about allowable stress applies to every one of these
strengths. **They are your decisions, not properties of PLA.** They depend
on your printer, your settings, and how warm the part gets in service.

`build_direction` says which way the layers stack. Laying a part flat on
the bed stacks them upward, which is `[0, 0, 1]`.

**You can also let OpenOptima decide.** Make it a design variable whose
choices are the axis the layers stack along, and the search tries each way
of laying the part on the bed:

```yaml
geometry:
  variables:
    - id: print_direction
      type: categorical
      choices: [x, y, z]     # the axis the layers stack along
      default: z

material:
  printed:
    build_direction: print_direction     # names the variable above
```

**It only ever judges the strength.** OpenOptima has no idea how much
support material an orientation needs, how long it takes to print, what
the surface comes out like, or whether the part fits your bed that way up.
If it tells you to print the part standing on end, that may be a real
nuisance the analysis never saw. It is answering "which way is strongest",
not "which way should I print this".

### Will it print, and what will it cost to print?

Two things OpenOptima can now measure about the shape itself:

```yaml
printing:
  enabled: true
  overhang_angle_deg: 45.0
  build_volume: { width_mm: 220.0, depth_mm: 220.0, height_mm: 250.0 }
```

**Overhang.** A printer lays down plastic on top of what is already
there. Where a surface leans out too far, there is nothing underneath it,
and the slicer has to print a scaffold — "support" — which you then break
off. `support_area_mm2` is how much surface needs that. The 45 degree
setting is the usual rule of thumb: a surface leaning less than 45 degrees
from horizontal needs support. Your printer may manage shallower.

**Whether it fits.** `build_volume_overflow_mm` is how far the part
sticks out past your printer, and 0 means it fits. It is a distance rather
than a yes-or-no so you can tell "5 mm too long" from "hopeless", and so
you can forbid it outright:

```yaml
constraints:
  - metric: build_volume_overflow_mm
    operator: less_than_or_equal
    value: 0.0
```

**Support is a cost, not a rule.** A design needing support is more work,
not wrong, and how much performance you would give up to avoid one is your
call. So these are numbers you may constrain, trade against mass, or
ignore. OpenOptima never quietly throws a design away for needing support.

**What is deliberately not counted.** The face resting on the print bed
looks exactly like the worst possible overhang — it points straight down
and is perfectly flat — and it needs no support at all, because the bed is
under it. So it is excluded. That is not tidying: on the drone arm,
counting it makes printing on edge look better than printing flat, and
removing it makes flat less than half the cost of on edge. The answer
reverses.

**What these numbers do not know.** They measure the shape against a
printer, and nothing about the print itself. They do not know that a short
gap can be bridged instead of supported. They do not know whether you can
reach the support to remove it — support sealed inside a hollow part is
there forever. And they say nothing about print time or surface finish. A
part needing 500 mm² of support may be easy or impossible depending on
*where* that 500 mm² is, and only you looking at the shape can tell.

**Read the answer as "not that way" rather than as a recommendation.** On
the drone arm it firmly rejects one orientation — the lightest arm it can
find printed upright is 106 g, against about 71 g the other two ways. But
between the two good orientations it genuinely cannot choose: fix each one
and run the same search, and both settle on exactly the same part. The
difference between two runs of the search is bigger than the difference
between those two orientations. So a result saying "printed on edge" means
"not upright", and does not mean on edge beats flat.

**Which way up you print it is a structural decision, and OpenOptima can
now show you what it costs.** The drone arm example is one arm of a
quadcopter, 150 mm long, bending under the motor. Printed flat, bending
stretches it along its length — the strong direction. Stand the same arm
on its end to print it and the layers stack along the arm instead, so
bending pulls them straight apart. Same shape, same load, same file, only
`build_direction` changed:

| | printed flat | printed standing up |
|---|---|---|
| Stress | 7.53 MPa | 7.54 MPa |
| Factor of safety | **3.07** | **1.55** |
| Tip movement | 1.82 mm | 2.42 mm |
| Verdict | passes | **fails** |

**Look at the stress row.** It does not move. The part is half as strong
and the stress is the same number to three figures. That is the whole
reason this exists: if you judged this part by stress alone — which is
what almost every quick check does — you would see nothing at all wrong
with the version that fails.

`failure_criterion` picks how the strengths above are compared against
what the part is actually feeling. `hoffman` is the better answer and the
default. It has one hard limit: it cannot describe a material whose weak
direction is too far below its strong one, because past that point it
stops predicting failure at all for one particular combination of
stresses, and would report an unlimited margin. OpenOptima checks this
**when it reads your file**, and refuses with a message telling you to set
`failure_criterion: max_stress` instead. It does not wait until after a
run to tell you.

In practice most real prints are fine on `hoffman`. The limit bites on the
product of tension and compression, and a print is only bad at being
pulled apart — layers press together perfectly well — so the compression
figure holds it clear. Measured on the PLA above: `hoffman` is still
accepted with through-layer tension as low as 6 MPa against 22 MPa in
plane, and refused at 5 MPa.

Two things `printed:` does **not** switch on:

- **Buckling.** OpenOptima refuses to combine the two, because the check
  that decides whether a buckling number can be trusted assumes one
  stiffness in every direction. See below.
- **Any check on whether your printer can make the shape.** Nothing looks
  at overhangs, wall thickness against nozzle size, or build volume.

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

**Not every error is worth a second try, and one is worth reading
carefully.** If the machine runs out of memory, the operating system stops
a solve outright, part way through. OpenOptima reports that as *out of
memory* rather than as a crash, and it does not retry it — the same design
meshes to the same size and runs beside the same number of other designs,
so a second attempt hits the same wall and costs another evaluation to
find out.

The fix is to run fewer designs at once. Set `parallel_jobs` in the
`optimisation:` block to something below your core count, or use a coarser
mesh. Each design being solved holds a whole meshed model in memory, so
halving the number of workers roughly halves the memory needed.

One more thing about it: **a run can show a dozen memory errors from a
single event.** When one worker is stopped, every design being solved
alongside it goes down with it. Twelve errors does not mean twelve bad
designs.

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

**Changing a limit is different, and it is handled differently.** If you
decide you want a factor of safety of 2.5 instead of 2, none of the
*numbers* change — the stress, the mass and the vibration rate of every
design are exactly what they were. Only your view of which designs are
acceptable has changed. So OpenOptima keeps the saved numbers, which saves
a great deal of time, and re-decides pass or fail against your new limit.

It is worth knowing that this used to be wrong, because it shows the shape
of the mistake. OpenOptima used to save the *verdict* along with the
numbers and hand back the old verdict. Lowering the drone arm example's
vibration limit from 195 Hz to 170 Hz meant 30 of the next run's 50
designs came back still judged against 195 — and one of them, a perfectly
good 72 g design, was reported as unacceptable while the run went on to
recommend a heavier one. Every number on screen was correct. Only the
pass-or-fail beside them was answering a question you had already changed.

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

**The strength half of this is handled now, but only if you ask for it.**
Describe the part under `printed:` and OpenOptima knows the material is
weaker between its layers than along them, and works out the factor of
safety accordingly. See ["If the part is 3D printed, say
so"](#if-the-part-is-3d-printed-say-so).

If you describe a printed part with an ordinary `allowable_stress_mpa`
instead, you get the old behaviour and the old danger: one strength in
every direction, and a part that can be reported as safe when it is about
to peel apart along its layers. OpenOptima cannot tell that you meant to
print it. Nothing in the file says so unless you say so.

**Three things are still missing, and two of them matter a lot.**

- **Wall thickness is still unchecked.** Nothing looks at whether a wall
  is thinner than your nozzle can produce. Planned — see
  [`roadmap.md`](roadmap.md). Overhangs and build-volume fit *are* now
  measured; see below.
- **Vibration cannot be trusted for a part carrying something heavy.**
  OpenOptima can work out the rates a part likes to vibrate at, but it
  cannot yet put a lump of mass on it — a motor on the end of an arm, a
  camera on a mount. That mass is usually most of what sets the answer,
  and leaving it out reports a rate roughly twice too high, in the
  direction that looks safe. This is why the drone arm example has
  vibration switched **off** rather than switched on with a caveat.
- **Fatigue is invisible**, as it is for every material here. PLA is also
  worse than metals in two ways this analysis says nothing about: it
  **creeps** — it keeps slowly deforming under a load it is holding
  steadily — and it **softens badly when warm**. A part in a hot car, in
  direct sun, or bolted next to a warm motor is not the part these numbers
  describe.

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

**Tell it about anything heavy the part carries, or the answer is wrong.**
A natural frequency comes from stiffness and mass, and on a part whose job
is to carry something, the carried thing is usually most of the mass. A
motor on the end of an arm, a camera on a mount, a battery on a tray:

```yaml
point_masses:
  - name: motor
    region: motor_pad     # the face it is bolted to
    mass_kg: 0.035
    size:                 # how big it is — see below
      shape: cylinder
      across_mm: 28.0
      height_mm: 32.0
```

Measured on the drone arm example — same arm, same everything, the only
difference being whether the 35 g motor is in the model:

| | first natural frequency |
|---|---|
| With the motor | **121.5 Hz** |
| Without it | **191.4 Hz** |

The bare number is 58% high, and high is the **reassuring** direction. If
you left the motor out and told OpenOptima to keep the arm above the
propeller's rate, it would happily hand you a design sitting right on top
of it.

### Tell it how big the motor is, not just how heavy

A motor does not sit *in* the pad it bolts to. Its weight is a couple of
centimetres above it, and it is a solid lump that resists being twisted as
well as being moved. Leave that out and the arm looks stiffer than it is.

`size` says how big it is. `height_mm` is measured straight up off the
face; `across_mm` is how wide (the diameter, for a round one). A box needs
`depth_mm` as well.

Measured on the same arm, at the section the example settles on:

| | first natural frequency |
|---|---|
| Motor flat against the pad | 169.8 Hz |
| Motor where it really sits | **165.5 Hz** |

That is only 2.5%, and it is 2.5% in the direction that flatters the part —
straight across the 170 Hz limit that example holds the arm to. It matters
more than the number suggests because **an optimiser stops right on a
limit**. That is its job. So a small error that always leans one way lands
exactly where it does harm, on every design it hands you.

Two things about `size`:

- **It is optional, and leaving it out is not silent.** Without a size the
  item is treated as flat, and both `openoptima doctor` and the result say
  so in words.
- **It is treated as a solid lump of even weight**, so its middle sits half
  way up. That is a guess, and **not necessarily a safe one**: a motor with
  the propeller on top carries its weight higher than the middle, and the
  real rate is lower than the reported one. If you know better, say
  `centre_height_mm` and put the middle where it belongs.

Two things about any carried mass:

- **It is not counted in `mass_kg`.** That figure is the mass of the part
  you print, which is what you are trying to reduce. The motor is not
  something the optimiser can make lighter.
- **It has weight as well as inertia.** If you apply an acceleration load
  — the whole thing pulling *g* in a turn, say — the carried mass pulls
  its share too.

**What this does not tell you either**, and none of it is a small print
detail:

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
| **Build direction** | Which way the layers stack in a 3D print. The weak direction, and a structural decision rather than a workshop one. |
| **Buckling** | Something long and thin suddenly folding sideways, like an empty can when you press its side. |
| **Buckling factor** | How many times your load the part takes before it folds. Below 1.0 means it folds now. |
| **Chamfer** | A corner cut off flat, at 45 degrees. OpenOptima can add one to an imported shape and vary how far back it cuts. |
| **Cache** | Saved results, reused when you ask an identical question. |
| **CalculiX** | The free program that does the actual stress calculation. |
| **Cantilever** | A beam or part fixed at one end and free at the other, like a diving board. |
| **Constraint** | A line you must not cross. |
| **Design space** | Every combination of dimensions the software may try. |
| **Design variable** | One dimension the software is allowed to change. |
| **DOE** (design of experiments) | A planned spread of trial designs, used to survey what is possible. |
| **Edge feature** | A rounded or cut-back corner OpenOptima adds on top of a shape. It is named by the two faces it lies between, never by an edge number. |
| **Element** | One small piece the part is chopped into. |
| **Factor of safety** | How much margin you have. 2.0 = stress is half the allowable. |
| **Fillet** | A rounded corner. OpenOptima can add one to an imported shape and vary its radius. |
| **Gmsh** | The free program that builds the shape and chops it into pieces. |
| **Hoffman criterion** | The sum used to judge a printed part, where one allowable stress will not do. It accounts for the material being stronger along its layers than through them, and stronger in compression than in tension. |
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
| **Printed material** | One that is weaker between its print layers than along them, so it has no single strength. Described under `printed:` rather than with one allowable stress. |
| **Overhang** | A surface leaning out so far that there is nothing underneath to print it on. Needs a temporary scaffold, called support. |
| **Support** | Scaffolding a printer builds under an overhang and you break off afterwards. Counted as an area, because it is a cost rather than a fault. |
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
