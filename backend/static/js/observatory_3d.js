// Observatory: recorded trade events only (GET /api/observatory, built from
// the event journal). Structural paths are faint dashed lines that say
// nothing about activity; observed flows are bright and sized by the real
// number of transitions in the window. Nothing moves unless events exist.
import * as THREE from "three";
import { OrbitControls } from "/static/js/vendor/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const STAGE_COLORS = { signal: 0x8b5cff, plan: 0x6d8bff, ticket: 0x5ee7ff, order: 0xffb547, fill: 0x37e39a, protection: 0x2fc7a0, reconcile: 0xff8f6b, exit: 0xe6dcff };
const hex = (n) => "#" + n.toString(16).padStart(6, "0");

const canvas = $("obsCanvas");
let renderer = null;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true }); } catch (err) { renderer = null; }
if (!renderer) $("obsFallback").hidden = false;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 400);
const HOME = new THREE.Vector3(0, 5, 34);
const CAMERA_BY_LEVEL = { agent: new THREE.Vector3(0, 5, 34), sectors: new THREE.Vector3(0, 12, 30), ticker: new THREE.Vector3(0, 2, 26) };
const TARGET = new THREE.Vector3(0, 1.5, 0);
camera.position.copy(HOME);
let controls = null;
if (renderer) {
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.minDistance = 8;
  controls.maxDistance = 80;
  controls.target.copy(TARGET);
}
scene.add(new THREE.AmbientLight(0x8877ff, 0.7));
const light = new THREE.PointLight(0xb28cff, 2.0, 90);
light.position.set(0, 12, 12);
scene.add(light);

let world = new THREE.Group();
scene.add(world);
let pickables = [];
let particles = [];

function label(text, color = "#e6dcff", scale = 1) {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 96;
  const g = c.getContext("2d");
  g.font = "600 38px 'Segoe UI', system-ui, sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.lineWidth = 7; g.strokeStyle = "rgba(4,3,12,0.95)"; g.strokeText(text, 256, 48);
  g.fillStyle = color; g.fillText(text, 256, 48);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
  sprite.scale.set(4.2 * scale, 0.8 * scale, 1);
  return sprite;
}

function clearWorld() {
  scene.remove(world);
  world.traverse((o) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
  world = new THREE.Group();
  scene.add(world);
  pickables = [];
  particles = [];
}

const size = (count) => Math.min(1.35, 0.45 + Math.log2(1 + (count || 0)) * 0.2);

function curveBetween(a, b, lift) {
  const mid = a.clone().add(b).multiplyScalar(0.5);
  mid.y += lift;
  return new THREE.QuadraticBezierCurve3(a, mid, b);
}

// ---------------------------------------------------------------- levels
function drawAgent(d) {
  const positions = new Map();
  const n = d.stages.length;
  d.stages.forEach((s, i) => {
    const t = i / (n - 1);
    const pos = new THREE.Vector3(-15 + t * 30, Math.sin(t * Math.PI) * 3.5, Math.cos(t * Math.PI) * 3);
    positions.set(s.id, pos);
    const active = s.count > 0;
    const color = STAGE_COLORS[s.id] || 0x8b5cff;
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(size(s.count), 32, 20),
      new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: active ? 0.9 : 0.08, transparent: !active, opacity: active ? 1 : 0.45 }));
    mesh.position.copy(pos);
    mesh.userData = { kind: "stage", title: s.label, text: `${s.count} recorded event(s) in the window${s.last_at ? ` · last ${new Date(s.last_at).toLocaleString()}` : ""}`, detail: s.detail };
    world.add(mesh);
    pickables.push(mesh);
    const tag = label(`${s.label} · ${s.count}`, active ? hex(color) : "#8a86b0", 1.25);
    tag.position.copy(pos).add(new THREE.Vector3(0, -(size(s.count) + 0.75), 0));
    world.add(tag);
  });
  d.structural_edges.forEach((e) => {
    const a = positions.get(e.from), b = positions.get(e.to);
    if (!a || !b) return;
    const pts = curveBetween(a, b, -1.2).getPoints(24);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineDashedMaterial({ color: 0x6b7aa8, dashSize: 0.35, gapSize: 0.3, transparent: true, opacity: 0.45 }));
    line.computeLineDistances();
    world.add(line);
  });
  d.observed_edges.forEach((e) => {
    const a = positions.get(e.from), b = positions.get(e.to);
    if (!a || !b) return;
    const curve = curveBetween(a, b, 1.6);
    const color = e.structural ? 0x5ee7ff : 0xffb547; // amber: a transition outside the designed paths
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 32, 0.05 + Math.log2(1 + e.count) * 0.05, 8, false),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 }));
    tube.userData = { kind: "edge", title: `${e.from} → ${e.to}`, text: `${e.count} observed transition(s)${e.structural ? "" : " - outside the designed paths"}` };
    world.add(tube);
    pickables.push(tube);
    if (!reduceMotion) {
      for (let k = 0; k < Math.min(6, e.count); k++) {
        const p = new THREE.Mesh(new THREE.SphereGeometry(0.12, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
        p.userData = { curve, phase: k / Math.min(6, e.count) };
        world.add(p);
        particles.push(p);
      }
    }
  });
}

