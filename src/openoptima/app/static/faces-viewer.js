// The 3D face picker: turn the part, click a face, get back the description
// that finds it again -- the same work `openoptima faces` does on the
// command line, shown as a picture instead of a numbered list.
//
// A `type="module"` script, unlike app.js, because it needs `import` for
// three.js. That gives it its own top-level scope -- app.js's `state` is not
// reachable here even though both scripts run on the same page -- so the one
// thing this file needs from app.js, the open project, crosses through the
// explicit `window.OpenOptimaState` bridge at the bottom of app.js rather
// than by restructuring that file into a module for one feature's sake.
//
// This never computes an engineering number. Every geometric fact shown here
// -- what a face is, whether a description finds only the faces picked --
// comes from the server, which measures it the same way `openoptima doctor`
// does. This file only draws triangles and turns a click into a face tag.

import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const { dataset, ...properties } = props;
  const node = Object.assign(document.createElement(tag), properties);
  if (dataset) Object.entries(dataset).forEach(([key, value]) => { node.dataset[key] = value; });
  kids.flat().forEach((k) => node.append(k?.nodeType ? k : document.createTextNode(k)));
  return node;
};

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: "bad response from the app" }));
  if (!response.ok) throw new Error(data.error || `request failed (${response.status})`);
  return data;
}

// A steel-ish neutral for the part, and a warm, unambiguous highlight for
// whatever is currently selected -- deliberately far from the blue used for
// buttons and links elsewhere on the page, so "selected" never reads as
// "interactive UI element".
const BASE_COLOR = new THREE.Color(0.63, 0.67, 0.72);
const HOVER_COLOR = new THREE.Color(0.78, 0.81, 0.85);
const SELECTED_COLOR = new THREE.Color(1.0, 0.55, 0.12);

const view = {
  ready: false,
  scene: null, camera: null, renderer: null, controls: null,
  mesh: null, faceTags: [], geometry: null,
  raycaster: new THREE.Raycaster(),
  pointer: new THREE.Vector2(),
  hoveredTriangle: -1,
  selected: new Set(),
  generation: null,
  faces: [],
};

function ensureScene() {
  if (view.ready) return;
  const canvas = $("viewer-canvas");
  const stage = $("viewer-stage") || canvas.parentElement;

  view.scene = new THREE.Scene();
  // WebGL's own default is black, which reads as "broken" against the rest
  // of the page rather than as an empty stage.
  view.scene.background = new THREE.Color(
    getComputedStyle(document.body).getPropertyValue("--bg").trim() || "#f6f7f9",
  );
  view.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1e6);

  view.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  view.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  view.controls = new OrbitControls(view.camera, view.renderer.domElement);
  view.controls.enableDamping = true;
  view.controls.dampingFactor = 0.08;

  view.scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3f47, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(1, 1.6, 1.2);
  view.scene.add(key);

  const resize = () => {
    const width = stage.clientWidth || 400;
    const height = stage.clientHeight || 300;
    view.renderer.setSize(width, height, false);
    view.camera.aspect = width / Math.max(height, 1);
    view.camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(stage);
  resize();

  view.renderer.domElement.addEventListener("pointermove", onPointerMove);
  view.renderer.domElement.addEventListener("click", onClick);

  const loop = () => {
    requestAnimationFrame(loop);
    view.controls.update();
    view.renderer.render(view.scene, view.camera);
  };
  loop();

  view.ready = true;
}

function setStatus(text) {
  const node = $("viewer-status");
  node.textContent = text;
  node.classList.toggle("hidden", !text);
}

async function loadPart() {
  const project = window.OpenOptimaState?.project;
  if (!project) return;

  $("viewer-panel").classList.remove("hidden");
  ensureScene();
  setStatus("Building the part…");
  view.selected.clear();

  let data;
  try {
    data = await api("/api/faces/build", {
      path: project.path,
      variable_overrides: readVariableOverrides(),
    });
  } catch (error) {
    setStatus("Could not build the part: " + error.message);
    return;
  }

  buildGeometry(data.mesh);
  frameCamera(data.mesh.bbox);
  view.generation = data.generation;
  view.faces = data.faces;
  setStatus("");
  renderInfoPanel(null);

  const rangeNote = data.shape_can_change
    ? `Each description is checked against ${data.checked_against} other size(s) as well as this one.`
    : "This part has no dimensions to vary, so there is only one shape to check.";
  $("face-info").prepend(el("p", { className: "hint", id: "range-note" }, rangeNote));
}

function readVariableOverrides() {
  // Mirrors app.js's own variableOverrides(): read straight from the same
  // DOM inputs rather than reaching into app.js's closure, which a
  // `type="module"` script cannot do anyway.
  const overrides = {};
  document.querySelectorAll("input[data-variable]").forEach((input) => {
    const value = Number(input.value);
    if (!Number.isFinite(value)) return;
    (overrides[input.dataset.variable] ||= {})[input.dataset.bound] = value;
  });
  return overrides;
}

