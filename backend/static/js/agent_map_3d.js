import * as THREE from "three";
import { OrbitControls } from "/static/js/vendor/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const COLORS = { live: 0x8b5cff, support: 0xffb547, shadow: 0x6b7aa8 };
const CSS_COLORS = { live: "#8b5cff", support: "#ffb547", shadow: "#6b7aa8" };
const CATEGORY_TEXT = {
  live: "Feeds a live decision",
  support: "Reporting only",
  shadow: "Shadow-mode by design: never gates a trade",
};

const canvas = $("am3dCanvas");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
} catch (err) {
  renderer = null;
}
if (!renderer) $("am3dFallback").hidden = false;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 400);
const HOME = new THREE.Vector3(0, 13, 30);
camera.position.copy(HOME);
let controls = null;
if (renderer) {
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 10;
  controls.maxDistance = 70;
  controls.autoRotate = !reduceMotion;
  controls.autoRotateSpeed = 0.55;
  controls.target.set(0, 0, 0);
}
scene.add(new THREE.AmbientLight(0x8877ff, 0.55));
const hubLight = new THREE.PointLight(0xb28cff, 2.4, 60);
scene.add(hubLight);

// starfield
{
  const count = 900, pos = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = 55 + Math.random() * 40, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph), r * Math.sin(ph) * Math.sin(th)], i * 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0xcdbfff, size: 0.18, sizeAttenuation: true, transparent: true, opacity: 0.8 })));
}

// memory hub
const hub = new THREE.Group();
const hubCore = new THREE.Mesh(new THREE.SphereGeometry(1.5, 48, 32), new THREE.MeshStandardMaterial({ color: 0x5a2bd0, emissive: 0x6b3bff, emissiveIntensity: 0.9, roughness: 0.35 }));
const hubShell = new THREE.Mesh(new THREE.IcosahedronGeometry(2.05, 1), new THREE.MeshBasicMaterial({ color: 0xb59bff, wireframe: true, transparent: true, opacity: 0.35 }));
hub.add(hubCore, hubShell);
scene.add(hub);

function labelSprite(text, color) {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 96;
  const g = c.getContext("2d");
  g.font = "600 40px 'Segoe UI', system-ui, sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.lineWidth = 7; g.strokeStyle = "rgba(4,3,12,0.95)"; g.strokeText(text, 256, 48);
  g.fillStyle = color; g.fillText(text, 256, 48);
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 4;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  sprite.scale.set(4.4, 0.82, 1);
  return sprite;
}
const hubLabel = labelSprite("MEMORY", "#e6dcff");
hubLabel.position.set(0, 3.1, 0);
scene.add(hubLabel);

const nodes = new Map();           // id -> {group, mesh, ring, link, packets, data, base}
const pickables = [];
const packetGeo = new THREE.SphereGeometry(0.13, 10, 8);
const ripples = [];

const RING_LAYOUT = { live: { radius: 9.5, y: 2.4 }, support: { radius: 12, y: -3.8 }, shadow: { radius: 14.5, y: -0.6 } };
function layout(nodeList) {
  const byCat = { live: [], support: [], shadow: [] };
  nodeList.forEach((n) => (byCat[n.category] || byCat.shadow).push(n));
  const out = new Map();
  Object.entries(byCat).forEach(([cat, list]) => {
    const L = RING_LAYOUT[cat];
    list.forEach((n, i) => {
      const a = (i / list.length) * Math.PI * 2 + (cat === "shadow" ? 0.5 : cat === "support" ? 1.2 : 0);
      const wobble = cat === "shadow" ? Math.sin(i * 1.7) * 1.9 : 0;
      out.set(n.id, new THREE.Vector3(Math.cos(a) * L.radius, L.y + wobble, Math.sin(a) * L.radius));
    });
  });
  return out;
}

function buildNode(n, position) {
  const category = COLORS[n.category] ? n.category : "shadow";
  const color = COLORS[category];
  const group = new THREE.Group();
  group.position.copy(position);
  const geo = category === "live" ? new THREE.OctahedronGeometry(0.68) : category === "support" ? new THREE.BoxGeometry(0.8, 0.8, 0.8) : new THREE.IcosahedronGeometry(0.55, 0);
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.2, roughness: 0.4, metalness: 0.2 }));
  mesh.userData.nodeId = n.id;
  const ring = new THREE.Mesh(new THREE.RingGeometry(1.05, 1.14, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.0, side: THREE.DoubleSide }));
  ring.lookAt(camera.position);
  const label = labelSprite(n.label, CSS_COLORS[category]);
  label.position.set(0, -1.45, 0);
  group.add(mesh, ring, label);
  scene.add(group);
  const lineGeo = new THREE.BufferGeometry().setFromPoints([position, new THREE.Vector3(0, 0, 0)]);
  const link = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.18 }));
  scene.add(link);
  const packets = [0, 1, 2].map((k) => {
    const m = new THREE.Mesh(packetGeo, new THREE.MeshBasicMaterial({ color: 0x5ee7ff, transparent: true, opacity: 0.95 }));
    m.visible = false; m.userData.phase = k / 3; scene.add(m); return m;
  });
  pickables.push(mesh);
  nodes.set(n.id, { group, mesh, ring, link, packets, data: n, position, category, color });
}

