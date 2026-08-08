"use strict";

// Plain browser JavaScript, no build step and no framework. The whole point of
// this app is that it packages into a Windows executable without surprises, and
// a toolchain that has to run before it works is exactly the kind of surprise
// that bites on a machine nobody can debug.

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const { dataset, ...properties } = props;
  const node = Object.assign(document.createElement(tag), properties);
  if (dataset) Object.entries(dataset).forEach(([key, value]) => { node.dataset[key] = value; });
  kids.flat().forEach((k) => node.append(k?.nodeType ? k : document.createTextNode(k)));
  return node;
};

const state = {
  project: null, job: null, poll: null, status: null,
  solver: null, installPoll: null,
};

const api = async (path, body) => {
  const options = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: "bad response from the app" }));
  if (!response.ok) throw new Error(data.error || `request failed (${response.status})`);
  return data;
};

const num = (value, digits = 4) =>
  value === null || value === undefined || Number.isNaN(value)
    ? "—"
    : Math.abs(value) >= 1e5 || (Math.abs(value) < 1e-3 && value !== 0)
      ? value.toExponential(2)
      : Number(value.toPrecision(digits)).toString();

const METRIC_LABELS = {
  mass_kg: "Mass (kg)",
  factor_of_safety: "Safety factor",
  buckling_factor: "Buckling factor",
  displacement_max_mm: "Deflection (mm)",
  stress_max_mpa: "Stress (MPa)",
  stress_raw_max_mpa: "Peak stress (MPa)",
  volume_mm3: "Volume (mm³)",
  stiffness_n_per_mm: "Stiffness (N/mm)",
};
const label = (metric) => METRIC_LABELS[metric] || metric;

// ── environment ──────────────────────────────────────────────────────────
async function loadStatus() {
  const status = await api("/api/status");
  state.status = status;
  const versions = status.versions || {};
  $("env").innerHTML = "";
  $("env").append(
    el("div", {}, `${status.cores} cores · gmsh ${versions.gmsh || "?"}`),
    status.solver_available
      ? el("div", {}, `Solver ready`)
      : el("div", { className: "bad" }, "No stress solver — set one up below")
  );
}

// ── stress solver setup ──────────────────────────────────────────────────
// Without a solver the app can build a part but cannot work out a single
// stress, so this offers the two ways out: point at an existing copy, or let
// OpenOptima fetch one.

const INSTALL_STAGES = {
  starting: "Getting ready…",
  downloading: "Downloading CalculiX…",
  unpacking: "Unpacking…",
};

async function loadSolver() {
  state.solver = await api("/api/solver");
  renderSolver();
  if (state.solver.available) return;
  // An install already under way survives a page reload, so pick it back up
  // rather than showing an idle button next to a running download.
  const progress = await api("/api/solver/install").catch(() => null);
  if (progress && progress.state === "running") {
    markInstalling();
    pollInstall();
  }
}

