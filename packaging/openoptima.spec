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

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"

datas = [(str(ROOT / "src" / "openoptima" / "app" / "static"), "openoptima/app/static")]
datas += collect_data_files("gmsh")

# Bundled examples, so a new user has something to open immediately.
for example in (ROOT / "examples").glob("*/project.yaml"):
    datas.append((str(example), f"examples/{example.parent.name}"))

binaries = collect_dynamic_libs("gmsh")

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
    console=True,   # keep the console: it shows the URL and any startup error
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
