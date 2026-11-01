/* Page behaviour: smooth scroll, the kinetic hero and its wish loop, the marquee, the pinned story,
   the doodle-to-device gallery, the example and price, the FAQ and the final call to action.
   three.js loads only when a 3D stage is near the screen (js/hero3d.js, js/story3d.js). */
import { createSparkles } from "./sparkles.js";

const SITE = window.SITE;
const gsap = window.gsap;
const ScrollTrigger = window.ScrollTrigger;
const html = document.documentElement;
const $ = (q, el = document) => el.querySelector(q);
const $$ = (q, el = document) => Array.from(el.querySelectorAll(q));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const LIBS = !!(gsap && ScrollTrigger);
if (!LIBS) html.classList.add("rm");
const RM = html.classList.contains("rm");
const FINE = html.classList.contains("fine");
if (RM || !LIBS) html.classList.remove("pin");
const PIN = html.classList.contains("pin");

/* The pinned 3D story (desktop) and the stacked cards (phones) are chosen once, before first paint.
   When the viewport later crosses that breakpoint (rotation, window resize, DevTools device mode),
   reload so the right layout is built instead of squeezing the desktop one. */
if (!RM && window.matchMedia) {
  const pinMq = window.matchMedia("(min-width: 1024px) and (min-height: 600px)");
  let pinTimer = 0;
  const onPinChange = () => {
    clearTimeout(pinTimer);
    pinTimer = setTimeout(() => { if (pinMq.matches !== PIN) location.reload(); }, 250);
  };
  if (pinMq.addEventListener) pinMq.addEventListener("change", onPinChange);
  else if (pinMq.addListener) pinMq.addListener(onPinChange);
}

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) { return false; }
}
const GL = hasWebGL();

if (LIBS) gsap.registerPlugin(ScrollTrigger);

/* ---------- smooth scroll ---------- */
let lenis = null;
if (LIBS && !RM && window.Lenis) {
  lenis = new window.Lenis({ lerp: 0.1, smoothWheel: true });
  lenis.on("scroll", ScrollTrigger.update);
  gsap.ticker.add((t) => lenis.raf(t * 1000));
  gsap.ticker.lagSmoothing(0);
}

/* small handle for automated screenshots */
window.__ww = { get lenis() { return lenis; }, ScrollTrigger };

function scrollToY(y, duration = 1.3) {
  if (lenis) lenis.scrollTo(y, { duration });
  else window.scrollTo({ top: y, behavior: RM ? "auto" : "smooth" });
}

$$('a[href^="#"]').forEach((a) => a.addEventListener("click", (e) => {
  const id = a.getAttribute("href");
  if (id.length < 2) return;
  const t = document.querySelector(id);
  if (!t) return;
  e.preventDefault();
  const y = id === "#main" ? 0 : t.getBoundingClientRect().top + window.scrollY - (id === "#how" ? 0 : 70);
  scrollToY(y);
  history.replaceState(null, "", id);
}));

/* header hairline once the page scrolls */
const top = $("#top");
const howSec = $("#how");
const onScroll = () => {
  top.classList.toggle("scrolled", window.scrollY > 10);
  const r = howSec.getBoundingClientRect();
  top.classList.toggle("dark", r.top < 40 && r.bottom > 40);
};
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

/* ---------- sparkles ---------- */
const fx = !RM ? createSparkles($("#fx")) : null;
if (fx && FINE) fx.enableTrail();

/* ---------- helpers ---------- */
function split(el) {
  const text = el.textContent;
  el.textContent = "";
  const chars = [];
  text.split(/(\s+)/).forEach((tok) => {
    if (!tok) return;
    if (/^\s+$/.test(tok)) { el.appendChild(document.createTextNode(" ")); return; }
    const w = document.createElement("span");
    w.className = "w";
    for (const ch of tok) {
      const c = document.createElement("span");
      c.className = "ch";
      c.textContent = ch;
      w.appendChild(c);
      chars.push(c);
    }
    el.appendChild(w);
  });
  return chars;
}

function jiggle(chars) {
  if (!FINE || RM) return;
  chars.forEach((c) => c.addEventListener("pointerenter", () => {
    if (gsap.isTweening(c)) return;
    gsap.timeline()
      .to(c, { y: -18, scaleY: 1.12, rotate: gsap.utils.random(-10, 10), duration: 0.16, ease: "power2.out" })
      .to(c, { y: 0, scaleY: 1, rotate: 0, duration: 0.8, ease: "elastic.out(1.1, 0.35)" });
  }));
}