function renderSolver() {
  const s = state.solver;
  const section = $("step-solver");
  const body = $("solver-body");
  body.innerHTML = "";

  if (s.available) {
    // A solver found on its own needs no explaining. Only show the panel when
    // the user picked this one, so they have a way to undo that choice.
    if (!s.chosen_by_user) { section.classList.add("hidden"); return; }
    section.classList.remove("hidden");
    const change = el("button", { className: "secondary", type: "button" }, "Use a different one");
    change.onclick = async () => {
      change.disabled = true;
      state.solver = await api("/api/solver/forget", {});
      await loadStatus();
      renderSolver();
    };
    body.append(
      el("div", { className: "banner good" },
        el("b", {}, "Ready. "),
        s.version ? `CalculiX ${s.version} is doing the stress calculations.`
          : "CalculiX is doing the stress calculations."),
      el("p", { className: "path" }, s.path),
      el("div", { className: "actions" }, change));
    return;
  }

  section.classList.remove("hidden");
  body.append(el("div", { className: "banner bad" },
    el("b", {}, "OpenOptima cannot work out any stresses yet. "),
    "It builds the part and chops it into small pieces itself, but the stress "
    + "calculation is done by a separate free program called CalculiX. "
    + "Either way below will fix it, and it is a one-off."));

  const choices = el("div", { className: "choices" });

  const d = s.download || {};
  const fetchIt = el("div", { className: "choice" }, el("h3", {}, "Install it for me"));
  if (s.can_install) {
    const button = el("button", { className: "primary", type: "button", id: "install-start" },
      "Install it for me");
    button.onclick = () => startInstall();
    fetchIt.append(
      el("p", { className: "hint" },
        `Downloads CalculiX ${d.version || ""} (about ${d.megabytes || "25"} MB) from the `
        + "CalculiX project itself and keeps it in OpenOptima's own folder. Nothing else "
        + "on this computer is changed and no administrator password is needed. "
        + "Usually under a minute."),
      el("div", { className: "actions" }, button),
      el("div", { id: "install-progress", className: "hidden" },
        el("div", { className: "meter" }, el("div", { id: "install-fill" })),
        el("p", { className: "hint", id: "install-note" }, "")),
      el("p", { className: "hint" },
        "CalculiX is free software under the GPL licence. Its source code is at ",
        el("a", { href: d.source || "https://github.com/calculix",
          target: "_blank", rel: "noreferrer" }, d.source || "github.com/calculix"), "."));
  } else {
    fetchIt.append(el("p", { className: "hint" }, s.install_note || s.message || ""));
  }
  choices.append(fetchIt);

  const input = el("input", { type: "text", id: "solver-path",
    placeholder: "C:\\CalculiX\\bin\\ccx.exe" });
  const locate = el("button", { className: "secondary", type: "button" }, "Use this one");
  locate.onclick = async () => {
    const target = $("locate-result");
    target.innerHTML = "";
    const path = input.value.trim();
    if (!path) {
      target.append(el("div", { className: "banner bad" }, "Type or paste the location first."));
      return;
    }
    locate.disabled = true;
    try {
      state.solver = await api("/api/solver/locate", { path });
      await loadStatus();
      renderSolver();
    } catch (error) {
      target.append(el("div", { className: "banner bad" }, error.message));
      locate.disabled = false;
    }
  };
  choices.append(el("div", { className: "choice" },
    el("h3", {}, "I already have it"),
    el("p", { className: "hint" },
      "Paste where CalculiX is. The folder is enough — OpenOptima will look inside "
      + "it for the program. It is checked by actually running it, so a copy that "
      + "will not work is caught now rather than halfway through a run."),
    el("div", { className: "row" }, input, locate),
    el("div", { id: "locate-result" })));

  body.append(choices);
}

function markInstalling() {
  const button = $("install-start");
  if (button) { button.disabled = true; button.textContent = "Installing…"; }
  const progress = $("install-progress");
  if (progress) progress.classList.remove("hidden");
}

async function startInstall() {
  markInstalling();
  try {
    await api("/api/solver/install", {});
  } catch (error) {
    // Already running is not a problem — follow the one that exists.
    if (!/already running/i.test(error.message)) return showInstallError(error.message);
  }
  pollInstall();
}

function pollInstall() {
  clearInterval(state.installPoll);
  state.installPoll = setInterval(async () => {
    let progress;
    try {
      progress = await api("/api/solver/install");
    } catch {
      return; /* transient; the next tick will retry */
    }
    if (progress.state === "running") return showInstallProgress(progress);
    clearInterval(state.installPoll);
    if (progress.state === "error") return showInstallError(progress.message);
    if (progress.state === "done") {
      state.solver = await api("/api/solver");
      await loadStatus();
      renderSolver();
    }
  }, 700);
}

function showInstallProgress(progress) {
  const percent = Math.round((progress.fraction || 0) * 100);
  const fill = $("install-fill");
  if (fill) fill.style.width = percent + "%";
  const note = $("install-note");
  if (note) {
    note.textContent = (INSTALL_STAGES[progress.stage] || "Working…")
      + (progress.stage === "downloading" ? ` ${percent}%` : "");
  }
}