function applyNode(entry, n) {
  entry.data = n;
  const active = !!n.active;
  entry.mesh.material.emissiveIntensity = active ? 1.15 : 0.16;
  entry.mesh.material.opacity = 1;
  entry.link.material.opacity = active ? 0.85 : 0.14;
  entry.link.material.color.setHex(active ? 0x5ee7ff : entry.color);
  entry.packets.forEach((p) => (p.visible = active && !reduceMotion));
  entry.ring.material.opacity = active ? 0.55 : 0;
}

function spawnRipple(position, color) {
  if (reduceMotion) return;
  const r = new THREE.Mesh(new THREE.RingGeometry(0.4, 0.5, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide }));
  r.position.copy(position); r.lookAt(camera.position); r.userData.born = performance.now();
  scene.add(r); ripples.push(r);
}

// ---------------------------------------------------------------- live data
const seenFeed = new Set();
let firstFeed = true, lastGood = null, seq = 0, applied = 0, inflight = false, failures = 0, timer = null;

function renderLegend(d) {
  const nodesData = d.nodes;
  const active = nodesData.filter((n) => n.active).length;
  $("am3dCount").textContent = active + " of " + nodesData.length + " active now";
  $("am3dLegend").innerHTML = nodesData.map((n) => {
    const cat = COLORS[n.category] ? n.category : "shadow";
    return '<li><span class="am3d-swatch" style="background:' + CSS_COLORS[cat] + (n.active ? ";box-shadow:0 0 8px " + CSS_COLORS[cat] : ";opacity:.45") + '"></span><span><b>' + esc(n.label) + "</b> " +
      (n.active ? "&middot; active" : "&middot; " + (cat === "shadow" ? "shadow-mode" : cat === "support" ? "reporting only" : "idle")) + '<br><span class="muted">' + esc(n.detail) + "</span></span></li>";
  }).join("");
  $("am3dWindow").textContent = "Live engines count as active when their signal is newer than " + d.activity_window_minutes + " minutes (calibration: " + d.calibration_window_hours + " hours). Shadow-mode and reporting-only engines never pulse by design.";
}

function renderFeed(d) {
  const rows = d.feed.slice(0, 20).map((f) => {
    const t = f.logged_at ? new Date(f.logged_at) : null;
    const time = t && !isNaN(t) ? t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "--";
    return "<li><span class='muted'>" + esc(time) + "</span><b>" + esc(f.ticker) + "</b><span>" + (f.is_placed ? "<b>PLACED</b> " : "") + esc(f.description) + "</span></li>";
  });
  $("am3dFeed").innerHTML = rows.join("") || "<li><span></span><span></span><span class='muted'>No scan decisions recorded yet for this account.</span></li>";
}

function apply(d, generatedAt) {
  lastGood = d;
  const first = nodes.size === 0;
  if (first && renderer) {
    const positions = layout(d.nodes);
    d.nodes.forEach((n) => buildNode(n, positions.get(n.id)));
  }
  d.nodes.forEach((n) => { const e = nodes.get(n.id); if (e) applyNode(e, n); });
  renderLegend(d); renderFeed(d);
  // a genuinely NEW decision in the feed sends a ripple from the scanner to the hub
  d.feed.forEach((f) => {
    const key = (f.logged_at || "") + "|" + f.ticker;
    if (!seenFeed.has(key)) {
      seenFeed.add(key);
      if (!firstFeed) { const scanner = nodes.get("scanner"); if (scanner) spawnRipple(scanner.position, f.is_placed ? 0x37e39a : 0x5ee7ff); spawnRipple(new THREE.Vector3(0, 0, 0), f.is_placed ? 0x37e39a : 0xb28cff); }
    }
  });
  firstFeed = false;
  const st = $("am3dStatus");
  st.innerHTML = '<span class="am3d-dot ' + (d.scan_is_active ? "is-live" : "") + '"></span><b>' + (d.scan_is_active ? "Active" : "Idle") + "</b> " +
    (d.scan_is_active ? "&middot; last scan decision " + esc(new Date(d.most_recent_logged_at).toLocaleTimeString()) : "&middot; no scan decision in the last " + d.activity_window_minutes + " min");
  const mem = $("am3dMemory"); mem.hidden = false;
  mem.textContent = "Memory: " + d.memory_feed_populated + " of " + d.memory_feed_total + " recent decisions carry a signal snapshot";
  $("am3dUpdated").textContent = "updated " + new Date(generatedAt).toLocaleTimeString();
  $("am3dBanner").hidden = true;
}