/* ==========================================================================
   1. Hero
   ========================================================================== */
const heroSec = $(".hero");
const form = $("#wishbox");
const input = $("#wish-input");
const wishTxt = $(".wish-txt", form);
const stage = $("#hero-stage");
const chip = $("#hero-chip");
const fallbackImg = $("#hero-fallback");
const toastEl = $("#wish-toast");
const note = $(".stage-note", stage);

let heroChars = [];
$$(".kinetic [data-split]").forEach((el) => { heroChars = heroChars.concat(split(el)); });

if (LIBS && !RM) {
  const tl = gsap.timeline({ delay: 0.1 });
  tl.from(heroChars, {
    yPercent: -170, opacity: 0, rotate: () => gsap.utils.random(-28, 28),
    duration: 1.15, ease: "bounce.out", stagger: 0.045
  })
    .fromTo(".squiggle path", { strokeDashoffset: 1 }, { strokeDashoffset: 0, duration: 0.9, ease: "power2.out" }, "-=0.55")
    .from([".hero .kicker", ".hero .lede", "#wishbox", ".hero-actions"], { y: 28, opacity: 0, duration: 0.9, ease: "power3.out", stagger: 0.09 }, 0.3)
    .from(stage, { opacity: 0, scale: 0.9, duration: 1.1, ease: "power3.out" }, 0.25);
  jiggle(heroChars);
}

function toast(msg) {
  toastEl.textContent = msg;
  if (LIBS && !RM) gsap.fromTo(toastEl, { y: 6, opacity: 0 }, { y: 0, opacity: 1, duration: 0.4 });
}

/* The wish box: auto-typing until the visitor takes over. */
let userMode = false;
form.classList.add("auto");
input.addEventListener("focus", () => { userMode = true; form.classList.remove("auto"); });
input.addEventListener("blur", () => { if (!input.value.trim()) { userMode = false; form.classList.add("auto"); } });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.submit(); }
});
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const v = input.value.trim();
  if (fx) fx.burstAt($(".wish-go", form), { count: 60, speed: 10 });
  const go = () => { window.location.href = SITE.STUDIO_URL; };
  if (v && navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(v).then(() => { toast("Your wish is copied. Paste it into the studio."); setTimeout(go, 1300); }, () => setTimeout(go, 400));
  } else setTimeout(go, RM ? 0 : 450);
});

let heroOn = true;
new IntersectionObserver((es) => { heroOn = es[es.length - 1].isIntersecting; }, { rootMargin: "0px" }).observe(heroSec);
async function gate() { while (userMode || !heroOn || document.hidden) await wait(220); }

async function typeIn(str) {
  for (let i = 1; i <= str.length; i++) {
    await gate();
    wishTxt.textContent = str.slice(0, i);
    await wait(30 + Math.random() * 38 + (str[i - 1] === " " ? 40 : 0));
  }
}
async function erase() {
  let s = wishTxt.textContent;
  while (s.length) {
    await gate();
    s = s.slice(0, -Math.max(1, Math.ceil(s.length / 10)));
    wishTxt.textContent = s;
    await wait(18);
  }
}

function setChip(g, animate = true) {
  $(".chip-name", chip).textContent = g.name;
  $(".chip-aud", chip).textContent = g.chip.charAt(0).toLowerCase() + g.chip.slice(1);
  heroSec.style.setProperty("--g", g.glow);
  if (animate && LIBS && !RM) gsap.fromTo(chip, { y: 14, opacity: 0, scale: 0.9 }, { y: 0, opacity: 1, scale: 1, duration: 0.7, ease: "back.out(2)", overwrite: true });
}

function showFallback(g) {
  stage.classList.add("fallback");
  fallbackImg.alt = g.name + ", an example design rendered by our engine";
  fallbackImg.style.visibility = "";
  fallbackImg.onerror = () => { fallbackImg.style.visibility = "hidden"; };
  fallbackImg.src = `assets/img/fallback/${g.slug}.webp`;
  note.textContent = "Engine renders of real example designs";
  if (LIBS && !RM) gsap.fromTo(fallbackImg, { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1, duration: 0.9, ease: "back.out(1.6)" });
}