function showInstallError(message) {
  const button = $("install-start");
  if (button) { button.disabled = false; button.textContent = "Try again"; }
  const fill = $("install-fill");
  if (fill) fill.style.width = "0%";
  const note = $("install-note");
  if (note) {
    note.textContent = "";
    note.append(el("span", { className: "bad" }, message));
  }
}

// ── 1. choose a part ─────────────────────────────────────────────────────
async function loadProjects() {
  const { projects } = await api("/api/projects");
  const list = $("project-list");
  list.innerHTML = "";
  if (!projects.length) {
    list.append(el("p", { className: "hint" },
      "No parts found. Use the box below to open a project file."));
    return;
  }
  projects.forEach((p) => {
    const card = el("button", { className: "card", type: "button" },
      el("b", {}, p.name), el("span", {}, p.description || p.path));
    card.onclick = () => {
      [...list.children].forEach((c) => c.classList.remove("selected"));
      card.classList.add("selected");
      openProject(p.path);
    };
    list.append(card);
  });
}

async function openProject(path) {
  try {
    state.project = await api("/api/open", { path });
  } catch (error) {
    alert(error.message);
    return;
  }
  renderProject();
  $("step-review").classList.remove("hidden");
  $("step-run").classList.remove("hidden");
  $("step-results").classList.add("hidden");
  $("doctor-result").innerHTML = "";
  $("budget").value = state.project.budget || 48;
  updateTimeHint();
  $("step-review").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderProject() {
  const p = state.project;
  const body = $("project-summary");
  body.innerHTML = "";

  body.append(el("p", {}, el("b", {}, p.name), p.description ? " — " + p.description : ""));

  const sizes = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "What can change"), el("th", { className: "num" }, "Smallest"),
      el("th", { className: "num" }, "Largest"), el("th", {}, "Units"))),
    el("tbody", {}, p.variables.map((v) =>
      el("tr", {},
        el("td", {}, v.label),
        el("td", { className: "num" }, el("input", { type: "number", value: v.minimum,
          step: "any", dataset: { variable: v.id, bound: "minimum" }, "aria-label": `${v.label} minimum` })),
        el("td", { className: "num" }, el("input", { type: "number", value: v.maximum,
          step: "any", dataset: { variable: v.id, bound: "maximum" }, "aria-label": `${v.label} maximum` })),
        el("td", {}, v.unit || ""))))
  );
  body.append(el("h3", {}, "Sizes the software may change"), sizes,
    el("p", { className: "hint" }, "Edit these limits to define the designs this run may explore. The project file is not changed."));

  const rules = el("ul", {}, p.constraints.map((c) => el("li", {}, c)));
  body.append(
    el("h3", {}, "Rules it must not break"),
    p.constraints.length ? rules : el("p", { className: "hint" }, "None set."),
    el("h3", {}, "What it is trying to do"),
    el("ul", {}, p.objectives.map((o) =>
      el("li", {}, `${o.direction === "minimise" ? "Make as small as possible" : "Make as large as possible"}: ${o.label}`)))
  );

  const m = p.material;
  body.append(el("h3", {}, "Material"),
    el("dl", { className: "kv" },
      el("dt", {}, "Name"), el("dd", {}, m.name),
      el("dt", {}, "Working limit"), el("dd", {}, `${num(m.allowable_stress_mpa)} MPa`),
      el("dt", {}, "Chosen because"), el("dd", {}, m.basis)));

  if (p.buckling.enabled) {
    body.append(el("div", { className: "banner good" },
      "Buckling is being checked — good, this is the failure a stress check cannot see."));
  } else {
    body.append(el("div", { className: "banner warn" },
      "Buckling is not being checked. If this part is long and thin, it could fold "
      + "up long before the stress limit is reached."));
  }
}

function variableOverrides() {
  const overrides = {};
  document.querySelectorAll("input[data-variable]").forEach((input) => {
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error(`Enter a number for ${input.dataset.variable}.`);
    (overrides[input.dataset.variable] ||= {})[input.dataset.bound] = value;
  });
  for (const [id, range] of Object.entries(overrides)) {
    if (range.minimum > range.maximum) throw new Error(`The minimum for ${id} is above its maximum.`);
  }
  return overrides;
}

