/* The real routed circuit board of the bedtime robot (engine output). When it scrolls into view its parts
   drop onto the board one by one, then the copper tracks pulse while it turns slowly. Drag to spin. */
import { THREE, makeRenderer, makeEnvironment, sizer, loadGLB, contactShadow, visibilityLoop } from "./stage3d.js";

const LAYERS = /^(copper|pads|vias|silkscreen|soldermask|board)/i;

export async function createBoard(host, { still = false } = {}) {
  const gltf = await loadGLB("assets/3d/board.glb");
  const renderer = makeRenderer(host, { dprCap: 2 });
  const scene = new THREE.Scene();
  scene.environment = makeEnvironment(renderer);
  scene.add(new THREE.HemisphereLight(0xe8e2ff, 0x1b1638, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(60, 140, 90); scene.add(key);
  const rim = new THREE.DirectionalLight(0x9c84ff, 1.8); rim.position.set(-120, 60, -90); scene.add(rim);

  const camera = new THREE.PerspectiveCamera(28, 1, 1, 2000);
  const resize = sizer(renderer, camera, host);

  const root = gltf.scene;
  const spin = new THREE.Group(); spin.add(root); scene.add(spin);
  const shadow = contactShadow(0.45); shadow.scale.set(105, 120, 1); shadow.position.y = -2.4; spin.add(shadow);

  /* copper and pads get a warm pulse; the solder mask keeps its own blue */
  const glowMats = [];
  root.traverse((o) => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((m) => {
      if (m.userData.wwDone) return;
      m.userData.wwDone = true;
      m.envMapIntensity = 0.65;
      if (/^(copper|pad-finish)$/i.test(m.name || "")) {
        m.emissive = new THREE.Color("#FFB347"); m.emissiveIntensity = 0; glowMats.push(m);
      }
      /* a slightly thinner, deeper solder mask so the routed copper tracks show through */
      if (/^soldermask$/i.test(m.name || "")) { m.color.multiplyScalar(0.62); m.transparent = true; m.opacity = 0.7; m.depthWrite = false; }
    });
  });

  /* the parts that drop in: every top-level node that is not a board layer */
  const parts = root.children.filter((n) => !LAYERS.test(n.name || ""));
  parts.sort((a, b) => (a.position.z - b.position.z) || (a.position.x - b.position.x));
  parts.forEach((n) => { n.userData.baseY = n.position.y; });

  let dropStart = -1;   // wall-clock start of the drop (ms); -1 = not started. Wall clock, so slow GPUs finish on time.
  function setDrop(t) {
    parts.forEach((n, i) => {
      const local = Math.max(0, Math.min(1, (t - i * 0.07) / 0.55));
      const b = 1 - Math.pow(1 - local, 3);
      const bounce = local < 1 ? Math.sin(local * Math.PI) * 4 * (1 - local) : 0;
      n.position.y = n.userData.baseY + (1 - b) * 70 + bounce;
      n.visible = local > 0;
    });
  }
  setDrop(still ? 99 : 0);

  /* drag to spin */
  let yaw = 0, vel = 0, dragging = false, lastX = 0;
  host.addEventListener("pointerdown", (e) => { dragging = true; lastX = e.clientX; vel = 0; try { host.setPointerCapture(e.pointerId); } catch (_) {} });
  host.addEventListener("pointermove", (e) => { if (!dragging) return; const dx = e.clientX - lastX; lastX = e.clientX; yaw += dx * 0.012; vel = dx * 0.012; });
  const end = () => { dragging = false; };
  host.addEventListener("pointerup", end); host.addEventListener("pointercancel", end);

  let t = 0;
  const loop = visibilityLoop(host, (dt) => {
    resize();
    t += dt;
    const dropT = dropStart < 0 ? -1 : (performance.now() - dropStart) / 1000;
    if (dropT >= 0 && !still) setDrop(dropT);
    if (!dragging) { yaw += vel; vel *= 0.94; }
    spin.rotation.y = (still ? -0.5 : -0.5 + Math.sin(t * 0.3) * 0.45) + yaw;
    const done = still || dropT > parts.length * 0.07 + 0.6;
    const pulse = done ? 0.35 + 0.35 * Math.sin(t * 2.6) : 0;
    glowMats.forEach((m) => { m.emissiveIntensity = pulse; });
    const fov = camera.fov * Math.PI / 180;
    let dist = 50 / Math.sin(fov / 2);
    if (camera.aspect < 1) dist /= Math.max(0.62, camera.aspect);
    const el = 0.78;
    camera.position.set(0, Math.sin(el) * dist, Math.cos(el) * dist);
    camera.lookAt(0, 2, 0);
    renderer.render(scene, camera);
  }, "100px");

  return {
    drop() { if (dropStart < 0) { dropStart = performance.now(); loop.kick(); } },
    kick: () => loop.kick()
  };
}