function poof() {
  if (!fx) return;
  const caret = $(".caret", form).getBoundingClientRect();
  fx.burst(caret.left, caret.top + caret.height / 2, { count: 14, speed: 4, size: 6 });
  const r = stage.getBoundingClientRect();
  const g = SITE.GADGETS[0];
  fx.burst(r.left + r.width / 2, r.top + r.height * 0.48, { count: 70, speed: 11, size: 12, jitter: 70, lift: 2.4, colors: ["#7C5CFF", "#FFC94D", "#FFFFFF", "#B9A8FF", getComputedStyle(heroSec).getPropertyValue("--g").trim() || g.glow] });
}

/* The first wish is already in the box, so its gadget appears at once; after that each new wish is typed,
   and the old gadget dissolves away as the new one materialises. */
async function heroLoop(hero) {
  const list = SITE.GADGETS;
  let i = 0;
  if (hero) hero.prepare(list[0]);
  await wait(900);
  for (;;) {
    const g = list[i];
    if (i !== 0 || wishTxt.textContent !== g.wish) {
      if (hero) hero.prepare(g);
      await typeIn(g.wish);
    }
    await gate();
    form.classList.add("glow");
    poof();
    let ok = false;
    if (hero) {
      try { await Promise.race([hero.show(g), wait(9000).then(() => { throw new Error("slow"); })]); ok = true; }
      catch (e) { ok = false; }
    }
    if (ok) stage.classList.remove("fallback"); else showFallback(g);
    setChip(g);
    if (hero) hero.prepare(list[(i + 1) % list.length]);
    await wait(4200);
    await gate();
    form.classList.remove("glow");
    if (!hero) gsap.to(fallbackImg, { opacity: 0.25, scale: 0.94, duration: 0.5 });
    await erase();
    i = (i + 1) % list.length;
    await wait(200);
  }
}

async function initHero() {
  setChip(SITE.GADGETS[0], false);
  if (RM || !LIBS) { showFallback(SITE.GADGETS[0]); return; }
  let hero = null;
  if (GL) {
    try {
      const mod = await import("./hero3d.js");
      hero = mod.createHero($("#hero-canvas"));
    } catch (e) { hero = null; }
  }
  chip.style.opacity = "0";
  heroLoop(hero);
}

/* ==========================================================================
   2. Marquee
   ========================================================================== */
function initMarquee() {
  if (RM || !LIBS) return;
  const rows = $$(".mq-row").map((row) => {
    const track = $(".mq-track", row);
    const items = Array.from(track.children);
    for (let k = 0; k < 3; k++) items.forEach((it) => { const c = it.cloneNode(true); c.setAttribute("aria-hidden", "true"); track.appendChild(c); });
    return { row, track, first: items[0], firstClone: track.children[items.length], x: 0, dir: Number(row.dataset.dir) || -1, period: 1 };
  });
  const measure = () => rows.forEach((r) => { r.period = Math.max(1, r.firstClone.offsetLeft - r.first.offsetLeft); });
  measure();
  window.addEventListener("resize", measure, { passive: true });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
  let on = false, boost = 0, skew = 0, sign = 1;
  new IntersectionObserver((es) => { on = es[es.length - 1].isIntersecting; }).observe($(".marquee"));
  gsap.ticker.add((time, delta) => {
    if (!on) return;
    const dt = Math.min(0.05, delta / 1000);
    const v = lenis ? lenis.velocity : 0;
    if (Math.abs(v) > 0.2) sign = v > 0 ? 1 : -1;
    boost += (Math.min(Math.abs(v) * 60, 1500) - boost) * 0.08;
    skew += (gsap.utils.clamp(-8, 8, v * 0.35) - skew) * 0.1;
    for (const r of rows) {
      r.x += r.dir * sign * (64 + boost) * dt;
      r.x = ((r.x % r.period) - r.period) % r.period;
      r.track.style.transform = `translate3d(${r.x.toFixed(2)}px,0,0) skewX(${(-skew).toFixed(2)}deg)`;
    }
  });
}

/* ==========================================================================
   3. How it works
   ========================================================================== */