function drawSectors(d, focus) {
  const sectors = focus ? d.sectors.filter((s) => s.sector === focus) : d.sectors;
  const n = Math.max(1, sectors.length);
  sectors.forEach((sec, i) => {
    const a = (i / n) * Math.PI * 2;
    const r = focus ? 0 : 11;
    const center = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
    const core = new THREE.Mesh(new THREE.SphereGeometry(size(sec.events) * (focus ? 1.6 : 1), 32, 20),
      new THREE.MeshStandardMaterial({ color: 0x6b3bff, emissive: 0x6b3bff, emissiveIntensity: 0.6 }));
    core.position.copy(center);
    core.userData = { kind: "sector", sector: sec.sector, title: sec.sector, text: `${sec.tickers.length} ticker(s), ${sec.chains} trade idea(s), ${sec.events} event(s)` };
    world.add(core);
    pickables.push(core);
    const tag = label(sec.sector, "#e6dcff", focus ? 1.2 : 0.9);
    tag.position.copy(center).add(new THREE.Vector3(0, -2.2, 0));
    world.add(tag);
    sec.tickers.forEach((t, j) => {
      const b = (j / Math.max(1, sec.tickers.length)) * Math.PI * 2;
      const orbit = focus ? 7 : 2.6;
      const pos = center.clone().add(new THREE.Vector3(Math.cos(b) * orbit, Math.sin(j * 1.3) * (focus ? 2 : 0.8), Math.sin(b) * orbit));
      const color = STAGE_COLORS[t.furthest_stage] || 0x8a86b0;
      const m = new THREE.Mesh(new THREE.SphereGeometry(focus ? size(t.events) * 0.8 : 0.32, 20, 14),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.7 }));
      m.position.copy(pos);
      m.userData = { kind: "ticker", ticker: t.ticker, title: t.ticker, text: `${t.chains} trade idea(s), furthest stage: ${t.furthest_stage || "-"}` };
      world.add(m);
      pickables.push(m);
      if (focus) { const l = label(t.ticker, hex(color), 0.7); l.position.copy(pos).add(new THREE.Vector3(0, -1.1, 0)); world.add(l); }
    });
  });
}

function drawTicker(d) {
  const chains = (d.ticker && d.ticker.chains) || [];
  const shown = chains.slice(0, 12);
  shown.forEach((chain, row) => {
    const y = 1.5 + ((shown.length - 1) / 2 - row) * 1.8;
    const evs = chain.events;
    evs.forEach((e, k) => {
      const x = -14 + (k / Math.max(1, evs.length - 1)) * 28;
      const color = STAGE_COLORS[e.stage] || 0x8a86b0;
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.28, 16, 12), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.8 }));
      m.position.set(x, y, 0);
      m.userData = { kind: "event", title: e.type, text: `${new Date(e.at).toLocaleString()} · ${e.env || "env n/a"}`, correlation: chain.correlation_id };
      world.add(m);
      pickables.push(m);
      const tag = label(e.type.replace(/^(order|ticket|signal|plan|position)\./, ""), hex(color), 1.0);
      tag.position.set(x, y - 0.55, 0);
      world.add(tag);
    });
    const name = label(chain.correlation_id, "#a9a3cf", 0.8);
    name.position.set(0, y + 0.6, 0);
    world.add(name);
    if (evs.length > 1) {
      world.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-14, y, 0), new THREE.Vector3(14, y, 0)]),
        new THREE.LineBasicMaterial({ color: 0x5ee7ff, transparent: true, opacity: 0.35 })));
    }
  });
}

// ---------------------------------------------------------------- state
const state = { level: "agent", sector: null, ticker: null, window: 24 };
let lastData = null;

function crumbs() {
  const parts = [`<button type="button" data-crumb="agent">Agent</button>`];
  if (state.level !== "agent") parts.push(`<button type="button" data-crumb="sectors">Sectors</button>`);
  if (state.sector) parts.push(`<button type="button" data-crumb="sector">${esc(state.sector)}</button>`);
  if (state.ticker) parts.push(`<span>${esc(state.ticker)}</span>`);
  $("obsCrumbs").innerHTML = parts.join(" › ");
}

