"""Entry point for the frozen application.

PyInstaller runs its entry script as ``__main__``, which means that script has
no package context and every relative import inside it fails with
"attempted relative import with no known parent package". Pointing the spec
straight at ``launcher.py`` therefore produces a build that compiles cleanly and
dies the moment it is run.

This shim exists so the real module is imported by its absolute name, with the
package properly established.
"""

from __future__ import annotations

import multiprocessing

from openoptima.app.launcher import main

if __name__ == "__main__":
    # Must come before anything spawns a worker, or each optimiser process
    # relaunches the whole application instead of running an evaluation.
    multiprocessing.freeze_support()
    raise SystemExit(main())
