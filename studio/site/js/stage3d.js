/* Shared three.js helpers: renderer, environment light, meshopt-aware GLB loading, model normalising,
   and one material patch that adds a sparkle "dissolve", a glowing print slice line and a glow tint. */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export { THREE };

export const IS_PHONE = window.matchMedia("(max-width: 760px)").matches;

export function webglOK() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGL2RenderingContext && c.getContext("webgl2")) || !!c.getContext("webgl");
  } catch (e) { return false; }
}

export function makeRenderer(host, { dprCap = 2 } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, IS_PHONE ? 1.5 : dprCap));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.setClearColor(0x000000, 0);
  host.appendChild(renderer.domElement);
  return renderer;
}

export function makeEnvironment(renderer) {
  const pm = new THREE.PMREMGenerator(renderer);
  const env = new RoomEnvironment(renderer);
  const tex = pm.fromScene(env, 0.04).texture;
  env.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  pm.dispose();
  return tex;
}

/* Keep the canvas sized to its host. Returns a function that reports whether the size changed. */
export function sizer(renderer, camera, host) {
  let w = 0, h = 0;
  return function () {
    const nw = host.clientWidth, nh = host.clientHeight;
    if (nw === w && nh === h) return false;
    w = nw; h = nh;
    if (!w || !h) return false;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    return true;
  };
}

const loader = new GLTFLoader();
loader.setMeshoptDecoder(MeshoptDecoder);
const cache = new Map();
export function loadGLB(url) {
  if (!cache.has(url)) {
    cache.set(url, new Promise((resolve, reject) => loader.load(url, resolve, undefined, reject)));
    cache.get(url).catch(() => cache.delete(url));
  }
  return cache.get(url);
}

/* Centre on x/z and put the base on y = 0, whatever the file's own placement. Returns the size. */
export function groundModel(root) {
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const c = box.getCenter(new THREE.Vector3());
  root.position.x -= c.x; root.position.z -= c.z; root.position.y -= box.min.y;
  root.updateMatrixWorld(true);
  return box.getSize(new THREE.Vector3());
}

/* ---------- material patch ---------- */
const NOISE = /* glsl */`
float wwHash(vec3 p){ p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float wwNoise(vec3 x){
  vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(wwHash(i), wwHash(i + vec3(1,0,0)), f.x), mix(wwHash(i + vec3(0,1,0)), wwHash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(wwHash(i + vec3(0,0,1)), wwHash(i + vec3(1,0,1)), f.x), mix(wwHash(i + vec3(0,1,1)), wwHash(i + vec3(1,1,1)), f.x), f.y), f.z);
}`;

export function makeUniforms(o = {}) {
  return {
    uDissolve: { value: o.dissolve ?? 1 },
    uNoiseMatrix: { value: new THREE.Matrix4() },
    uEdge: { value: new THREE.Color(o.edge || "#B9A8FF") },
    uNoiseScale: { value: o.noiseScale ?? 0.05 },
    uSliceY: { value: -1e5 },
    uSliceOn: { value: 0 },
    uSliceColor: { value: new THREE.Color(o.slice || "#B9A8FF") },
    uSliceWidth: { value: o.sliceWidth ?? 1.6 },
    uGlow: { value: 0 },
    uGlowColor: { value: new THREE.Color(o.glow || "#7C5CFF") }
  };
}

export function patchMaterial(mat, U) {
  mat.userData.ww = U;
  mat.onBeforeCompile = (sh) => {
    for (const k in U) sh.uniforms[k] = U[k];
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 wwObj;\nvarying vec3 wwWorld;\nuniform mat4 uNoiseMatrix;")
      .replace("#include <project_vertex>", "#include <project_vertex>\nwwWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\nwwObj = (uNoiseMatrix * vec4(wwWorld, 1.0)).xyz;");
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
varying vec3 wwObj; varying vec3 wwWorld;
uniform float uDissolve; uniform vec3 uEdge; uniform float uNoiseScale;
uniform float uSliceY; uniform float uSliceOn; uniform vec3 uSliceColor; uniform float uSliceWidth;
uniform float uGlow; uniform vec3 uGlowColor;
${NOISE}`)
      .replace("#include <clipping_planes_fragment>", `#include <clipping_planes_fragment>
float wwN = wwNoise(wwObj * uNoiseScale) * 0.72 + wwNoise(wwObj * uNoiseScale * 3.3) * 0.28;
if (wwN > uDissolve) discard;`)
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
float wwE = (1.0 - smoothstep(0.0, 0.08, uDissolve - wwN)) * step(uDissolve, 0.995);
totalEmissiveRadiance += uEdge * wwE * 3.0;
float wwS = uSliceOn * (1.0 - smoothstep(0.0, uSliceWidth, abs(wwWorld.y - uSliceY)));
totalEmissiveRadiance += uSliceColor * wwS * 3.0;
totalEmissiveRadiance += uGlowColor * uGlow;`);
  };
  mat.customProgramCacheKey = () => "ww-patch-1";
  mat.needsUpdate = true;
  return mat;
}