// ── 2. check ─────────────────────────────────────────────────────────────
$("run-doctor").onclick = async () => {
  const button = $("run-doctor");
  button.disabled = true;
  button.textContent = "Checking…";
  const target = $("doctor-result");
  target.innerHTML = "";
  try {
    const report = await api("/api/doctor", {
      path: state.project.path, variable_overrides: variableOverrides(),
    });
    target.append(el("div", { className: "banner " + (report.ok ? "good" : "bad") },
      report.ok
        ? "All checks passed. This part is ready to run."
        : "Something needs fixing before this will give trustworthy answers."));
    report.checks.forEach((c) => {
      target.append(el("div", { className: "check " + (c.ok ? "ok" : "bad") },
        el("span", { className: "mark" }, c.ok ? "✓" : "✗"),
        el("div", {}, el("div", {}, c.name),
          el("small", {}, c.detail || ""),
          c.fix ? el("small", {}, "→ " + c.fix) : "")));
    });
    if (report.probes.length) {
      target.append(el("h3", {}, "Built at three sizes"),
        el("div", { className: "scroller" }, el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "Size"), el("th", { className: "num" }, "Mass (kg)"),
            el("th", {}, "Surfaces found"))),
          el("tbody", {}, report.probes.map((probe) =>
            el("tr", {},
              el("td", {}, probe.label),
              el("td", { className: "num" }, num(probe.mass_kg, 3)),
              el("td", {}, probe.error
                ? probe.error
                : probe.regions.map((r) => `${r.name} (${r.faces})`).join(", "))))))));
    }
  } catch (error) {
    target.append(el("div", { className: "banner bad" }, error.message));
  } finally {
    button.disabled = false;
    button.textContent = "Check this part";
  }
};

// ── 3. run ───────────────────────────────────────────────────────────────
function updateTimeHint() {
  const budget = Number($("budget").value) || 0;
  const cores = state.status?.cores || 1;
  const seconds = (budget * 4) / cores;
  const minutes = Math.max(1, Math.round(seconds / 60));
  $("time-hint").textContent =
    `Roughly ${minutes} minute${minutes === 1 ? "" : "s"} on this machine, `
    + `at about 4 seconds per design across ${cores} cores. You can stop at any time `
    + `and keep what has been found so far.`;
}
$("budget").oninput = updateTimeHint;

$("start").onclick = async () => {
  try {
    const job = await api("/api/run", {
      path: state.project.path,
      kind: $("kind").value,
      budget: Number($("budget").value) || null,
      variable_overrides: variableOverrides(),
    });
    state.job = job;
    $("progress").classList.remove("hidden");
    $("step-results").classList.add("hidden");
    $("start").disabled = true;
    $("stop").classList.remove("hidden");
    $("log").innerHTML = "";
    startPolling();
  } catch (error) {
    alert(error.message);
  }
};

$("stop").onclick = async () => {
  if (!state.job) return;
  $("stop").disabled = true;
  await api(`/api/job/${state.job.id}/stop`, {});
};

function startPolling() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    try {
      const job = await api(`/api/job/${state.job.id}`);
      renderProgress(job);
      if (job.state !== "running") {
        clearInterval(state.poll);
        $("start").disabled = false;
        $("stop").classList.add("hidden");
        $("stop").disabled = false;
        renderResults(job);
      }
    } catch {
      /* transient; the next tick will retry */
    }
  }, 900);
}

let renderedLines = 0;
function renderProgress(job) {
  $("progress-count").textContent = job.evaluated;
  // The optimiser explores before it hunts, so the total can exceed the target.
  // Saying "22 of 16" would look like a bug; it is simply how the search works.
  $("progress-total").textContent = job.budget ? `target ${job.budget}` : "";
  $("progress-state").textContent = job.state === "running" ? "running…" : job.state;
  $("meter-fill").style.width =
    Math.min(100, job.budget ? (100 * job.evaluated) / job.budget : 0) + "%";

  const tally = { ok: 0, infeasible: 0, error: 0 };
  job.progress.forEach((line) => { tally[line.outcome] = (tally[line.outcome] || 0) + 1; });
  $("tally-ok").textContent = tally.ok || 0;
  $("tally-no").textContent = tally.infeasible || 0;
  $("tally-err").textContent = tally.error || 0;

  const log = $("log");
  job.progress.slice(renderedLines).forEach((line) => {
    log.append(el("div", { className: line.outcome },
      `${String(line.n).padStart(4)}  ${line.outcome.padEnd(10)} ${line.message}`
      + (line.cached ? "  (reused)" : "")));
  });
  renderedLines = job.progress.length;
  log.scrollTop = log.scrollHeight;
}