const STORY_WISH = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters.";
const CAPTIONS = [
  "The request we test our engine with",
  "Real engine output: every part of the bedtime robot, to scale",
  "The shell, printed layer by layer",
  "Unbox it, switch it on, connect it",
  "Changes in plain words",
  "New versions go on trial first, with rollback"
];
const STAGE_GLOW = ["#7C5CFF", "#7C5CFF", "#FFB347", "#C8A27A", "#9B6BFF", "#3FD3A4"];
const PRINT_PHASES = [[2.1, 2.6, "Printing the shell"], [2.6, 2.76, "Fitting the parts"], [2.76, 2.88, "Loading the software"], [2.88, 2.98, "Checking every part"]];

function initStory() {
  const section = $("#how");
  const stageEl = $("#story-stage");
  const steps = $$(".story-step");

  /* load the small step pictures once the section is near */
  const loadPhotos = () => $$(".ss-photo img[data-src]").forEach((img) => { img.src = img.dataset.src; img.removeAttribute("data-src"); });

  if (!PIN) {
    if (LIBS && !RM) steps.forEach((el) => gsap.from(el, { y: 50, opacity: 0, duration: 0.9, ease: "power3.out", scrollTrigger: { trigger: el, start: "top 90%", once: true } }));
    return;
  }

  const pin = $("#story-pin");
  const dots = $$("#story-dots button");
  const stepsList = $("#story-steps");
  const ovWish = $(".ov-wish", stageEl), wbTyped = $(".wb-typed", stageEl);
  const ovPrint = $(".ov-print", stageEl), pmLabel = $(".pm-label", stageEl), pmVal = $(".pm-val", stageEl), pmBar = $(".print-bar", stageEl);
  const ovChat = $(".ov-chat", stageEl), chatEls = $$(".ov-chat .bubble, .ov-chat .sent-chip", stageEl);
  const ovRing = $(".ov-ring", stageEl), ringChips = $$(".ring-chips li", stageEl);
  const caption = $("#story-caption"), photos = $$(".ss-photo figure", stageEl), hint = $(".scroll-hint");
  const photoBox = $(".ss-photo", stageEl);
  const wbRest = document.createElement("span");
  wbRest.className = "wb-rest"; wbRest.style.opacity = "0";
  wbTyped.after(wbRest);

  let target = 0, cur = 0, story = null, lastIdx = -1, dirty = true;
  const st = ScrollTrigger.create({
    trigger: pin, start: "top top", end: () => "+=" + Math.round(window.innerHeight * 6),
    pin: true, anticipatePin: 1,
    onUpdate: (self) => { target = self.progress * 6; }
  });

  dots.forEach((d) => d.addEventListener("click", () => {
    const i = Number(d.dataset.go);
    scrollToY(st.start + ((i + 0.55) / 6) * (st.end - st.start), 1.4);
  }));

  function burstStage(o) {
    if (!fx) return;
    const r = stageEl.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return;
    fx.burst(r.left + r.width / 2, r.top + r.height * (o.y || 0.5), o);
  }

  function applyDOM(s) {
    window.__ww.s = s;
    const T = (a, b) => clamp01((s - a) / (b - a));
    const idx = Math.min(5, Math.floor(s + 1e-4));
    if (idx !== lastIdx) {
      lastIdx = idx;
      steps.forEach((el, i) => { el.classList.toggle("on", i === idx); el.classList.toggle("done", i < idx); });
      dots.forEach((el, i) => { el.classList.toggle("on", i === idx); el.setAttribute("aria-current", i === idx ? "step" : "false"); });
      photos.forEach((el) => el.classList.toggle("on", Number(el.dataset.step) === idx));
      photoBox.classList.toggle("up", idx === 1);
      caption.textContent = CAPTIONS[idx];
      stageEl.style.setProperty("--sg", STAGE_GLOW[idx]);
    }
    stepsList.style.setProperty("--p", clamp01(s / 5.5).toFixed(4));

    /* Imagine: the wish writes itself, then floats away */
    const n = Math.round(T(0.04, 0.6) * STORY_WISH.length);
    wbTyped.textContent = STORY_WISH.slice(0, n);
    wbRest.textContent = STORY_WISH.slice(n);
    const away = T(0.74, 0.96);
    ovWish.style.opacity = String(1 - away);
    ovWish.style.transform = `translateY(${(-away * 60).toFixed(1)}px) scale(${(1 - away * 0.18).toFixed(3)})`;

    /* Manufacture: progress meter */
    ovPrint.style.opacity = String(T(2.06, 2.12) * (1 - T(2.97, 3.01)));
    let ph = PRINT_PHASES.find((p) => s < p[1]) || null;
    if (s >= 2.98) { pmLabel.textContent = "Ready to ship"; pmVal.textContent = "100%"; pmBar.style.setProperty("--pp", 1); }
    else if (ph) {
      const pr = T(ph[0], ph[1]);
      pmLabel.textContent = ph[2]; pmVal.textContent = Math.round(pr * 100) + "%"; pmBar.style.setProperty("--pp", pr.toFixed(3));
    }

    /* Modify: chat bubbles */
    ovChat.style.opacity = String(T(4.0, 4.05) * (1 - T(4.94, 5.0)));
    [4.08, 4.26, 4.44].forEach((th, i) => chatEls[i] && chatEls[i].classList.toggle("in", s > th));

    /* Deploy: update ring, check, rollback wiggle */
    ovRing.style.opacity = String(T(5.0, 5.06));
    const fill = s < 5.7 ? ease(T(5.05, 5.5)) : 1 - 0.24 * Math.sin(Math.PI * T(5.7, 5.92));
    ovRing.style.setProperty("--rf", fill.toFixed(4));
    ovRing.classList.toggle("check", s > 5.55);
    ovRing.classList.toggle("rewind", s > 5.7);
    ovRing.dataset.l = s < 5.55 ? "a" : s < 5.7 ? "b" : "c";
    [5.08, 5.24, 5.44, 5.72].forEach((th, i) => ringChips[i].classList.toggle("on", s > th));

    hint.style.opacity = String(1 - T(0.03, 0.18));
    if (story) story.setProgress(s);
  }

  gsap.ticker.add((time, delta) => {
    const d = target - cur;
    if (Math.abs(d) < 0.0004 && !dirty) return;
    dirty = false;
    const k = 1 - Math.exp(-Math.min(delta, 100) / 1000 * 9);
    cur += Math.abs(d) < 0.0004 ? d : d * k;
    applyDOM(cur);
  });
  applyDOM(0);

  /* lazy 3D */
  const io = new IntersectionObserver(async (es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    loadPhotos();
    if (!GL) { stageEl.classList.add("flat"); return; }
    try {
      const mod = await import("./story3d.js");
      story = await mod.createStory($("#story-canvas"), $("#story-labels"), (ev) => {
        if (ev === "parts") burstStage({ count: 40, speed: 8, size: 9, y: 0.5 });
        if (ev === "printed") burstStage({ count: 30, speed: 6, size: 8, y: 0.45, colors: ["#FFC94D", "#FFFFFF", "#B9A8FF"] });
        if (ev === "unbox") burstStage({ count: 90, speed: 12, size: 12, y: 0.42, jitter: 90, lift: 3 });
        if (ev === "hop") burstStage({ count: 50, speed: 9, size: 10, y: 0.6, colors: ["#8B5CFF", "#B9A8FF", "#FFFFFF", "#FF8FB1"] });
        if (ev === "check") burstStage({ count: 50, speed: 9, size: 10, y: 0.16, colors: ["#3FD3A4", "#8EF0C8", "#FFFFFF", "#FFC94D"] });
      });
      story.setProgress(cur);
      story.kick();
    } catch (e) {
      window.__ww.storyError = String(e && (e.stack || e.message) || e);
      stageEl.classList.add("flat");
    }
  }, { rootMargin: "120% 0px" });
  io.observe(section);
}

