/* Hero stage: each wish's real 3D design materialises (sparkle dissolve + elastic scale), turns slowly,
   follows the mouse a little and can be spun by dragging. */
import { THREE, makeRenderer, makeEnvironment, sizer, loadGLB, groundModel, makeUniforms, patchMaterial, contactShadow, visibilityLoop, addFace } from "./stage3d.js";

const gsap = window.gsap;

export function createHero(host) {
  const renderer = makeRenderer(host, { dprCap: 2 });
  const scene = new THREE.Scene();
  scene.environment = makeEnvironment(renderer);
  const camera = new THREE.PerspectiveCamera(24, 1, 0.1, 100);
  camera.position.set(0, 0.85, 7.4);
  camera.lookAt(0, 0.02, 0);
  scene.add(new THREE.HemisphereLight(0xfff6ea, 0xd8c6ad, 0.5));
  const key = new THREE.DirectionalLight(0xffffff, 1.55); key.position.set(3, 5, 4); scene.add(key);
  const rim = new THREE.DirectionalLight(0xa48cff, 1.5); rim.position.set(-4, 2.5, -3.5); scene.add(rim);

  const tilt = new THREE.Group(); scene.add(tilt);
  const spin = new THREE.Group(); tilt.add(spin);
  const shadow = contactShadow(0.3); shadow.material.opacity = 0; tilt.add(shadow);

  const resize = sizer(renderer, camera, host);
  const items = new Map();
  let current = null;

  async function prepare(g) {
    if (items.has(g.slug)) return items.get(g.slug);
    const gltf = await loadGLB(`assets/3d/${g.slug}.glb`);
    const model = gltf.scene;
    let size = groundModel(model);
    if (Math.max(size.x, size.y, size.z) < 2) { model.scale.multiplyScalar(1000); size = groundModel(model); }
    const U = makeUniforms({ edge: g.glow, glow: g.glow, noiseScale: 0.055 });
    model.traverse((o) => {
      if (!o.isMesh) return;
      (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => patchMaterial(m, U));
    });
    const content = new THREE.Group();
    content.add(model);
    const face = g.face ? addFace(model, g.face, U, content) : null;
    const maxDim = Math.max(size.x, size.y, size.z);
    /* fit the largest side to 2.0 units and lift a little, so the base and its shadow clear the name chip */
    const s = 2.0 / maxDim;
    const inner = new THREE.Group();
    inner.add(content);
    inner.scale.setScalar(s);
    inner.position.y = -size.y * s / 2 + 0.16;
    const holder = new THREE.Group();
    holder.add(inner);
    const it = { slug: g.slug, holder, content, U, face, height: size.y * s, foot: Math.max(size.x, size.z) * s, blinkAt: 2 };
    items.set(g.slug, it);
    return it;
  }

  function hideItem(it) {
    gsap.killTweensOf([it.U.uDissolve, it.holder.scale]);
    gsap.to(it.U.uDissolve, { value: 0, duration: 0.65, ease: "power2.in" });
    gsap.to(it.holder.scale, { x: 0.82, y: 0.82, z: 0.82, duration: 0.65, ease: "power2.in", onComplete: () => spin.remove(it.holder) });
  }

  async function show(g) {
    const it = await prepare(g);
    if (current && current !== it) hideItem(current);
    current = it;
    gsap.killTweensOf([it.U.uDissolve, it.holder.scale, it.holder.rotation]);
    spin.add(it.holder);
    it.U.uDissolve.value = 0;
    it.holder.scale.setScalar(0.001);
    it.holder.rotation.y = -1.25;
    gsap.to(it.U.uDissolve, { value: 1, duration: 1.6, ease: "power2.inOut" });
    gsap.to(it.holder.scale, { x: 1, y: 1, z: 1, duration: 1.35, ease: "elastic.out(1, 0.55)" });
    gsap.to(it.holder.rotation, { y: 0, duration: 1.9, ease: "power3.out" });
    gsap.to(shadow.position, { y: -it.height / 2 + 0.15, duration: 0.6, ease: "power2.out" });
    gsap.to(shadow.scale, { x: it.foot * 1.35, y: it.foot * 1.05, duration: 0.9, ease: "power2.out" });
    gsap.to(shadow.material, { opacity: 0.3, duration: 1.1 });
    loop.kick();
    return it;
  }

  function hide() {
    if (current) hideItem(current);
    current = null;
    gsap.to(shadow.material, { opacity: 0, duration: 0.6 });
  }

  /* Mouse parallax (fine pointers) and drag to spin (any pointer, horizontal only). */
  let tx = 0, ty = 0, px = 0, py = 0, yaw = 0, vel = 0, dragging = false, lastX = 0;
  if (document.documentElement.classList.contains("fine")) {
    window.addEventListener("pointermove", (e) => {
      tx = (e.clientX / window.innerWidth - 0.5) * 2;
      ty = (e.clientY / window.innerHeight - 0.5) * 2;
    }, { passive: true });
  }
  host.addEventListener("pointerdown", (e) => { dragging = true; lastX = e.clientX; vel = 0; try { host.setPointerCapture(e.pointerId); } catch (_) {} });
  host.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX; lastX = e.clientX;
    yaw += dx * 0.012; vel = dx * 0.012;
  });
  const end = () => { dragging = false; };
  host.addEventListener("pointerup", end);
  host.addEventListener("pointercancel", end);

  let t = 0;
  const loop = visibilityLoop(host, (dt) => {
    resize();
    t += dt;
    px += (tx - px) * 0.05; py += (ty - py) * 0.05;
    tilt.rotation.x = py * 0.1;
    tilt.rotation.y = px * 0.28;
    if (!dragging) { yaw += vel; vel *= 0.94; }
    spin.rotation.y = -0.2 + Math.sin(t * 0.45) * 0.36 + yaw;
    spin.position.y = Math.sin(t * 1.5) * 0.035;
    if (current && current.face) {
      current.blinkAt -= dt;
      const blink = current.blinkAt < 0.12 && current.blinkAt > 0;
      if (current.blinkAt <= 0) current.blinkAt = 2.6 + Math.random() * 2.4;
      const look = Math.round(Math.sin(t * 0.7) * 4) / 4;
      const sig = (blink ? 1 : 0) + look * 10;
      if (sig !== current.face.sig) { current.face.sig = sig; current.face.draw(blink, look, false); current.face.tex.needsUpdate = true; }
    }
    /* the dissolve noise lives in the model's own millimetre frame, so it does not slide as the model turns */
    scene.updateMatrixWorld();
    for (const it of items.values()) if (it.holder.parent) it.U.uNoiseMatrix.value.copy(it.content.matrixWorld).invert();
    renderer.render(scene, camera);
  }, "60px");

  return { show, hide, prepare: (g) => prepare(g).catch(() => null), kick: () => loop.kick() };
}