// ── 4. results ───────────────────────────────────────────────────────────
function renderResults(job) {
  const body = $("results-body");
  body.innerHTML = "";
  $("step-results").classList.remove("hidden");
  renderedLines = 0;

  if (job.state === "failed") {
    body.append(el("div", { className: "banner bad" }, "The run failed. " + job.error));
    return;
  }
  if (job.state === "cancelled") {
    body.append(el("div", { className: "banner warn" }, job.error));
  }

  const s = job.summary || {};
  body.append(el("div", { className: "banner " + (job.front.length ? "good" : "warn") },
    job.front.length
      ? `Tried ${s.evaluated} designs. ${job.front.length} of them are worth considering — `
        + `none is better than another in every way, so the choice is yours.`
      : `Tried ${s.evaluated} designs and none met all the rules. Either the rules `
        + `cannot be met within the sizes allowed, or the size range is too narrow.`));

  if (s.errors) {
    body.append(el("div", { className: "banner warn" },
      `${s.errors} design${s.errors === 1 ? "" : "s"} could not be worked out at all `
      + `(as opposed to being rejected). That usually means a setup problem rather `
      + `than a bad design.`));
  }

  if (!job.front.length) { addReportLink(body, job); return; }

  // Chart, when there are exactly two things being traded off.
  const objectives = job.front[0].objectives;
  if (objectives.length === 2) body.append(chart(job, objectives));

  // The table.
  const metrics = Object.keys(job.front[0].metrics)
    .filter((m) => ["mass_kg", "factor_of_safety", "buckling_factor",
                    "displacement_max_mm", "stress_max_mpa"].includes(m));
  const sorted = [...job.front].sort(
    (a, b) => a.objectives[0].value - b.objectives[0].value);

  body.append(el("h3", {}, "Your options"),
    el("div", { className: "scroller" }, el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, ""), ...metrics.map((m) => el("th", { className: "num" }, label(m))),
        ...state.project.variables.map((v) => el("th", { className: "num" }, v.label)))),
      el("tbody", {}, sorted.map((entry) => {
        const isKnee = entry.run_id === job.highlights.knee_run_id;
        const isTrade = entry.run_id === job.highlights.trade_rule_run_id;
        const note = isKnee ? "best balance" : isTrade ? "your trade rule" : "";
        return el("tr", { className: isKnee ? "highlight" : "" },
          el("td", {}, note),
          ...metrics.map((m) => el("td", { className: "num" }, num(entry.metrics[m]))),
          ...state.project.variables.map((v) =>
            el("td", { className: "num" }, num(entry.design[v.id], 4))));
      })))));

  if (job.highlights.knee_run_id) {
    body.append(el("p", { className: "hint" },
      "The highlighted row is the best balance — the point where paying more stops "
      + "buying much. It is a suggestion, not a decision."));
  }

  // What each step costs.
  if (job.trade_offs.length) {
    const t = job.trade_offs[0];
    body.append(el("h3", {}, "What each step up the list costs you"),
      el("div", { className: "scroller" }, el("table", {},
        el("thead", {}, el("tr", {},
          el("th", { className: "num" }, `Extra ${t.give_label.toLowerCase()}`),
          el("th", { className: "num" }, `Change in ${t.gain_label.toLowerCase()}`),
          el("th", { className: "num" }, "Cost per unit"))),
        el("tbody", {}, job.trade_offs.map((row) =>
          el("tr", {},
            el("td", { className: "num" }, num(row.give_delta, 3)),
            el("td", { className: "num" }, num(row.gain_delta, 3)),
            el("td", { className: "num" }, row.rate === null ? "—" : num(Math.abs(row.rate), 3))))))),
      el("p", { className: "hint" },
        "Read down the last column. Where the cost per unit jumps, you have passed "
        + "the point of good value."));
  }

  const warned = job.front.filter((f) => f.warnings.length);
  if (warned.length) {
    body.append(el("h3", {}, "Worth reading before you trust these"),
      el("ul", {}, warned.slice(0, 5).flatMap((f) =>
        f.warnings.slice(0, 2).map((w) => el("li", {}, w)))));
  }

  addReportLink(body, job);
}