/* ==========================================================================
   4. Doodle to device
   ========================================================================== */
function initGallery() {
  const grid = $("#g-grid");
  grid.innerHTML = "";
  SITE.GADGETS.forEach((g, i) => {
    const art = document.createElement("article");
    art.className = "g-card";
    art.id = "card-" + g.slug;
    art.style.setProperty("--c1", g.colors.body);
    art.style.setProperty("--c2", g.colors.accent);
    art.setAttribute("aria-labelledby", "g-" + g.slug);
    art.innerHTML = `
      <div class="g-media">
        <img class="g-studio" src="assets/img/ideas/${g.slug}-studio.webp" width="1200" height="1200" loading="lazy" decoding="async" alt="${esc(g.alt.studio)}">
        <img class="g-doodle" src="assets/img/doodles/${g.slug}.webp" width="1200" height="1200" loading="lazy" decoding="async" alt="A pen doodle of the ${esc(g.name)}, drawn in the same pose.">
        
        <span class="g-tag g-tag-r">Doodle</span>
        <span class="g-handle" role="slider" tabindex="0" aria-label="Doodle to photo: ${esc(g.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-valuetext="Doodle"><span class="g-knob"><svg aria-hidden="true"><use href="#i-drag"/></svg></span></span>
        <span class="g-glare" aria-hidden="true"></span>
      </div>
      <figure class="g-polaroid">
        <img class="g-life" src="assets/img/ideas/${g.slug}-life.webp" width="1600" height="1200" loading="lazy" decoding="async" alt="${esc(g.alt.life)}">
        <figcaption>In real life</figcaption>
      </figure>
      <div class="g-body">
        <span class="g-chip">${esc(g.chip)}</span>
        <h3 id="g-${g.slug}">${esc(g.name)}</h3>
        <p class="g-one">${esc(g.one)}</p>
        <div class="g-type"><span>You might type</span><p>\u201C${esc(g.type)}\u201D</p></div>
      </div>`;
    grid.appendChild(art);

    const media = $(".g-media", art), handle = $(".g-handle", art);
    const tagL = $(".g-tag-l", art), tagR = $(".g-tag-r", art);
    const life = $(".g-life", art), doodle = $(".g-doodle", art), studio = $(".g-studio", art);
    const chainFor = (img, list) => { let k = 0; img.addEventListener("error", () => { if (k < list.length) img.src = list[k++]; else img.style.visibility = "hidden"; }); };
    chainFor(doodle, [`assets/img/ideas/${g.slug}-studio.webp`, `assets/img/fallback/${g.slug}.webp`]);
    chainFor(studio, [`assets/img/fallback/${g.slug}.webp`]);
    life.addEventListener("error", () => { const f = $(".g-polaroid", art); if (f) f.remove(); });

    let touched = false, dragging = false;
    const setW = (v) => {
      const w = Math.max(0, Math.min(100, v));
      art.style.setProperty("--w", w.toFixed(2) + "%");
      handle.setAttribute("aria-valuenow", String(Math.round(w)));
      handle.setAttribute("aria-valuetext", w < 12 ? "Doodle" : w > 88 ? "Photo" : Math.round(w) + "% photo");
      if (tagL) tagL.style.opacity = w > 22 ? "1" : "0";
      if (tagR) tagR.style.opacity = w < 78 ? "1" : "0";
    };
    const fromX = (x) => { const r = media.getBoundingClientRect(); return ((x - r.left) / r.width) * 100; };
    media.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      touched = dragging = true; art.classList.add("dragging");
      try { media.setPointerCapture(e.pointerId); } catch (_) {}
      setW(fromX(e.clientX));
    });
    media.addEventListener("pointermove", (e) => { if (dragging) setW(fromX(e.clientX)); });
    const stop = () => { dragging = false; art.classList.remove("dragging"); };
    media.addEventListener("pointerup", stop);
    media.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", (e) => {
      const now = Number(handle.getAttribute("aria-valuenow"));
      const map = { ArrowLeft: now - 5, ArrowDown: now - 5, ArrowRight: now + 5, ArrowUp: now + 5, Home: 0, End: 100, PageDown: now - 20, PageUp: now + 20 };
      if (e.key in map) { e.preventDefault(); touched = true; setW(map[e.key]); }
    });

    if (RM || !LIBS) { setW(50); return; }
    setW(0);
    ScrollTrigger.create({
      trigger: media, start: "top 92%", end: "top 22%",
      onUpdate: (self) => {
        if (touched) return;
        const p = self.progress;
        setW(p < 0.62 ? (p / 0.62) * 100 : 100 - ((p - 0.62) / 0.38) * 50);
      }
    });
    gsap.from(art, { y: 90, opacity: 0, rotate: i % 2 ? 2.5 : -2.5, duration: 1.1, ease: "power3.out", scrollTrigger: { trigger: art, start: "top 92%", once: true } });
    gsap.from($(".g-polaroid", art), { y: -60, x: 30, rotate: 22, opacity: 0, duration: 1.2, delay: 0.35, ease: "back.out(1.7)", scrollTrigger: { trigger: art, start: "top 80%", once: true } });

    if (FINE) {
      art.addEventListener("pointermove", (e) => {
        if (dragging) return;
        const r = art.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        gsap.to(art, { rotateY: (x - 0.5) * 9, rotateX: (0.5 - y) * 7, transformPerspective: 1200, duration: 0.6, ease: "power3.out", overwrite: "auto" });
        art.style.setProperty("--gx", (x * 100).toFixed(1) + "%");
        art.style.setProperty("--gy", (y * 100).toFixed(1) + "%");
      });
      art.addEventListener("pointerleave", () => gsap.to(art, { rotateX: 0, rotateY: 0, duration: 1.1, ease: "elastic.out(1, 0.5)", overwrite: "auto" }));
    }
  });
}

