# PyInstaller build specification for the OpenOptima desktop app.
#
#   pip install pyinstaller
#   pyinstaller packaging/openoptima.spec
#
# Produces dist/OpenOptima/OpenOptima.exe on Windows.
#
# Two things here are load-bearing and easy to get wrong:
#
# 1. gmsh ships a native library and its own data files. PyInstaller's analysis
#    does not find them by following imports, so they are collected explicitly.
#    Without this the built app starts and then fails on the first mesh with an
#    obscure DLL error.
#
# 2. The web interface lives in openoptima/app/static. It is data, not code, so
#    it must be listed or the app serves 404s for its own page.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"

datas = [(str(ROOT / "src" / "openoptima" / "app" / "static"), "openoptima/app/static")]
datas += collect_data_files("gmsh")

# Some packages ask what version of themselves is installed, at import time,
# via importlib.metadata. PyInstaller does not bundle the .dist-info folders
# that answers that question, so the import raises PackageNotFoundError and the
# whole optimiser falls over.
#
# This shipped once. moocore, which pymoo imports for its hypervolume
# indicator, does exactly this -- and because the failure happens on the first
# real optimisation rather than at startup, the build's own smoke test walked
# straight past it. That test now imports the optimiser too; see
# packaging/build_windows.ps1.
#
# recursive=True because a dependency of a dependency can do the same thing.
for package in ("moocore", "pymoo", "pylife"):
    try:
        datas += copy_metadata(package, recursive=True)
    except Exception:  # noqa: BLE001 - an optional extra that is not installed
        pass

# Bundled examples, so a new user has something to open immediately.
#
# Every file next to project.yaml is included, not just project.yaml itself:
# examples/imported_bracket/ needs its bracket.step alongside the project
# file, and a glob for *only* project.yaml silently shipped a broken example
# whose geometry.source pointed at a file that was never packaged -- caught
# by reading this file for an unrelated reason, not by running the app,
# which is exactly trap 9's warning that starting the app proves almost
# nothing about what it can actually do.
for example_dir in (ROOT / "examples").iterdir():
    if not example_dir.is_dir() or not (example_dir / "project.yaml").is_file():
        continue
    for item in example_dir.iterdir():
        if item.name == "openoptima_work":  # run artifacts, never shipped
            continue
        if item.is_file():
            datas.append((str(item), f"examples/{example_dir.name}"))

binaries = collect_dynamic_libs("gmsh")

# rtree ships its own compiled libspatialindex, and the wall-thickness check
# reaches every spatial query in trimesh through it -- including the
# pure-Python ray engine, which builds an rtree index of triangle bounds. A
# build that bundled the Python package and not the native library would import
# fine and raise on the first measurement, which is trap 9's exact shape.
# `openoptima-app --self-check` runs a real measurement rather than an import,
# so a missing library fails the build instead of a user's first run.
try:
    binaries += collect_dynamic_libs("rtree")
except Exception:  # noqa: BLE001 - an optional extra that is not installed
    pass

# A solver placed here before building is shipped with the app. See
# packaging/README.md for the licence obligations that come with doing so.
solver = ROOT / "packaging" / "solver"
if solver.is_dir():
    for item in solver.iterdir():
        if item.is_file():
            datas.append((str(item), "solver"))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "openoptima.app.launcher",
        "openoptima.app.server",
        "openoptima.app.jobs",
        "openoptima.app.checks",
        # Reached only through create_solver / create_provider, so the import
        # graph does not lead PyInstaller to them.
        "openoptima.solvers.calculix.solver",
        "openoptima.solvers.analytic",
        "openoptima.geometry.occ.provider",
        # The optimiser is an optional extra; pymoo pulls these dynamically.
        "pymoo.algorithms.moo.nsga2",
        "pymoo.operators.sampling.lhs",
        # Printability. trimesh imports its backends lazily by name.
        "openoptima.printing.overhang",
        "rtree",
        "rtree.index",
        # The stress swing a fatigue check is built from. Reached only when a
        # project describes a load cycle, so nothing in the import graph leads
        # PyInstaller here. pyLife also imports pandas at module scope, which
        # is why `openoptima-app --self-check` measures a real reversed cycle
        # rather than only importing the module.
        "openoptima.results.fatigue",
        "pylife.stress.equistress",
        "pylife.materiallaws.woehlercurve",
        "pylife.strength.meanstress",
        "scipy.stats",
        "scipy.special",
    ],
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide6", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="OpenOptima",
    # No console window. A black box appearing behind the application is the
    # single thing that makes a packaged Python program look unfinished, and
    # the app has had its own window since it stopped opening a browser tab.
    #
    # The console used to be the only place a startup failure was visible, so
    # it is not simply switched off: a windowed build has sys.stdout set to
    # None, and `launcher._redirect_output_to_log` gives it a log file instead
    # and shows a message box naming that file if the app cannot start. Turning
    # this back on without keeping that is how a silent failure gets shipped.
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").exists() else None,
)

COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,      # UPX routinely trips antivirus heuristics; not worth it
    name="OpenOptima",
)