function renderText(d) {
  const w = d.window_hours >= 48 ? `${Math.round(d.window_hours / 24)} days` : `${d.window_hours} hours`;
  $("obsStatus").innerHTML = d.event_count
    ? `<b>${d.event_count}</b> recorded event(s) in ${d.chain_count} trade idea(s), last ${w}${d.environments_seen.length ? ` · orders in: ${esc(d.environments_seen.join(", "))}` : ""}`
    : `<b>No recorded events in the last ${w}.</b> Nothing is animated.`;
  const list = [];
  if (state.level === "agent") {
    list.push("<h4>Stages</h4><ul>" + d.stages.map((s) => `<li>${esc(s.label)}: ${s.count}</li>`).join("") + "</ul>");
    list.push("<h4>Observed transitions</h4><ul>" + (d.observed_edges.map((e) => `<li>${esc(e.from)} → ${esc(e.to)}: ${e.count}${e.structural ? "" : " (outside designed paths)"}</li>`).join("") || "<li>none in this window</li>") + "</ul>");
  } else if (state.level === "ticker") {
    const chains = (d.ticker && d.ticker.chains) || [];
    list.push(`<h4>${esc(state.ticker)} - ${chains.length} trade idea(s)</h4><ul>` + (chains.map((c) =>
      `<li><code>${esc(c.correlation_id)}</code>: ${esc(c.stages.join(" → "))} · latest ${esc(c.latest)} · <a href="/api/events/trace/${encodeURIComponent(c.correlation_id)}" target="_blank" rel="noopener">full trace</a></li>`).join("") || "<li>none in this window</li>") + "</ul>");
  } else {
    const secs = state.sector ? d.sectors.filter((s) => s.sector === state.sector) : d.sectors;
    list.push("<h4>Sectors</h4><ul>" + (secs.map((s) => `<li>${esc(s.sector)}: ${s.tickers.map((t) => `<button type="button" data-ticker="${esc(t.ticker)}">${esc(t.ticker)}</button> (${t.chains})`).join(", ")}</li>`).join("") || "<li>no tickers in this window</li>") + "</ul>");
  }
  $("obsText").innerHTML = list.join("");
}

async function load() {
  const params = new URLSearchParams({ level: state.level, window_hours: state.window });
  if (state.sector) params.set("sector", state.sector);
  if (state.ticker) params.set("ticker", state.ticker);
  try {
    const r = await fetch(`/api/observatory?${params}`, { credentials: "same-origin" });
    const payload = await r.json();
    if (!r.ok || !payload.success) throw new Error((payload.error && payload.error.message) || "request failed");
    lastData = payload.data;
    clearWorld();
    if (state.level === "agent") drawAgent(lastData);
    else if (state.level === "ticker") drawTicker(lastData);
    else drawSectors(lastData, state.sector);
    crumbs();
    renderText(lastData);
  } catch (err) {
    $("obsStatus").innerHTML = `<b>Could not load events:</b> ${esc(err.message)}`;
  }
}

function go(level, extra = {}) {
  Object.assign(state, { level, sector: null, ticker: null }, extra);
  camera.position.copy(CAMERA_BY_LEVEL[level] || HOME);
  controls?.target.copy(TARGET);
  load();
}

$("obsCrumbs").addEventListener("click", (e) => {
  const c = e.target.closest("[data-crumb]")?.dataset.crumb;
  if (c === "agent") go("agent");
  if (c === "sectors") go("sectors");
  if (c === "sector") go("sectors", { sector: state.sector });
});
$("obsText").addEventListener("click", (e) => {
  const t = e.target.closest("[data-ticker]")?.dataset.ticker;
  if (t) go("ticker", { ticker: t, sector: state.sector });
});
$("obsWindow").addEventListener("change", (e) => { state.window = Number(e.target.value); load(); });
$("obsSectorsBtn").addEventListener("click", () => go("sectors"));

// ---------------------------------------------------------------- picking + loop
const ray = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const tip = $("obsTip");
function pick(ev) {
  const rect = canvas.getBoundingClientRect();
  pointer.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
  ray.setFromCamera(pointer, camera);
  return ray.intersectObjects(pickables, false)[0]?.object || null;
}
canvas.addEventListener("pointermove", (ev) => {
  const hit = pick(ev);
  if (!hit) { tip.hidden = true; return; }
  const u = hit.userData;
  tip.innerHTML = `<b>${esc(u.title)}</b><small>${esc(u.text || "")}</small>${u.detail ? `<small>${esc(u.detail)}</small>` : ""}`;
  const rect = canvas.getBoundingClientRect();
  tip.style.left = `${ev.clientX - rect.left}px`;
  tip.style.top = `${ev.clientY - rect.top}px`;
  tip.hidden = false;
});
canvas.addEventListener("pointerleave", () => (tip.hidden = true));
canvas.addEventListener("click", (ev) => {
  const u = pick(ev)?.userData;
  if (!u) return;
  if (u.kind === "stage") go("sectors");
  else if (u.kind === "sector") go("sectors", { sector: u.sector });
  else if (u.kind === "ticker") go("ticker", { ticker: u.ticker, sector: state.sector });
});

function resize() {
  if (!renderer) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();
const clock = new THREE.Clock();
function frame() {
  const t = clock.getElapsedTime();
  particles.forEach((p) => {
    const u = (p.userData.phase + t * 0.18) % 1;
    p.position.copy(p.userData.curve.getPoint(u));
  });
  controls?.update();
  if (renderer) renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
if (renderer) frame();
load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
