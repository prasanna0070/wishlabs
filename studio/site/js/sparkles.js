/* Sparkle particles on one fixed full-screen canvas: bursts ("poof") and a cursor trail.
   The loop runs only while particles are alive, so it costs nothing when idle. */
const COLORS = ["#7C5CFF", "#FFC94D", "#FFFFFF", "#B9A8FF", "#FF8FB1", "#7FE3C4"];
const MAX = 520;

export function createSparkles(canvas) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  let W = 0, H = 0, running = false, last = 0;
  const parts = [];

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  resize();
  window.addEventListener("resize", resize, { passive: true });

  const pick = (a) => a[(Math.random() * a.length) | 0];

  function spawn(p) {
    if (parts.length >= MAX) parts.shift();
    parts.push(p);
    if (!running) { running = true; last = performance.now(); requestAnimationFrame(tick); }
  }

  function burst(x, y, o = {}) {
    const n = o.count || 34, sp = o.speed || 7, colors = o.colors || COLORS;
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2, v = sp * (0.3 + Math.random() * 0.9);
      spawn({
        x: x + (o.jitter ? (Math.random() - 0.5) * o.jitter : 0),
        y: y + (o.jitter ? (Math.random() - 0.5) * o.jitter * 0.5 : 0),
        vx: Math.cos(a) * v, vy: Math.sin(a) * v - (o.lift ?? 1.6),
        g: o.gravity ?? 0.13, drag: 0.955,
        life: 0, max: 0.6 + Math.random() * (o.life || 0.8),
        size: (o.size || 9) * (0.45 + Math.random() * 0.9),
        rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
        color: pick(colors), star: Math.random() < 0.68
      });
    }
  }

  function trail(x, y, colors) {
    spawn({
      x: x + (Math.random() - 0.5) * 6, y: y + (Math.random() - 0.5) * 6,
      vx: (Math.random() - 0.5) * 1.2, vy: (Math.random() - 0.5) * 1.2 + 0.4,
      g: 0.035, drag: 0.94, life: 0, max: 0.45 + Math.random() * 0.45,
      size: 3.5 + Math.random() * 5.5, rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.25,
      color: pick(colors || COLORS), star: Math.random() < 0.75
    });
  }

  function drawStar(s) {
    ctx.beginPath();
    ctx.moveTo(0, -s);
    ctx.quadraticCurveTo(0, 0, s, 0);
    ctx.quadraticCurveTo(0, 0, 0, s);
    ctx.quadraticCurveTo(0, 0, -s, 0);
    ctx.quadraticCurveTo(0, 0, 0, -s);
    ctx.fill();
  }

  function tick(now) {
    const dt = Math.max(0, Math.min(0.05, (now - last) / 1000)); last = Math.max(last, now);
    const k = dt * 60;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      p.life += dt;
      if (p.life >= p.max) { parts.splice(i, 1); continue; }
      const d = Math.pow(p.drag, k);
      p.vx *= d; p.vy = p.vy * d + p.g * k;
      p.x += p.vx * k; p.y += p.vy * k; p.rot += p.vr * k;
      const f = p.life / p.max;
      const grow = Math.min(1, p.life / 0.07);
      const s = Math.max(0.01, p.size * grow * (1 - f * 0.55));
      ctx.globalAlpha = Math.max(0, 1 - f * f);
      ctx.fillStyle = p.color;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      if (p.star) drawStar(s); else { ctx.beginPath(); ctx.arc(0, 0, s * 0.32, 0, Math.PI * 2); ctx.fill(); }
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    if (parts.length) requestAnimationFrame(tick);
    else { running = false; ctx.clearRect(0, 0, W, H); }
  }

  /* Burst from the centre of an element. */
  function burstAt(el, o) {
    const r = el.getBoundingClientRect();
    burst(r.left + r.width / 2, r.top + r.height / 2, o);
  }

  function enableTrail() {
    let lx = -99, ly = -99;
    window.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      const dx = e.clientX - lx, dy = e.clientY - ly;
      if (dx * dx + dy * dy < 150) return;
      lx = e.clientX; ly = e.clientY;
      trail(e.clientX, e.clientY);
    }, { passive: true });
  }

  return { burst, burstAt, trail, enableTrail };
}
