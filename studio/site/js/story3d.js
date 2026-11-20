/* "How it works" 3D stage. One scene, driven only by scroll progress s (0..6, one unit per step), so it
   scrubs both ways:
   Imagine (stage empty, wish bubble in the DOM) -> Engineer (the real bedtime-robot assembly flies in
   exploded with labels, then scrubs together, wireframe flash) -> Manufacture (the shell prints layer by
   layer under a nozzle) -> Receive (a gift box drops on, the lid lifts, the walls fold open) -> Modify
   (happy bounce, the light turns purple) -> Deploy (gentle wiggle while the DOM ring fills and rewinds). */
import { THREE, makeRenderer, makeEnvironment, sizer, loadGLB, groundModel, makeUniforms, patchMaterial, visibilityLoop, addFace } from "./stage3d.js";

/* Plain-word labels, used only when assets/3d/assembly.json is missing. Node names are the engine's. */
const FALLBACK = {
  PRINT_Shell: ["print", "Printed shell", 1],
  PRINT_RearCover: ["print", "Back cover", 2],
  PURCHASE_Speaker: ["purchase", "Speaker", 3],
  PURCHASE_Button: ["purchase", "Big story button", 4],
  PURCHASE_Display: ["purchase", "Screen for the eyes", 5],
  PURCHASE_Led: ["purchase", "Colour light", 6],
  PURCHASE_Microphone: ["purchase", "Microphone", 7],
  PURCHASE_Backplane: ["pcb", "Circuit board", 8],
  PURCHASE_CoreBoard: ["pcb", "ESP32-S3 board", 9],
  PURCHASE_Amplifier: ["pcb", "Sound amplifier", 10],
  PURCHASE_MicroSdModule: ["pcb", "Memory card reader", 11]
};
const MAX_LABELS = 9;
/* Which parts get a label, in this order of preference (others follow by build order). */
const LABEL_FIRST = ["PRINT_Shell", "PRINT_RearCover", "PURCHASE_Speaker", "PURCHASE_Button", "PURCHASE_Display", "PURCHASE_Led",
  "PURCHASE_Microphone", "PURCHASE_Backplane", "PURCHASE_CoreBoard", "PURCHASE_Amplifier", "PURCHASE_MicroSdModule"];

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const easeOut = (x) => 1 - Math.pow(1 - x, 3);
const bounceOut = (x) => {
  const n = 7.5625, d = 2.75;
  if (x < 1 / d) return n * x * x;
  if (x < 2 / d) return n * (x -= 1.5 / d) * x + 0.75;
  if (x < 2.5 / d) return n * (x -= 2.25 / d) * x + 0.9375;
  return n * (x -= 2.625 / d) * x + 0.984375;
};

function kindOf(name) {
  if (/^PRINT_/i.test(name)) return "print";
  if (/backplane|coreboard|amplifier|microsd|pcb/i.test(name)) return "pcb";
  if (/cable/i.test(name)) return "cable";
  if (/screw|insert|feet|fastener/i.test(name)) return "fastener";
  return "purchase";
}