/* ==========================================================================
   5. Example + price
   ========================================================================== */
function initExample() {
  const typed = $(".ex-typed");
  const pv = $("#price-val");
  const card = $("#price-card");
  if (RM || !LIBS) { card.classList.add("lit"); return; }
  const full = typed.textContent;
  const rest = document.createElement("span");
  rest.style.opacity = "0";
  typed.after(rest);
  typed.textContent = ""; rest.textContent = full;
  ScrollTrigger.create({
    trigger: ".ex-ask", start: "top 82%", once: true,
    onEnter: async () => {
      for (let i = 1; i <= full.length; i++) { typed.textContent = full.slice(0, i); rest.textContent = full.slice(i); await wait(16); }
    }
  });
  /* The price drops in character by character. It never counts up, so no other number is ever on screen. */
  const priceChars = split(pv).concat([$(".price .rs", card)]);
  priceChars.unshift(priceChars.pop());
  priceChars.forEach((c) => { c.style.display = "inline-block"; });
  gsap.set(priceChars, { yPercent: -130, opacity: 0, rotate: () => gsap.utils.random(-30, 30) });
  ScrollTrigger.create({
    trigger: card, start: "top 78%", once: true,
    onEnter: () => {
      gsap.to(priceChars, {
        yPercent: 0, opacity: 1, rotate: 0, duration: 1.05, ease: "bounce.out", stagger: 0.09, delay: 0.15,
        onComplete: () => { card.classList.add("lit"); if (fx) fx.burstAt(pv, { count: 46, speed: 9, size: 10 }); }
      });
    }
  });
}