function buildGeometry(mesh) {
  if (view.mesh) {
    view.scene.remove(view.mesh);
    view.geometry.dispose();
    view.mesh.material.dispose();
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(mesh.positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(mesh.normals, 3));
  const colors = new Float32Array(mesh.positions.length);
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));

  const material = new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.65, metalness: 0.08, side: THREE.DoubleSide,
  });

  view.geometry = geometry;
  view.faceTags = mesh.face_tags;
  view.mesh = new THREE.Mesh(geometry, material);
  view.scene.add(view.mesh);
  paintColors();
}

function paintColors() {
  const colorAttr = view.geometry.getAttribute("color");
  for (let triangle = 0; triangle < view.faceTags.length; triangle++) {
    const tag = view.faceTags[triangle];
    const color = view.selected.has(tag)
      ? SELECTED_COLOR
      : triangle === view.hoveredTriangle
        ? HOVER_COLOR
        : BASE_COLOR;
    const base = triangle * 3;
    for (let corner = 0; corner < 3; corner++) {
      colorAttr.setXYZ(base + corner, color.r, color.g, color.b);
    }
  }
  colorAttr.needsUpdate = true;
}

function frameCamera(bbox) {
  const [xmin, ymin, zmin, xmax, ymax, zmax] = bbox;
  const center = new THREE.Vector3((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2);
  const diagonal = Math.max(
    new THREE.Vector3(xmax - xmin, ymax - ymin, zmax - zmin).length(),
    1e-6,
  );
  const distance = diagonal * 1.4;
  view.camera.near = diagonal / 1000;
  view.camera.far = diagonal * 100;
  view.camera.position.set(
    center.x + distance * 0.6, center.y + distance * 0.5, center.z + distance * 0.75,
  );
  view.camera.updateProjectionMatrix();
  view.controls.target.copy(center);
  view.controls.update();
}

function triangleUnderPointer(event) {
  if (!view.mesh) return -1;
  const rect = view.renderer.domElement.getBoundingClientRect();
  view.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  view.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  view.raycaster.setFromCamera(view.pointer, view.camera);
  const hits = view.raycaster.intersectObject(view.mesh, false);
  return hits.length ? hits[0].faceIndex : -1;
}

function onPointerMove(event) {
  const triangle = triangleUnderPointer(event);
  if (triangle === view.hoveredTriangle) return;
  view.hoveredTriangle = triangle;
  view.renderer.domElement.style.cursor = triangle >= 0 ? "pointer" : "grab";
  paintColors();
}

function onClick(event) {
  const triangle = triangleUnderPointer(event);
  if (triangle < 0) return;
  const tag = view.faceTags[triangle];
  if (event.shiftKey) {
    view.selected.has(tag) ? view.selected.delete(tag) : view.selected.add(tag);
  } else {
    view.selected = new Set(view.selected.has(tag) && view.selected.size === 1 ? [] : [tag]);
  }
  paintColors();
  describeSelection();
}

let describeToken = 0;
async function describeSelection() {
  if (!view.selected.size) return renderInfoPanel(null);
  const token = ++describeToken;
  renderInfoPanel({ loading: true });
  let result;
  try {
    result = await api("/api/faces/describe", {
      generation: view.generation,
      tags: [...view.selected],
    });
  } catch (error) {
    result = { ok: false, error: error.message };
  }
  if (token !== describeToken) return; // a later click has already superseded this one
  renderInfoPanel(result);
}

function renderInfoPanel(result) {
  const panel = $("face-info");
  const rangeNote = $("range-note");
  panel.innerHTML = "";
  if (rangeNote) panel.append(rangeNote);

  if (!result) {
    panel.append(el("p", { className: "hint" },
      "Click a face to describe it. Shift-click to add more faces to the "
      + "selection — useful for something like two bolt holes that should be "
      + "picked together."));
    return;
  }
  if (result.loading) {
    panel.append(el("p", { className: "hint" }, "Checking this selection…"));
    return;
  }
  if (!result.ok) {
    panel.append(el("div", { className: "warning" }, result.error));
    return;
  }

  panel.append(
    el("h3", {}, `${view.selected.size} face${view.selected.size === 1 ? "" : "s"} selected`),
    el("p", { className: "explanation" }, result.explanation),
  );
  result.warnings.forEach((warning) => {
    panel.append(el("div", { className: "warning" }, warning));
  });

  const nameInput = el("input", {
    type: "text", id: "region-name-input", placeholder: "name this region, e.g. mounting_face",
  });
  const yamlBlock = el("div", { className: "yaml-block" }, result.yaml);
  const copyButton = el("button", { className: "secondary", type: "button" }, "Copy YAML");

  const refreshYaml = () => {
    const name = nameInput.value.trim() || "CHANGE_ME";
    yamlBlock.textContent = result.yaml.replace("name: CHANGE_ME", `name: ${name}`);
  };
  nameInput.oninput = refreshYaml;
  copyButton.onclick = async () => {
    try {
      await navigator.clipboard.writeText(yamlBlock.textContent);
      copyButton.textContent = "Copied";
      setTimeout(() => { copyButton.textContent = "Copy YAML"; }, 1500);
    } catch {
      copyButton.textContent = "Select the text above and copy it";
    }
  };

  panel.append(
    el("label", {}, "Name for this region", nameInput),
    yamlBlock,
    el("div", { className: "actions" }, copyButton),
  );
}

$("open-viewer")?.addEventListener("click", () => { loadPart().catch(() => {}); });
