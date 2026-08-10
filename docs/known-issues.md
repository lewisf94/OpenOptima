# Known issues

## L-bracket and compression strut: implausible stress and displacement — FIXED

**Status:** root cause found and fixed. The FRD parser in
`solvers/calculix/frd.py` misread node values whenever a negative number
followed a positive one on the same line, on any CalculiX build that writes a
3-digit exponent (`E-003`, the Windows Fortran convention) rather than 2
(`E-03`, the Linux convention this project's fixed-width slicing was written
against). The extra exponent digit shifted every later fixed-width field on
the line, and the shifted numbers were large enough to look like an alarming
but plausible result rather than obvious garbage — the strut's `factor_of_safety`
at its smallest section came out 0.75 instead of the correct 4.77, and its
`displacement_max_mm` came out 82 mm instead of 0.29 mm.

The fix replaces the fixed-width column slicing with a pattern that finds
each number by its own shape (a sign, one digit, a decimal point, then an
exponent) rather than assuming how wide the field is. It works for either
exponent width and needs no per-platform configuration. Regression test:
`tests/unit/test_frd_parser.py::test_three_digit_exponent_does_not_corrupt_adjacent_values`.

Re-run after the fix, against the example's own documented expectation
(a factor of safety of "4.8" at the lightest section, in
`examples/strut/project.yaml`'s header comment):

| Design | Metric | Before the fix | After the fix |
|---|---|---|---|
| Strut, 30×30 mm section | `factor_of_safety` | 0.75 | **4.77** |
| Strut, 30×30 mm section | `displacement_max_mm` | 81.9 | **0.285** (hand calc: 0.286) |
| Strut, 30×30 mm section | `stress_max_mpa` | 213.7 | **33.5** (nominal: 33.3) |
| L-bracket, default thicknesses | `displacement_max_mm` | 93.9 | **3.15** |
| L-bracket, max thicknesses (20/20/20) | outcome | infeasible | **feasible**, FoS 2.70 |

Buckling factors were unaffected — they come from `dat.py`, a separate code
path — and all 83 solver-backed verification tests and 34 integration tests
pass with the fix.

### Resolved: the L-bracket's default design was genuinely infeasible

With the parsing bug fixed, the L-bracket's old default thicknesses
(10/10/8 mm) failed the 1 mm tip-deflection limit for a physically plausible
reason (3.15 mm, not the old 93.9 mm) — the default point had been chosen
before the parsing bug was found, using numbers that were themselves wrong.
The default is now 19/19/17 mm (`examples/l_bracket/project.yaml`), which
evaluates as feasible with some margin (0.37 mm deflection, factor of safety
2.30) without sitting at the top of the allowed range, so the optimiser still
has room to explore both lighter and stronger designs. The minimum/maximum
range was left unchanged.

## Windows app: every optimisation failed the moment you pressed Start — FIXED

**Status:** fixed. Reported from a real installed build.

**What went wrong.** The app installed, opened, found its solver, checked a
part — and then failed instantly on every run with:

```
PackageNotFoundError: No package metadata was found for moocore
```

Nothing was wrong with the engineering. The optimiser could not be *loaded*.
pymoo imports a package called moocore to measure how good a set of trade-offs
is, and moocore asks Python what version of itself is installed. The tool that
packages OpenOptima into a Windows program does not include the small files
that answer that question unless it is told to, so the answer was "there is no
such package" and the import died.

**Why nobody caught it.** The build already started the packaged app and
checked it answered, precisely to catch this class of problem. But starting the
app does not load the optimiser — that only happens when somebody presses
Start. So the build passed, and the first person to find out was a user.

**The fix, and the wider fix.** The packaging now includes the metadata. More
importantly, `OpenOptima.exe --self-check` imports *everything* the app loads
late — the optimiser, the mesher, the solver adapter, the geometry engine — and
the Windows build fails if any of it is missing. Anything else added later that
loads on demand must be added to that list; see `packaging/README.md`.

Verified by running a real optimisation in the installed application: 22
designs evaluated, 3 on the final trade-off menu.

## Windows installer: CalculiX was a manual prerequisite — FIXED

**Status:** fixed. First-run setup added to the desktop app.

**What went wrong.** OpenOptima builds the part and chops it into small pieces
itself, but the stress calculation is done by a separate free program called
CalculiX. The Windows app started without it and then stopped dead with
"CalculiX not found" the moment anyone pressed Start. The only way forward was
to find CalculiX on the internet, install it, and set an environment variable
called `OPENOPTIMA_CCX` — which is not a reasonable thing to ask of somebody
who has just double-clicked an installer.

**What it does now.** When no solver is found, the app opens on a setup panel
offering two routes, neither of which needs an administrator password:

- **"Install it for me"** downloads CalculiX from the CalculiX project's own
  Windows repository, checks it against a known checksum, unpacks the seven
  files the solver actually needs plus its licence, and runs it once to prove
  it works before remembering it. About 26 MB to download, roughly 10 MB on
  disk, usually under a minute.
- **"I already have it"** takes a path to an existing copy. A folder is
  enough — it looks inside for the program. Either way the choice is checked
  by actually running the program, so a copy that will not work is caught on
  the setup screen rather than halfway through a study.

The choice is remembered in a small settings file
(`%LOCALAPPDATA%\OpenOptima\settings.json`), so it is a one-off.

**Why the download rather than shipping CalculiX inside the installer.**
CalculiX is licensed under the GPL. Putting the binary in our installer would
make OpenOptima a redistributor, which carries a standing obligation to keep
the matching source code available for as long as the installer is
downloadable. Downloading it to the user's own machine, from its own home,
carries no such obligation: the file arrives exactly as it would if the user
fetched it by hand.

Bundling remains a reasonable future option for people with no internet
connection or a firewall that blocks GitHub. It is not hard — the packaging
already picks up anything placed in `packaging/solver/` — but it needs a
complete corresponding-source bundle published alongside the installer, and
that is a decision for a human. See `packaging/README.md`.

**Verifying a solver is not optional.** On Windows, `ccx.exe` is a small
program sitting beside seven runtime DLL files. A copy separated from those
DLLs exists, is the right size, and dies instantly with a Windows error code
and no message at all. Checking the file name would have left the user with a
solver that failed much later, inside a run. So both routes run the program and
ask it for its version, which takes about a second.