async function poll() {
  if (inflight) return;
  inflight = true;
  const mine = ++seq;
  let wait = document.hidden ? 45000 : 10000;
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), 12000);
  try {
    const res = await fetch("/api/agent-map", { credentials: "same-origin", headers: { Accept: "application/json" }, signal: ctrl.signal, redirect: "manual" });
    if (res.type === "opaqueredirect" || res.status === 401 || res.status === 302) throw Object.assign(new Error("Your session expired. Sign in again to keep the map live."), { auth: true });
    const json = await res.json().catch(() => null);
    if (!res.ok || !json || !json.success) throw new Error((json && json.error && (json.error.message || json.error)) || "HTTP " + res.status);
    if (mine > applied) { applied = mine; apply(json.data, json.generated_at); }
    failures = 0;
  } catch (err) {
    failures += 1;
    wait = Math.min(wait * Math.pow(2, Math.min(failures, 3)), 60000);
    const b = $("am3dBanner");
    b.hidden = false;
    b.innerHTML = esc(err.name === "AbortError" ? "The server did not answer in time." : err.message) + (lastGood ? " Showing the last data received (" + esc($("am3dUpdated").textContent) + ")." : "") + (err.auth ? ' <a href="/login">Sign in</a>' : " Retrying automatically.");
  } finally {
    clearTimeout(to); inflight = false;
    timer = setTimeout(poll, wait);
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { clearTimeout(timer); poll(); } });
poll();

// ------------------------------------------------------------ interaction
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const tip = $("am3dTip");
canvas.addEventListener("pointermove", (ev) => {
  const r = canvas.getBoundingClientRect();
  pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(pickables, false)[0];
  if (!hit) { tip.hidden = true; return; }
  const e = nodes.get(hit.object.userData.nodeId);
  const n = e.data;
  tip.hidden = false;
  tip.style.left = ev.clientX - r.left + "px"; tip.style.top = ev.clientY - r.top + "px";
  tip.innerHTML = "<b>" + esc(n.label) + "</b> &middot; " + (n.active ? "active now" : n.category === "live" ? "idle" : "dim by design") + "<small>" + esc(CATEGORY_TEXT[n.category] || "") + "</small><small>" + esc(n.detail) + "</small>";
});
canvas.addEventListener("pointerleave", () => { tip.hidden = true; });

const rotateBtn = $("am3dRotate");
function syncRotate() { rotateBtn.setAttribute("aria-pressed", String(!!(controls && controls.autoRotate))); }
rotateBtn.addEventListener("click", () => { if (controls) { controls.autoRotate = !controls.autoRotate; syncRotate(); } });
$("am3dReset").addEventListener("click", () => { if (controls) { camera.position.copy(HOME); controls.target.set(0, 0, 0); controls.update(); } });
syncRotate();

function resize() {
  if (!renderer) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (w && h) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
}
new ResizeObserver(resize).observe(canvas);
resize();

// ---------------------------------------------------------------- render loop
const clock = new THREE.Clock();
function frame() {
  const t = clock.getElapsedTime();
  if (renderer) {
    if (!reduceMotion) {
      hubShell.rotation.y = t * 0.15; hubShell.rotation.x = t * 0.08;
      hubCore.scale.setScalar(1 + Math.sin(t * 1.4) * 0.03);
    }
    nodes.forEach((e) => {
      e.ring.lookAt(camera.position);
      if (e.data.active && !reduceMotion) {
        const pulse = 1 + Math.sin(t * 3 + e.position.x) * 0.08;
        e.mesh.scale.setScalar(pulse);
        e.ring.scale.setScalar(1 + ((t * 0.6 + e.position.z) % 1) * 0.5);
        e.ring.material.opacity = 0.55 * (1 - ((t * 0.6 + e.position.z) % 1));
        e.packets.forEach((p) => { const k = (t * 0.35 + p.userData.phase) % 1; p.position.lerpVectors(e.position, new THREE.Vector3(0, 0, 0), k); p.visible = true; });
      }
      if (!reduceMotion) { e.mesh.rotation.y = t * (e.data.active ? 0.8 : 0.15); }
    });
    const now = performance.now();
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i], age = (now - r.userData.born) / 1400;
      if (age >= 1) { scene.remove(r); r.geometry.dispose(); r.material.dispose(); ripples.splice(i, 1); continue; }
      r.scale.setScalar(1 + age * 7); r.material.opacity = 0.9 * (1 - age); r.lookAt(camera.position);
    }
    if (controls) controls.update();
    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
}
frame();
