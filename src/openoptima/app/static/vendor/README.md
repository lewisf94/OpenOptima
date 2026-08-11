# Vendored third-party files

The app has no build step and no bundler — every other file in `static/`
is hand-written and loaded directly by the browser. These two are the
exception, because drawing and picking triangles in 3D is a solved problem
and not one worth re-solving by hand.

- **`three.module.min.js`** — [three.js](https://threejs.org) 0.160.0, MIT
  licence (`THREE_LICENSE.txt`). Fetched via `npm view three@0.160.0` and
  copied from `node_modules/three/build/three.module.min.js` unmodified.
- **`OrbitControls.js`** — the official mouse/touch camera control from the
  same three.js release
  (`node_modules/three/examples/jsm/controls/OrbitControls.js`), copied
  unmodified. It imports from the bare specifier `"three"`, which
  `index.html` resolves with a browser-native import map — no bundler
  needed.

MIT is compatible with this project's GPL-3.0: see the "Explicitly not
planned" section of `docs/roadmap.md` for what that compatibility check
looks like when a licence is *not* compatible.

To update: `npm view three@<version>` in a scratch directory, copy the two
files above from `node_modules/three/...`, and update the version number
in this file and in `docs/capability-audit.md`.