/* The real routed circuit board, in 3D; three.js loads only when the card is near. */
function initBoard() {
  const card = $("#board-card");
  if (!card) return;
  if (!GL) { card.classList.add("flat"); return; }
  let board = null, wantDrop = false;
  const io = new IntersectionObserver(async (es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    try {
      const mod = await import("./board3d.js");
      board = await mod.createBoard($("#board-canvas"), { still: RM || !LIBS });
      if (wantDrop) board.drop();
      board.kick();
    } catch (e) { card.classList.add("flat"); }
  }, { rootMargin: "80% 0px" });
  io.observe(card);
  const go = () => { wantDrop = true; if (board) board.drop(); };
  if (LIBS && !RM) ScrollTrigger.create({ trigger: card, start: "top 70%", once: true, onEnter: go });
  else go();
}

/* ==========================================================================
   6. FAQ, final CTA, reveals, magnetic buttons
   ========================================================================== */
function initFAQ() {
  $$(".faq details").forEach((d) => {
    const sum = $("summary", d), body = $(".a", d);
    sum.addEventListener("click", (e) => {
      if (RM || !LIBS) return;
      e.preventDefault();
      if (d.open) {
        d.classList.add("closing");
        gsap.fromTo(body, { height: body.offsetHeight }, { height: 0, duration: 0.45, ease: "power3.inOut", onComplete: () => { d.open = false; d.classList.remove("closing"); body.style.height = ""; ScrollTrigger.refresh(); } });
      } else {
        d.open = true;
        gsap.fromTo(body, { height: 0 }, { height: body.scrollHeight, duration: 0.55, ease: "power3.out", onComplete: () => { body.style.height = ""; ScrollTrigger.refresh(); } });
      }
    });
  });
}