function addReportLink(body, job) {
  if (!job.report_path) return;
  const button = el("button", { className: "secondary", type: "button" }, "Show the full report");
  button.onclick = async () => {
    const response = await fetch("/api/report", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: job.report_path }),
    });
    const text = await response.text();
    const window_ = window.open("", "_blank");
    window_.document.write("<pre style='white-space:pre-wrap;font:13px/1.5 ui-monospace,monospace;padding:2rem;max-width:60rem;margin:auto'>"
      + text.replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[c]) + "</pre>");
  };
  body.append(el("div", { className: "actions" }, button,
    el("span", { className: "hint" }, "Saved at " + job.report_path)));
}

function chart(job, objectives) {
  const W = 720, H = 280, pad = { l: 62, r: 20, t: 16, b: 44 };
  const points = job.front.map((f) => ({
    x: f.objectives[0].value, y: f.objectives[1].value, run: f.run_id,
  }));
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const span = (values) => {
    const lo = Math.min(...values), hi = Math.max(...values);
    const margin = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 1;
    return [lo - margin, hi + margin];
  };
  const [x0, x1] = span(xs), [y0, y1] = span(ys);
  const sx = (v) => pad.l + ((v - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (v) => H - pad.b - ((v - y0) / (y1 - y0 || 1)) * (H - pad.t - pad.b);

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  const add = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  };

  add("line", { class: "axis", x1: pad.l, y1: H - pad.b, x2: W - pad.r, y2: H - pad.b });
  add("line", { class: "axis", x1: pad.l, y1: pad.t, x2: pad.l, y2: H - pad.b });
  [0, 0.5, 1].forEach((f) => {
    add("text", { x: pad.l + f * (W - pad.l - pad.r), y: H - pad.b + 16, "text-anchor": "middle" },
      num(x0 + f * (x1 - x0), 3));
    add("text", { x: pad.l - 8, y: H - pad.b - f * (H - pad.t - pad.b) + 4, "text-anchor": "end" },
      num(y0 + f * (y1 - y0), 3));
  });
  add("text", { x: (W + pad.l) / 2, y: H - 6, "text-anchor": "middle" }, objectives[0].label);
  add("text", { x: 14, y: H / 2, "text-anchor": "middle",
    transform: `rotate(-90 14 ${H / 2})` }, objectives[1].label);

  const ordered = [...points].sort((a, b) => a.x - b.x);
  add("polyline", {
    points: ordered.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" "),
    fill: "none", stroke: "var(--line)", "stroke-width": 2,
  });
  points.forEach((p) => {
    const dot = add("circle", {
      class: "dot" + (p.run === job.highlights.knee_run_id ? " knee" : ""),
      cx: sx(p.x), cy: sy(p.y), r: p.run === job.highlights.knee_run_id ? 7 : 5,
    });
    add("title", {}, `${objectives[0].label} ${num(p.x)} · ${objectives[1].label} ${num(p.y)}`);
    dot.append(svg.lastChild);
  });

  const wrap = el("div", {},
    el("h3", {}, "The trade-off"),
    el("p", { className: "hint" },
      "Every dot is a design worth considering. Moving along the line, you give up "
      + "one thing to gain the other. The green dot is the best balance."));
  wrap.append(svg);
  return wrap;
}

$("open-custom").onclick = () => {
  const path = $("custom-path").value.trim();
  if (path) openProject(path);
};

loadStatus().catch(() => {});
loadSolver().catch(() => {});
loadProjects().catch((error) => {
  $("project-list").textContent = "Could not list parts: " + error.message;
});