export async function createStory(host, labelLayer, onEvent = () => {}) {
  const [gltf, meta] = await Promise.all([
    loadGLB("assets/3d/assembly.glb"),
    fetch("assets/3d/assembly.json").then((r) => (r.ok ? r.json() : null)).catch(() => null)
  ]);

  const renderer = makeRenderer(host, { dprCap: 1.75 });
  renderer.localClippingEnabled = true;
  const scene = new THREE.Scene();
  scene.environment = makeEnvironment(renderer);
  scene.add(new THREE.HemisphereLight(0xe2dcff, 0x221c44, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 1.7); key.position.set(320, 520, 420); scene.add(key);
  const rim = new THREE.DirectionalLight(0x9c84ff, 2.2); rim.position.set(-420, 260, -320); scene.add(rim);
  const warm = new THREE.DirectionalLight(0xffd7a8, 0.5); warm.position.set(420, 80, -200); scene.add(warm);
  /* the "new light colour" in Modify: a purple glow in front of the face (no falloff, limited reach) */
  const purple = new THREE.PointLight(0x8b5cff, 0, 420, 0);
  scene.add(purple);
  const camera = new THREE.PerspectiveCamera(30, 1, 5, 9000);
  const resize = sizer(renderer, camera, host);

  /* ---------- the assembly ---------- */
  const root = gltf.scene;
  let size = groundModel(root);
  if (Math.max(size.x, size.y, size.z) < 2) { root.scale.multiplyScalar(1000); size = groundModel(root); }
  const rig = new THREE.Group();
  rig.add(root);
  scene.add(rig);
  const H = size.y;
  const C = new THREE.Vector3(0, H / 2, 0);

  let level = root;
  while (level.children.length === 1 && level.children[0].children.length > 0) level = level.children[0];
  const metaParts = new Map(((meta && meta.parts) || []).map((p) => [p.node, p]));

  const UA = makeUniforms({ edge: "#B9A8FF", noiseScale: 0.045 });            // the main shell (it prints)
  const UB = makeUniforms({ edge: "#B9A8FF", noiseScale: 0.045 });            // every other part
  UA.uSliceY = UB.uSliceY; UA.uSliceOn = UB.uSliceOn;
  const UG = { ...UB, uGlow: { value: 0 }, uGlowColor: { value: new THREE.Color("#8B5CFF") } };   // light + screen
  const UP = { ...UB, uGlow: { value: 0 }, uGlowColor: { value: new THREE.Color("#FFC94D") } };   // circuit boards
  const UAg = { ...UA, uGlow: { value: 0 }, uGlowColor: { value: new THREE.Color("#8B5CFF") } };
  const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e5);
  const ghostClip = new THREE.Plane(new THREE.Vector3(0, 1, 0), -1e5);
  const ghostMat = new THREE.MeshBasicMaterial({ color: 0x9c84ff, transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide, clippingPlanes: [ghostClip] });
  const ghosts = [];

  root.updateMatrixWorld(true);
  const parts = [];
  for (const node of level.children) {
    const name = node.name || "";
    const m = metaParts.get(name);
    const fb = FALLBACK[name];
    const kind = (m && m.kind) || (fb && fb[0]) || kindOf(name);
    const label = (m && m.label) || (fb && fb[1]) || "";
    const order = (m && m.order != null) ? m.order : (fb ? fb[2] : 99);
    const box = new THREE.Box3().setFromObject(node);
    if (box.isEmpty()) continue;
    const wc = box.getCenter(new THREE.Vector3());
    const isShell = /^PRINT_Shell$/i.test(name) || (m && /main shell/i.test(m.label || ""));

    /* explode offset in world mm */
    let off;
    if (m && Array.isArray(m.explode)) off = new THREE.Vector3().fromArray(m.explode);
    else {
      const d = wc.clone().sub(C); d.y *= 0.6;
      const dir = d.lengthSq() > 1 ? d.normalize() : new THREE.Vector3(0, 1, 0);
      if (isShell) off = new THREE.Vector3(0, 10, 150);
      else if (/rearcover/i.test(name)) off = new THREE.Vector3(0, 0, -150);
      else if (kind === "pcb") off = dir.multiplyScalar(50).add(new THREE.Vector3(0, -80, -40));
      else if (kind === "fastener") off = dir.multiplyScalar(160);
      else if (kind === "cable") off = dir.multiplyScalar(60).add(new THREE.Vector3(0, 30, 0));
      else off = dir.multiplyScalar(115);
    }
    const parent = node.parent;
    const base = node.position.clone();
    const worldOrigin = parent.localToWorld(base.clone());
    const tip = parent.worldToLocal(worldOrigin.clone().add(off));
    const delta = tip.sub(base);
    const localCenter = node.worldToLocal(wc.clone());

    const glowPart = /(^|_)(led|leddiffuser|display)$/i.test(name.replace(/^(PRINT|PURCHASE)_/i, "_"));
    const U = isShell ? UAg : glowPart ? UG : kind === "pcb" ? UP : UB;
    const meshes = [];
    node.traverse((o) => { if (o.isMesh) meshes.push(o); });
    meshes.forEach((o) => {
      const mats = (Array.isArray(o.material) ? o.material : [o.material]).map((mm) => {
        const c = mm.clone();
        patchMaterial(c, U);
        /* Every part shares the shell's cut plane, so the step-3 un-print cuts the whole robot cleanly from the top.
           Outside that moment the plane sits far above the model (clipH = 1e5) and cuts nothing. */
        c.clippingPlanes = [clip]; c.clipShadows = false;
        if (isShell) c.side = THREE.DoubleSide;
        return c;
      });
      o.material = Array.isArray(o.material) ? mats : mats[0];
      if (isShell) { const g = new THREE.Mesh(o.geometry, ghostMat); g.visible = false; o.add(g); ghosts.push(g); }
    });

    let el = null;
    if (label && kind !== "fastener" && kind !== "cable") {
      el = document.createElement("span");
      el.className = "ss-label";
      el.dataset.kind = kind;
      el.textContent = label;
      el.style.opacity = "0";
    }
    parts.push({ node, name, kind, label, order, base, delta, localCenter, el, isShell });
  }
  const rank = (p) => { const i = LABEL_FIRST.indexOf(p.name); return i < 0 ? 100 + p.order : i; };
  parts.filter((p) => p.el).sort((a, b) => rank(a) - rank(b)).slice(0, MAX_LABELS).forEach((p) => labelLayer.appendChild(p.el));
  const labelled = parts.filter((p) => p.el && p.el.parentNode);

  /* eyes on the screen (mono OLED), riding with the screen part */
  const dispPart = parts.find((p) => /display/i.test(p.name));
  const face = dispPart ? addFace(root, "oled", UB, dispPart.node) : null;
  let faceSig = "";

  /* a soft purple halo on the night light, so "glow purple at bedtime" reads at a glance */
  const ledPart = parts.find((p) => /leddiffuser/i.test(p.name)) || parts.find((p) => /(^|_)led$/i.test(p.name));
  let halo = null;
  if (ledPart) {
    const hc = document.createElement("canvas"); hc.width = hc.height = 128;
    const hg = hc.getContext("2d");
    const grd = hg.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, "rgba(196,160,255,.95)"); grd.addColorStop(0.18, "rgba(150,96,255,.75)");
    grd.addColorStop(0.45, "rgba(124,72,255,.32)"); grd.addColorStop(1, "rgba(124,72,255,0)");
    hg.fillStyle = grd; hg.fillRect(0, 0, 128, 128);
    const ht = new THREE.CanvasTexture(hc); ht.colorSpace = THREE.SRGBColorSpace;
    halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: ht, transparent: true, depthWrite: false, depthTest: false, blending: THREE.NormalBlending, toneMapped: false, opacity: 0 }));
    halo.visible = false; halo.renderOrder = 5;
    scene.add(halo);
  }
  const hv = new THREE.Vector3();

  /* blueprint edges for the wireframe flash, built in small idle chunks so scrolling never stalls */
  const edgeMat = new THREE.LineBasicMaterial({ color: 0xc9bbff, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
  const edgeLines = [];
  {
    const queue = [];
    root.traverse((o) => { if (o.isMesh && !ghosts.includes(o)) queue.push(o); });
    const step = () => {
      const t0 = performance.now();
      while (queue.length && performance.now() - t0 < 8) {
        const m = queue.shift();
        const ln = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry, 28), edgeMat);
        ln.visible = false; ln.renderOrder = 2;
        m.add(ln); edgeLines.push(ln);
      }
      if (queue.length) setTimeout(step, 16);
    };
    setTimeout(step, 400);
  }

  function setExplode(e) {
    curE = e;
    for (const p of parts) p.node.position.copy(p.base).addScaledVector(p.delta, e);
  }

  /* ---------- printer: build plate, gantry and nozzle ---------- */
  const printer = new THREE.Group();
  const PW = size.x + 80, PD = size.z + 80;
  const plateMat = new THREE.MeshStandardMaterial({ color: 0x2a2546, roughness: 0.55, metalness: 0.2, transparent: true });
  const plate = new THREE.Mesh(new THREE.BoxGeometry(PW, 6, PD), plateMat);
  plate.position.y = -3.2;
  const plateEdge = new THREE.LineSegments(new THREE.EdgesGeometry(plate.geometry), new THREE.LineBasicMaterial({ color: 0x9c84ff, transparent: true }));
  plateEdge.position.copy(plate.position);
  const metal = new THREE.MeshStandardMaterial({ color: 0xc9c4d8, roughness: 0.35, metalness: 0.7, transparent: true });
  const gantry = new THREE.Mesh(new THREE.BoxGeometry(PW + 60, 12, 14), new THREE.MeshStandardMaterial({ color: 0x3a3463, roughness: 0.5, metalness: 0.3, transparent: true }));
  const head = new THREE.Group();
  const block = new THREE.Mesh(new THREE.BoxGeometry(28, 22, 24), metal);
  block.position.y = 26;
  const fan = new THREE.Mesh(new THREE.CylinderGeometry(9, 9, 3, 24), new THREE.MeshStandardMaterial({ color: 0x7c5cff, roughness: 0.4, transparent: true }));
  fan.rotation.x = Math.PI / 2; fan.position.set(0, 26, 13.5);
  const nozzle = new THREE.Mesh(new THREE.ConeGeometry(6, 12, 20), new THREE.MeshStandardMaterial({ color: 0xd9a441, roughness: 0.3, metalness: 0.8, transparent: true }));
  nozzle.rotation.x = Math.PI; nozzle.position.y = 9;
  const tip = new THREE.Mesh(new THREE.SphereGeometry(2.6, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffd38a, transparent: true, toneMapped: false }));
  tip.position.y = 2.2;
  head.add(block, fan, nozzle, tip);
  printer.add(plate, plateEdge, gantry, head);
  printer.visible = false;
  scene.add(printer);
  const printerMats = [plateMat, plateEdge.material, metal, gantry.material, fan.material, nozzle.material, tip.material];

  /* ---------- gift box ---------- */
  const BW = size.x + 34, BD = size.z + 34, BH = H + 18, T = 3.2;
  const kraft = new THREE.MeshStandardMaterial({ color: 0xa8743f, roughness: 0.85, transparent: true });
  const kraftIn = new THREE.MeshStandardMaterial({ color: 0x8a5a2e, roughness: 0.95, transparent: true });
  const kraftLid = new THREE.MeshStandardMaterial({ color: 0xb07c47, roughness: 0.85, transparent: true });
  const ribbon = new THREE.MeshStandardMaterial({ color: 0x7c5cff, roughness: 0.45, transparent: true });
  const boxG = new THREE.Group();
  const bottom = new THREE.Mesh(new THREE.BoxGeometry(BW, T, BD), kraftIn); bottom.position.y = -T / 2 - 0.5;
  boxG.add(bottom);
  function wall(w, axis, sign) {
    const pivot = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(axis === "z" ? w : T, BH, axis === "z" ? T : w), kraft);
    mesh.position.y = BH / 2;
    if (axis === "z") { pivot.position.z = sign * (BD / 2 - T / 2); } else { pivot.position.x = sign * (BW / 2 - T / 2); }
    pivot.add(mesh);
    boxG.add(pivot);
    return pivot;
  }
  const wFront = wall(BW, "z", 1), wBack = wall(BW, "z", -1), wRight = wall(BD - 2 * T, "x", 1), wLeft = wall(BD - 2 * T, "x", -1);
  const lid = new THREE.Group();
  const LW = BW + 8, LD = BD + 8, LS = 26;
  const lidTop = new THREE.Mesh(new THREE.BoxGeometry(LW, T, LD), kraftLid); lidTop.position.y = T / 2;
  lid.add(lidTop);
  for (const [w, d, x, z] of [[LW, T, 0, LD / 2 - T / 2], [LW, T, 0, -LD / 2 + T / 2], [T, LD, LW / 2 - T / 2, 0], [T, LD, -LW / 2 + T / 2, 0]]) {
    const sk = new THREE.Mesh(new THREE.BoxGeometry(w, LS, d), kraftLid); sk.position.set(x, -LS / 2 + T, z); lid.add(sk);
  }
  const rb1 = new THREE.Mesh(new THREE.BoxGeometry(LW + 1.5, T + 1.2, 18), ribbon); rb1.position.y = T / 2 + 0.3;
  const rb2 = new THREE.Mesh(new THREE.BoxGeometry(18, T + 1.2, LD + 1.5), ribbon); rb2.position.y = T / 2 + 0.3;
  lid.add(rb1, rb2);
  for (const s of [-1, 1]) {
    const loop = new THREE.Mesh(new THREE.TorusGeometry(15, 4.2, 12, 28), ribbon);
    loop.position.set(s * 14, T + 12, 0); loop.rotation.set(0, s * 0.5, s * 0.6); loop.scale.set(1, 0.75, 1);
    lid.add(loop);
  }
  const knot = new THREE.Mesh(new THREE.SphereGeometry(7, 16, 12), ribbon); knot.position.y = T + 5; lid.add(knot);
  lid.position.y = BH;
  boxG.add(lid);
  boxG.visible = false;
  scene.add(boxG);
  const boxMats = [kraft, kraftIn];
  const lidMats = [kraftLid, ribbon];


  /* ---------- camera keys: [s, azimuth, elevation, distance factor] ---------- */
  const KEYS = [
    [0.0, 1.2, 0.3, 1.05, -0.3], [1.0, 1.12, 0.3, 0.98, -0.42], [1.5, 0.95, 0.3, 1.0, -0.3], [1.9, 0.38, 0.22, 1.45, 0],
    [2.2, 0.25, 0.5, 1.5, 0], [2.6, -0.2, 0.44, 1.42, 0], [2.95, -0.3, 0.36, 1.45, 0],
    [3.25, -0.12, 0.4, 1.95, 0], [3.7, 0.18, 0.36, 1.72, 0], [3.95, 0.3, 0.24, 1.5, 0],
    [4.3, 0.18, 0.16, 1.5, 0.28], [4.95, 0.08, 0.16, 1.5, 0.3], [5.15, 0.0, 0.16, 1.62, 0], [6.0, -0.38, 0.22, 1.62, 0]
  ];
  const R = 0.5 * Math.sqrt(size.x * size.x + size.y * size.y + size.z * size.z);
  let RX = R * 1.9;
  if (meta && Array.isArray(meta.explodedBbox) && meta.explodedBbox.length === 6) {
    const b = meta.explodedBbox, dx = b[3] - b[0], dy = b[4] - b[1], dz = b[5] - b[2];
    RX = 0.5 * Math.sqrt(dx * dx + dy * dy + dz * dz) * 0.82;
  }
  let curE = 0;
  function camAt(s, time) {
    let i = 0;
    while (i < KEYS.length - 2 && s > KEYS[i + 1][0]) i++;
    const a = KEYS[i], b = KEYS[i + 1];
    const t = ease(clamp01((s - a[0]) / (b[0] - a[0])));
    const az = lerp(a[1], b[1], t) + Math.sin(time * 0.35) * 0.035;
    const el = lerp(a[2], b[2], t) + Math.sin(time * 0.27) * 0.015;
    const fov = camera.fov * Math.PI / 180;
    const r = lerp(R, RX, clamp01(curE));
    let dist = (r / Math.sin(fov / 2)) * lerp(a[3], b[3], t);
    if (camera.aspect < 1) dist /= Math.max(0.6, camera.aspect);
    const pan = lerp(a[4] || 0, b[4] || 0, t) * R;
    const rx = Math.cos(az), rz = -Math.sin(az);   /* camera right vector on the ground */
    camera.position.set(Math.sin(az) * Math.cos(el) * dist + rx * pan, H * 0.5 + Math.sin(el) * dist, Math.cos(az) * Math.cos(el) * dist + rz * pan);
    camera.lookAt(rx * pan, H * 0.47, rz * pan);
  }

  /* ---------- per-frame state from s ---------- */
  let S = 0, lastS = 0;
  const v = new THREE.Vector3();
  function setFade(mats, a) { for (const m of mats) m.opacity = a; }

  function apply(s, time) {
    const Tm = (a, b) => clamp01((s - a) / (b - a));
    let dA = 1, dB = 1, clipH = 1e5, slice = 0, ghost = false, pr = 0, wire = 0, lab = 0, pcb = 0, led = 0, shellGlow = 0;
    let bounceY = 0, squash = 1, wiggle = 0;

    if (s < 2.0) {
      const ap = Tm(0.78, 1.08);
      dA = dB = ap;
      setExplode(s < 1.15 ? lerp(2.3, 1, ease(Tm(0.78, 1.15))) : 1 - ease(Tm(1.42, 1.84)));
      const rise = Tm(1.8, 1.86), fall = Tm(1.86, 1.98);
      wire = rise * (1 - fall);
      if (s >= 1.8) dA = dB = s < 1.86 ? 1 - 0.85 * rise : 0.15 + 0.85 * fall;
      lab = Tm(1.08, 1.18) * (1 - Tm(1.56, 1.72));
      pcb = Tm(1.12, 1.22) * (1 - Tm(1.48, 1.58)) * (0.55 + 0.45 * Math.sin(time * 5));
    } else {
      setExplode(0);
    }

    if (s >= 2.0 && s < 3.0) {
      /* The inner parts stay solid while the cut passes through them (no noise dissolve), are hidden once the cut
         reaches the floor (2.07) and sparkle back in after the print (2.62). */
      dB = s < 2.62 ? (s < 2.07 ? 1 : 0) : Tm(2.62, 2.76);
      if (s < 2.08) {
        /* The finished robot un-prints from the top: a clean cut with the shell's see-through outline left above it,
           so a paused frame reads as a half-printed shell (a noise dissolve here read as a broken mesh in stills). */
        const u = Tm(2.0, 2.07);
        clipH = lerp(H + 2, -1, ease(u));
        slice = u > 0 && u < 1 ? 1 : 0;
        ghost = u > 0;
      } else {
        const p = Tm(2.1, 2.6);
        clipH = lerp(-1, H + 2, p);
        slice = s < 2.6 ? 1 : 1 - Tm(2.6, 2.66);
        ghost = s < 2.64;
      }
      pr = Tm(2.02, 2.1) * (1 - Tm(2.6, 2.72));
    }

    /* printer */
    printer.visible = pr > 0.001;
    if (printer.visible) {
      setFade(printerMats, pr);
      printer.position.y = (1 - easeOut(pr)) * -40;
      const y = Math.min(clipH, H + 2);
      const ph = Tm(2.1, 2.6) * 90;
      const tri = (x) => 1 - 4 * Math.abs(((x % 1) + 1) % 1 - 0.5);
      head.position.set(tri(ph) * size.x * 0.42, y + 1 + (s > 2.6 ? Tm(2.6, 2.72) * 60 : 0), tri(ph * 0.37 + 0.25) * size.z * 0.4);
      gantry.position.set(0, head.position.y + 26, head.position.z);
      tip.material.opacity = pr * (0.6 + 0.4 * Math.sin(time * 18));
    }
    clip.constant = clipH;
    ghostClip.constant = -clipH;
    UB.uSliceY.value = clipH;
    UB.uSliceOn.value = slice;
    for (const g of ghosts) g.visible = ghost;

    /* receive: box */
    const boxOn = s > 3.0 && s < 3.97;
    boxG.visible = boxOn;
    if (boxOn) {
      const drop = bounceOut(Tm(3.0, 3.17));
      const hop = Tm(3.18, 3.32);
      boxG.position.y = (1 - drop) * H * 2.4 + Math.abs(Math.sin(hop * Math.PI * 2)) * 14 * (1 - hop);
      boxG.rotation.z = Math.sin(hop * Math.PI * 3) * 0.05 * (1 - hop);
      const lift = ease(Tm(3.34, 3.56));
      lid.position.set(0, BH + lift * H * 0.32, -lift * BD * 1.25);
      lid.rotation.set(-lift * 0.7, lift * 0.35, 0);
      const open = ease(Tm(3.48, 3.74)) * Math.PI * 0.52;
      wFront.rotation.x = open; wBack.rotation.x = -open; wRight.rotation.z = -open; wLeft.rotation.z = open;
      const f = Tm(3.78, 3.95);
      setFade(boxMats, 1 - f);
      setFade(lidMats, 1 - Tm(3.42, 3.54));
      boxG.scale.setScalar(1 + f * 0.08);
      kraft.depthWrite = kraftIn.depthWrite = f < 0.05;
    }

    /* modify: bounce + purple light */
    if (s >= 4.0 && s < 5.0) {
      const k = Tm(4.42, 4.78);
      if (k > 0 && k < 1) {
        const ph = k * 2, local = ph % 1;
        bounceY = 4 * local * (1 - local) * 34 * (ph < 1 ? 1 : 0.6);
        const edge = Math.min(local, 1 - local);
        squash = edge < 0.12 ? 1 - 0.09 * (1 - edge / 0.12) : 1 + 0.03 * Math.sin(local * Math.PI);
      }
      led = Tm(4.5, 4.66) * 1.7;
      shellGlow = Tm(4.5, 4.66) * 0.28;
    }
    if (s >= 5.0) {
      led = 1.2 + 0.5 * Math.sin(time * 4) * (s < 5.55 ? 1 : 0.3);
      shellGlow = 0.22;
      const w = Tm(5.7, 5.9);
      wiggle = Math.sin(w * Math.PI * 4) * 0.14 * (1 - w);
    }

    const purpleOn = s >= 4.5 ? (s < 5 ? Tm(4.5, 4.68) : 1) : 0;
    purple.intensity = purpleOn * (4.5 + (s >= 5 ? 1.0 * Math.sin(time * 4) : 0));
    purple.position.set(0, H * 0.62 + bounceY, size.z / 2 + 90);
    if (face) {
      const happy = s >= 4.45 && s < 5.0;
      const blink = !happy && (time % 3.6) < 0.13;
      const sig = (happy ? "h" : "") + (blink ? "b" : "");
      if (sig !== faceSig) { faceSig = sig; face.draw(blink, 0, happy); face.tex.needsUpdate = true; }
    }
    for (const ln of edgeLines) ln.visible = wire > 0.01;
    edgeMat.opacity = Math.min(1, wire * 1.2);

    rig.position.y = bounceY;
    rig.scale.set(1 / Math.sqrt(squash), squash, 1 / Math.sqrt(squash));
    rig.rotation.y = wiggle;

    UA.uDissolve.value = dA;
    UB.uDissolve.value = dB;
    UG.uGlow.value = led;
    UAg.uGlow.value = shellGlow;
    UP.uGlow.value = pcb;
    root.visible = s > 0.76;


    /* events (forward crossings only) */
    const crossed = (x) => lastS < x && s >= x;
    if (crossed(0.86)) onEvent("parts");
    if (crossed(2.66)) onEvent("printed");
    if (crossed(3.58)) onEvent("unbox");
    if (crossed(4.55)) onEvent("hop");
    if (crossed(5.55)) onEvent("check");
    lastS = s;

    camAt(s, time);

    if (halo) {
      halo.visible = purpleOn > 0.01;
      if (halo.visible) {
        scene.updateMatrixWorld();
        hv.copy(ledPart.localCenter); ledPart.node.localToWorld(hv);
        halo.position.set(hv.x, hv.y, hv.z + 8);
        const hs = 62 * (1 + 0.1 * Math.sin(time * 4));
        halo.scale.set(hs, hs, 1);
        halo.material.opacity = purpleOn * (s < 5 ? 0.9 : 0.7);
      }
    }

    /* labels */
    if (lab > 0.01) {
      const w = host.clientWidth, h = host.clientHeight;
      scene.updateMatrixWorld();
      camera.updateMatrixWorld();
      const placed = [];
      for (const p of labelled) {
        v.copy(p.localCenter); p.node.localToWorld(v); v.project(camera);
        p.sx = (v.x * 0.5 + 0.5) * w; p.sy = (-v.y * 0.5 + 0.5) * h; p.front = v.z < 1;
        if (!p.lw) { p.lw = p.el.offsetWidth || 90; p.lh = p.el.offsetHeight || 26; }
      }
      const order = labelled.slice().sort((a, b) => a.sy - b.sy);
      for (const p of order) {
        let y = p.sy;
        for (let k = 0; k < 6; k++) {
          const hit = placed.find((q) => p.sx < q.x + q.w + 6 && p.sx + p.lw + 6 > q.x && Math.abs(y - q.y) < (p.lh + q.h) / 2 + 3);
          if (!hit) break;
          y = hit.y + (p.lh + hit.h) / 2 + 4;
        }
        placed.push({ x: p.sx, y, w: p.lw, h: p.lh });
        p.el.style.transform = `translate3d(${p.sx.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-6px, -50%)`;
        p.el.style.opacity = String(p.front ? lab : 0);
      }
    } else if (labelled.length && labelled[0].el.style.opacity !== "0") {
      for (const p of labelled) p.el.style.opacity = "0";
    }
  }

  let time = 0;
  const loop = visibilityLoop(host, (dt) => {
    resize();
    time += dt;
    apply(S, time);
    renderer.render(scene, camera);
  }, "200px");

  return {
    setProgress(s) { S = s; },
    kick: () => loop.kick(),
    partCount: parts.length
  };
}