/* ---------- screen faces ----------
   A face for gadgets whose real design has a screen; the real device shows it too.
   kind: "bot" (colour screen eyes), "pill" (a capsule icon), "oled" (mono white eyes). draw(blink, look, happy). */
function faceCanvas(kind, w, h) {
  const c = document.createElement("canvas");
  c.width = 256; c.height = Math.max(64, Math.round(256 * h / w));
  const g = c.getContext("2d");
  const draw = (blink, look, happy) => {
    const W = c.width, H = c.height;
    g.fillStyle = kind === "oled" ? "#05060A" : "#0B0F16"; g.fillRect(0, 0, W, H);
    if (kind === "pill") {
      g.save(); g.translate(W / 2, H / 2); g.rotate(-0.42);
      const L = Math.min(W * 0.62, H * 1.2), R = L * 0.22;
      g.beginPath(); g.roundRect(-L / 2, -R, L, R * 2, R); g.clip();
      g.fillStyle = "#16897E"; g.fillRect(-L / 2, -R, L / 2, R * 2);
      g.fillStyle = "#F3F6F4"; g.fillRect(0, -R, L / 2, R * 2);
      g.fillStyle = "rgba(255,255,255,.35)"; g.fillRect(-L / 2 + R * 0.5, -R * 0.62, L - R, R * 0.22);
      g.restore();
      return;
    }
    if (kind === "oled") {
      const ex = W * 0.24, ey = H * 0.5, ew = W * 0.17, eh = H * 0.5;
      g.fillStyle = "#EAF6FF"; g.strokeStyle = "#EAF6FF"; g.lineCap = "round";
      for (const s of [-1, 1]) {
        const x = W / 2 + s * ex + look * W * 0.03;
        if (happy) { g.lineWidth = H * 0.12; g.beginPath(); g.arc(x, ey + eh * 0.2, ew * 0.5, Math.PI * 1.1, Math.PI * 1.9); g.stroke(); }
        else if (blink) { g.lineWidth = H * 0.1; g.beginPath(); g.moveTo(x - ew / 2, ey); g.lineTo(x + ew / 2, ey); g.stroke(); }
        else { g.beginPath(); g.roundRect(x - ew / 2, ey - eh / 2, ew, eh, ew * 0.45); g.fill(); }
      }
      return;
    }
    const ex = W * 0.25, ey = H * 0.46, er = Math.min(W, H) * 0.17;
    for (const s of [-1, 1]) {
      const x = W / 2 + s * ex;
      if (blink || happy) {
        g.strokeStyle = "#fff"; g.lineWidth = er * 0.32; g.lineCap = "round";
        g.beginPath(); g.moveTo(x - er * 0.8, ey); g.quadraticCurveTo(x, ey + (happy ? -er : er * 0.45), x + er * 0.8, ey); g.stroke();
      } else {
        g.fillStyle = "#fff"; g.beginPath(); g.ellipse(x, ey, er * 0.82, er, 0, 0, Math.PI * 2); g.fill();
        g.fillStyle = "#1B2030"; g.beginPath(); g.ellipse(x + look * er * 0.28, ey + er * 0.12, er * 0.5, er * 0.62, 0, 0, Math.PI * 2); g.fill();
        g.fillStyle = "#fff"; g.beginPath(); g.arc(x + look * er * 0.28 - er * 0.18, ey - er * 0.14, er * 0.17, 0, Math.PI * 2); g.fill();
      }
      g.fillStyle = "rgba(255,120,150,.55)"; g.beginPath(); g.ellipse(x + s * er * 0.55, ey + er * 1.25, er * 0.42, er * 0.2, 0, 0, Math.PI * 2); g.fill();
    }
    g.strokeStyle = "#fff"; g.lineWidth = er * 0.2; g.lineCap = "round";
    g.beginPath(); g.arc(W / 2, H * 0.7, er * 0.38, 0.15 * Math.PI, 0.85 * Math.PI); g.stroke();
  };
  draw(false, 0, false);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return { tex, draw };
}

