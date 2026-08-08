# Building the Windows application

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

That produces `dist\OpenOptima\OpenOptima.exe`. To make an installer, run
`iscc packaging\installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php).

Requires Python 3.11 or newer on `PATH`. Everything else is fetched into a
throwaway virtual environment, so whatever happens to be installed on the build
machine cannot leak into the build.

## What the build script does

1. Creates a clean virtual environment.
2. Runs the unit tests, and refuses to build if they fail.
3. Freezes the app with PyInstaller.
4. **Starts the built executable and calls its API**, failing the build if gmsh
   cannot be loaded from inside the bundle.

Step 4 matters more than it sounds. A frozen build that compiles cleanly and
then dies on first use is the normal failure mode here, and it is very hard to
diagnose on a user's machine. Two problems were found exactly this way during
development:

- pointing the spec at `launcher.py` produced a build that failed instantly with
  "attempted relative import with no known parent package", because PyInstaller
  runs its entry script as `__main__` with no package context. Hence
  `packaging/entry.py`;
- gmsh's native library is not discovered by following imports, so it has to be
  collected explicitly in the spec.

## Size

About 250 MB unpacked, most of it gmsh, SciPy and NumPy. This is normal for a
frozen scientific Python application. Compressed installers land near 90 MB.

UPX compression is deliberately disabled — it routinely trips antivirus
heuristics, and a false positive on a first download is worse than a larger file.

## The solver

OpenOptima needs CalculiX to work out any stresses, and does not ship it.

**What happens today.** `solvers/calculix/runner.py` looks for an existing
CalculiX first. If there is none, the desktop app shows a setup panel offering
to download one (`app/solver_setup.py`) or to use a copy the user points at.
The download comes from the CalculiX project's own Windows repository, pinned
to a commit and checked against a SHA-256 hash before anything is unpacked.

That is deliberately a **download, not a bundle**. The file arrives on the
user's machine from CalculiX's own home, exactly as it would if they fetched it
by hand, so OpenOptima is not redistributing it and takes on none of the
obligations below.

### If you ever do want to bundle it

Put `ccx.exe` and its DLLs in `packaging/solver/` before building. The spec
picks that folder up and the runtime looks there first. The files actually
needed are the eight in `_WANTED` in `app/solver_setup.py` — the solver, six
runtime DLLs and the licence. That is about 10 MB; the full CalculiX
distribution is 69 MB and most of it is the `cgx` viewer, which OpenOptima
never calls.

`LICENSE.txt` is not optional. CalculiX's licence must travel with the program.

**Read this before shipping one.** CalculiX is GPL-2.0-or-later. Redistributing
it is allowed, but it carries obligations — chiefly that you must make the
*corresponding source* available to anyone you give the binary to, and keep its
licence and copyright notices intact. "Corresponding source" means the source
for that exact build, including any patches the packager applied, not merely
whatever upstream calls 2.23. In practice that means publishing a source bundle
alongside the installer on the same download page, and keeping it there for as
long as the installer is downloadable. `THIRD_PARTY_LICENSES.md` covers what
OpenOptima itself depends on; it does not discharge those obligations for a
binary you choose to ship.

Get the packaging reviewed before publishing an installer that contains a
solver. Also note that one of the files in the upstream Windows distribution
(`README.txt`) carries a stray confidentiality header from a corporate fork in
its history. It is not needed and should not be shipped.

### The bundled-solver lookup and PyInstaller 6

PyInstaller 6 puts bundled data in an `_internal` subfolder, not next to the
executable, so `_bundled_search_paths()` checks `sys._MEIPASS` first. This is
worth knowing because getting it wrong fails silently: a shipped solver would
simply never be found, and the app would ask the user to install one it was
already carrying.

## Cross-platform note

The spec is not Windows-specific and builds on Linux and macOS too, which is how
it is tested in development. Only `build_windows.ps1` and `installer.iss` are
Windows-only.
