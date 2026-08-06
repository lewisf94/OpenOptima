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

## Shipping a solver

The app finds CalculiX automatically if it is already installed (see
`solvers/calculix/runner.py`). To ship one instead, put `ccx.exe` and its DLLs
in `packaging/solver/` before building; the spec picks up that folder and the
runtime looks there first.

**Read this before doing so.** CalculiX is GPL-2.0-or-later. Redistributing it
is allowed, but it carries obligations — chiefly that you must make the
corresponding source available to anyone you give the binary to, and keep its
licence and copyright notices intact. `THIRD_PARTY_LICENSES.md` covers what
OpenOptima depends on; it does not discharge those obligations for a binary you
choose to ship. Get the packaging reviewed before publishing an installer that
contains a solver.

## Cross-platform note

The spec is not Windows-specific and builds on Linux and macOS too, which is how
it is tested in development. Only `build_windows.ps1` and `installer.iss` are
Windows-only.