function initFinal() {
  const stars = $(".final-stars");
  const cols = ["#FFFFFF", "#FFC94D", "#B9A8FF"];
  for (let i = 0; i < 28; i++) {
    const s = document.createElement("i");
    s.style.cssText = `left:${(Math.random() * 100).toFixed(1)}%;top:${(Math.random() * 100).toFixed(1)}%;--s:${(5 + Math.random() * 12).toFixed(1)}px;--d:${(2 + Math.random() * 3).toFixed(2)}s;--dl:${(-Math.random() * 4).toFixed(2)}s;--c:${cols[i % 3]}`;
    stars.appendChild(s);
  }
  const chars = split($(".final-title [data-split]"));
  if (LIBS && !RM) {
    gsap.from(chars, { yPercent: 120, opacity: 0, rotate: 14, duration: 0.95, ease: "back.out(2.2)", stagger: 0.045, scrollTrigger: { trigger: "#final-card", start: "top 78%", once: true } });
    jiggle(chars);
    /* the shelf of six tips up into view as you scroll, then its name tags pop in one by one */
    const frame = $(".shelf-frame"), img = $(".shelf-zoom");
    gsap.fromTo(frame, { rotateX: 24, y: 90, scale: 0.9 }, { rotateX: 0, y: 0, scale: 1, ease: "none", transformPerspective: 1400,
      scrollTrigger: { trigger: "#shelf", start: "top 98%", end: "top 45%", scrub: 0.6 } });
    gsap.fromTo(img, { scale: 1.22 }, { scale: 1, ease: "none", scrollTrigger: { trigger: "#shelf", start: "top 98%", end: "bottom 60%", scrub: 0.6 } });
    gsap.from(".spot", { scale: 0, opacity: 0, duration: 0.7, ease: "back.out(2.6)", stagger: 0.11,
      scrollTrigger: { trigger: "#shelf", start: "top 62%", once: true },
      onComplete: () => { $("#shelf").classList.add("live"); } });
  } else {
    $("#shelf").classList.add("live");
  }
  $$("[data-studio-link]").forEach((a) => {
    if (a.hasAttribute("data-burst") && fx) a.addEventListener("pointerenter", () => fx.burstAt(a, { count: 18, speed: 5, size: 8 }));
    a.addEventListener("click", (e) => {
      if (!fx || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      fx.burstAt(a, { count: 90, speed: 13, size: 12 });
      setTimeout(() => { window.location.href = a.href; }, 480);
    });
  });
}

function initReveals() {
  if (RM || !LIBS) return;
  $$(".section-head, .story-head").forEach((head) => {
    gsap.from(head.children, { y: 40, opacity: 0, duration: 1, ease: "power3.out", stagger: 0.09, scrollTrigger: { trigger: head, start: "top 86%", once: true } });
  });
  $$("[data-reveal]").forEach((el) => gsap.from(el, { y: 44, opacity: 0, duration: 1, ease: "power3.out", scrollTrigger: { trigger: el, start: "top 90%", once: true } }));
  gsap.from(".mq-head > *", { y: 20, opacity: 0, duration: 0.8, stagger: 0.08, scrollTrigger: { trigger: ".marquee", start: "top 88%", once: true } });
  gsap.from(".faq details", { y: 24, opacity: 0, duration: 0.7, stagger: 0.06, ease: "power3.out", scrollTrigger: { trigger: ".faq", start: "top 85%", once: true } });
}

function initMagnetic() {
  if (!FINE || RM || !LIBS) return;
  $$(".magnetic").forEach((el) => {
    const xTo = gsap.quickTo(el, "x", { duration: 0.5, ease: "power3" });
    const yTo = gsap.quickTo(el, "y", { duration: 0.5, ease: "power3" });
    let rect = null;
    el.addEventListener("pointerenter", () => { rect = el.getBoundingClientRect(); });
    el.addEventListener("pointermove", (e) => {
      if (!rect) rect = el.getBoundingClientRect();
      xTo((e.clientX - (rect.left + rect.width / 2)) * 0.3);
      yTo((e.clientY - (rect.top + rect.height / 2)) * 0.4);
    });
    el.addEventListener("pointerleave", () => { rect = null; xTo(0); yTo(0); });
  });
}

/* ---------- boot ---------- */
initGallery();
initStory();
initMarquee();
initExample();
initBoard();
initFAQ();
initFinal();
initReveals();
initMagnetic();
initHero();
if (LIBS) {
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => ScrollTrigger.refresh());
  window.addEventListener("load", () => ScrollTrigger.refresh());
}