/* Find the screen in a model and put a face plane just in front of it, as a child of `parent`. */
export function addFace(model, kind, U, parent) {
  let screen = null, display = null;
  model.traverse((o) => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    if (!screen && mats.some((m) => /screen$/i.test((m && m.name) || ""))) screen = o;
    if (!display) { let p = o, hit = null; while (p && p !== model) { if (/display/i.test(p.name || "") && !/clip/i.test(p.name || "")) hit = p; p = p.parent; } if (hit) display = hit; }
  });
  if (!screen) return null;
  model.updateMatrixWorld(true);
  parent.updateMatrixWorld(true);
  const sb = new THREE.Box3().setFromObject(screen);
  const db = display ? new THREE.Box3().setFromObject(display) : sb.clone();
  const mb = new THREE.Box3().setFromObject(model);
  const ss = sb.getSize(new THREE.Vector3()), sc = sb.getCenter(new THREE.Vector3()), mc = mb.getCenter(new THREE.Vector3());
  const dims = [ss.x, ss.y, ss.z];
  const axis = dims.indexOf(Math.min(...dims));
  const key = "xyz"[axis];
  const sign = Math.sign(sc[key] - mc[key]) || 1;
  const pw = axis === 0 ? ss.z : ss.x, ph = axis === 1 ? ss.z : ss.y;
  const { tex, draw } = faceCanvas(kind, pw, ph);
  const mat = patchMaterial(new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }), U);
  const ps = new THREE.Vector3(); parent.getWorldScale(ps);
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(pw * 0.94 / ps.x, ph * 0.94 / ps.y), mat);
  const at = sc.clone();
  at[key] = (sign > 0 ? db.max[key] : db.min[key]) + sign * 0.35;
  plane.position.copy(parent.worldToLocal(at));
  if (axis === 2) plane.rotation.y = sign > 0 ? 0 : Math.PI;
  else if (axis === 0) plane.rotation.y = sign > 0 ? Math.PI / 2 : -Math.PI / 2;
  else plane.rotation.x = sign > 0 ? -Math.PI / 2 : Math.PI / 2;
  parent.add(plane);
  return { plane, tex, draw };
}

/* A soft round contact shadow drawn once on a canvas. */
let shadowTex = null;
export function contactShadow(opacity = 0.32) {
  if (!shadowTex) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d");
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, "rgba(22,19,43,1)"); grd.addColorStop(0.45, "rgba(22,19,43,.55)"); grd.addColorStop(1, "rgba(22,19,43,0)");
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    shadowTex = new THREE.CanvasTexture(c);
    shadowTex.colorSpace = THREE.SRGBColorSpace;
  }
  const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false, opacity, toneMapped: false }));
  m.rotation.x = -Math.PI / 2;
  m.renderOrder = -1;
  return m;
}

/* Run a render callback only while the host is on screen and the tab is visible. */
export function visibilityLoop(host, frame, margin = "120px") {
  let visible = false, raf = 0, last = performance.now();
  const loop = (now) => {
    raf = 0;
    if (!visible || document.hidden) return;
    const dt = Math.max(0, Math.min(0.05, (now - last) / 1000)); last = Math.max(last, now);
    frame(dt, now / 1000);
    raf = requestAnimationFrame(loop);
  };
  const start = () => { if (!raf && visible && !document.hidden) { last = performance.now(); raf = requestAnimationFrame(loop); } };
  const io = new IntersectionObserver((es) => { visible = es[es.length - 1].isIntersecting; start(); }, { rootMargin: margin });
  io.observe(host);
  document.addEventListener("visibilitychange", start);
  return { kick: start, isVisible: () => visible };
}
