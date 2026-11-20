// Judge's-eye visual audit of the live landing + studio (hack-ready sweep, 2026-11-20). Runs on the temp VM only.
//   printf '%s' "$KEY" | PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node audit.cjs <outDir>
// The access key arrives on stdin and is only ever passed to fetch("/api/login"); it is never logged or shown.
// Signed-in contexts abort every non-GET /api call except POST /api/login, so the walk cannot change any project.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const OUT = process.argv[2];
fs.mkdirSync(OUT, { recursive: true });
let KEY = "";
try { KEY = fs.readFileSync(0, "utf8").trim(); } catch { KEY = ""; }
const LANDING = "https://wish.plugbook.si/";
const STUDIO = "https://gstudio-web-xxxxxxxxxx-el.a.run.app";
const PID_OVERRIDE = process.env.PID || "";
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean);
const want = (k) => !ONLY.length || ONLY.includes(k);
const t0 = Date.now();
const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6) + "s", ...a);

const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
  userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36" };

const report = { started: new Date().toISOString(), pages: [], shots: [], notes: [], blocked: [] };
const save = () => fs.writeFileSync(path.join(OUT, "audit.json"), JSON.stringify(report, null, 2));

async function shot(page, name, opts = {}) {
  const file = path.join(OUT, `${name}.png`);
  try {
    await page.screenshot({ path: file, fullPage: !!opts.full, timeout: 120000, ...(opts.clip ? { clip: opts.clip } : {}) });
    report.shots.push({ name, at: page.url(), caption: opts.caption || "" });
    log("shot", name);
  } catch (e) {
    report.notes.push(`shot ${name} failed: ${String(e).split("\n")[0]}`);
    log("SHOT FAIL", name, String(e).split("\n")[0]);
  }
}

const newRec = (label) => ({ label, console: [], pageerrors: [], failed: [], http: [], bytes: 0, requests: 0, byType: {}, big: [] });
async function instrument(page) {
  page.__rec = newRec("init");
  page.__inflight = new Set();
  const streamy = (r) => r.resourceType() === "eventsource" || /\/stream(\?|$)|\/events(\?|$)/.test(r.url());
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") page.__rec.console.push({ type: m.type(), text: m.text().slice(0, 400), at: page.url() });
  });
  page.on("pageerror", (e) => page.__rec.pageerrors.push({ text: String(e).slice(0, 400), at: page.url() }));
  page.on("request", (r) => { if (!streamy(r)) page.__inflight.add(r); });
  page.on("requestfinished", (r) => page.__inflight.delete(r));
  page.on("requestfailed", (r) => {
    page.__inflight.delete(r);
    page.__rec.failed.push(`${r.failure()?.errorText} ${r.method()} ${r.url().slice(0, 220)}`);
  });
  page.on("response", (r) => { if (r.status() >= 400) page.__rec.http.push(`${r.status()} ${r.request().method()} ${r.url().slice(0, 220)}`); });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  const meta = new Map();
  cdp.on("Network.responseReceived", (e) => meta.set(e.requestId, { type: e.type, url: e.response.url }));
  cdp.on("Network.loadingFinished", (e) => {
    const r = page.__rec;
    const m = meta.get(e.requestId) || {};
    const t = m.type || "Other";
    r.bytes += e.encodedDataLength;
    r.requests++;
    r.byType[t] = (r.byType[t] || 0) + e.encodedDataLength;
    if (e.encodedDataLength > 150000) r.big.push(`${Math.round(e.encodedDataLength / 1024)} KB ${t} ${String(m.url || "").slice(0, 160)}`);
  });
  page.__cdp = cdp;
  return page;
}

async function quiet(page, ms = 800, max = 25000) {
  const s = Date.now();
  let lastBusy = Date.now();
  while (Date.now() - s < max) {
    if (page.__inflight.size > 0) lastBusy = Date.now();
    else if (Date.now() - lastBusy >= ms) return Date.now() - s - ms;
    await page.waitForTimeout(100);
  }
  return -1;
}

async function perf(page) {
  return page.evaluate(() => new Promise((resolve) => {
    let lcp = null;
    try {
      new PerformanceObserver((l) => { const es = l.getEntries(); if (es.length) lcp = es[es.length - 1]; })
        .observe({ type: "largest-contentful-paint", buffered: true });
    } catch { /* no LCP */ }
    setTimeout(() => {
      const nav = performance.getEntriesByType("navigation")[0] || {};
      const paint = Object.fromEntries(performance.getEntriesByType("paint").map((p) => [p.name, Math.round(p.startTime)]));
      const res = performance.getEntriesByType("resource");
      const desc = (el) => !el ? null : el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
        (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".") : "");
      resolve({
        ttfb: Math.round(nav.responseStart || 0), dcl: Math.round(nav.domContentLoadedEventEnd || 0), load: Math.round(nav.loadEventEnd || 0),
        fp: paint["first-paint"] ?? null, fcp: paint["first-contentful-paint"] ?? null,
        lcp: lcp ? Math.round(lcp.startTime) : null, lcpEl: lcp ? (desc(lcp.element) || lcp.url || null) : null,
        navTransfer: nav.transferSize ?? null, resTransferVisible: res.reduce((a, r) => a + (r.transferSize || 0), 0), nRes: res.length,
        docH: document.documentElement.scrollHeight, title: document.title,
      });
    }, 400);
  }));
}

async function scan(page) {
  return page.evaluate(() => {
    const txt = document.body ? document.body.innerText : "";
    const pats = [/lorem/i, /\bTODO\b/, /\bTBD\b/, /\bFIXME\b/, /placeholder/i, /\bundefined\b/, /\bNaN\b/, /\bnull\b/,
      /\[object [A-Za-z]+\]/, /\{\{.*?\}\}/, /gadget[\s_-]*studio/i, /localhost|127\.0\.0\.1/i, /example\.com/i, /\bdebug\b/i,
      /quarktex/i, /plugbook/i, /Infinity/, /₹\s*0(\b|\.)/, /\btest\b/i, /\bdummy\b/i, /\bmock\b/i, /coming soon/i, /error/i, /failed/i];
    const hits = [];
    for (const p of pats) {
      const re = new RegExp(p.source, p.flags.includes("g") ? p.flags : p.flags + "g");
      let m; let k = 0;
      while ((m = re.exec(txt)) && k < 3) { hits.push({ pat: String(p), ctx: txt.slice(Math.max(0, m.index - 70), m.index + 90).replace(/\s+/g, " ") }); k++; }
    }
    const broken = [...document.images].filter((i) => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src)).map((i) => i.currentSrc || i.src).slice(0, 20);
    const small = [];
    const seen = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode()) && small.length < 20) {
      const t = n.textContent.trim();
      if (t.length < 3) continue;
      const el = n.parentElement;
      if (!el || seen.has(el)) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || cs.visibility === "hidden" || cs.display === "none") continue;
      const f = parseFloat(cs.fontSize);
      if (f < 11.5) small.push({ fs: f, t: t.slice(0, 70), cls: String(el.className || "").slice(0, 60) });
    }
    const w = window.innerWidth;
    const sw = document.documentElement.scrollWidth;
    const bw = document.body.scrollWidth;
    const wide = [];
    for (const el of document.querySelectorAll("body *")) {
      if (wide.length >= 12) break;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && (r.right > w + 2 || r.left < -2)) {
        const cs = getComputedStyle(el);
        if (cs.position === "fixed" && cs.visibility === "hidden") continue;
        let a = el.parentElement; let clipped = false;
        while (a && a !== document.body) { const o = getComputedStyle(a).overflowX; if (o === "hidden" || o === "auto" || o === "scroll" || o === "clip") { clipped = true; break; } a = a.parentElement; }
        if (!clipped) wide.push({ tag: el.tagName.toLowerCase(), cls: String(el.className || "").slice(0, 60), l: Math.round(r.left), r: Math.round(r.right) });
      }
    }
    return { title: document.title, hits, broken, small, overflow: { sw, bw, w }, wide, textLen: txt.length };
  });
}

async function guard(ctx) {
  await ctx.route(/\/api\//, (route) => {
    const r = route.request();
    const m = r.method();
    const p = new URL(r.url()).pathname;
    if (m === "GET" || m === "HEAD" || (m === "POST" && p === "/api/login")) return route.continue();
    report.blocked.push(`${m} ${p}`);
    return route.abort("blockedbyclient");
  });
}

async function scrollThrough(page) {
  const H = await page.evaluate(() => document.documentElement.scrollHeight);
  const vh = await page.evaluate(() => window.innerHeight);
  for (let y = 0; y < H; y += Math.round(vh * 0.6)) {
    await page.evaluate((yy) => window.scrollTo(0, yy), y);
    await page.waitForTimeout(220);
  }
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(800);
}

async function landing(browser, dev, opts) {
  const ctx = await browser.newContext(opts);
  const page = await instrument(await ctx.newPage());
  page.__rec = newRec(`${dev} landing load`);
  const s = Date.now();
  let loadMs = null;
  try { await page.goto(LANDING, { waitUntil: "load", timeout: 90000 }); loadMs = Date.now() - s; } catch (e) { report.notes.push(`${dev} landing goto: ${String(e).split("\n")[0]}`); }
  await shot(page, `${dev}-landing-01-first-viewport-at-load`, { caption: `first viewport at the load event (${loadMs} ms)` });
  const quietMs = await quiet(page, 800, 20000);
  await page.waitForTimeout(6000);
  await shot(page, `${dev}-landing-02-first-viewport-settled`, { caption: "first viewport after the hero settles (~7 s)" });
  const p = await perf(page);
  await scrollThrough(page);
  const pos = await page.evaluate(() => {
    const sec = document.querySelector("#how");
    if (!sec) return [];
    const top = sec.getBoundingClientRect().top + window.scrollY;
    const h = sec.offsetHeight;
    const span = Math.max(h - window.innerHeight, 0);
    return [0, 1, 2, 3, 4, 5].map((i) => Math.round(top + (span > window.innerHeight ? (span * (i + 0.3)) / 6 : (i * h) / 6)));
  });
  for (let i = 0; i < pos.length; i++) {
    await page.evaluate((yy) => window.scrollTo(0, yy), pos[i]);
    await page.waitForTimeout(1800);
    await shot(page, `${dev}-landing-1${i}-story-pos${i + 1}`, { caption: `scroll story position ${i + 1} (y=${pos[i]})` });
  }
  let k = 20;
  for (const [sel, nm] of [["#gadgets", "gallery"], ["#example", "example"], ["#faq", "faq"], [".final", "final"]]) {
    const ok = await page.evaluate((s2) => { const el = document.querySelector(s2); if (!el) return false; window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 40); return true; }, sel);
    if (!ok) continue;
    await page.waitForTimeout(1600);
    await shot(page, `${dev}-landing-${k++}-${nm}`, { caption: `section ${sel} in view` });
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(1500);
  const sc = await scan(page);
  await shot(page, `${dev}-landing-00-full`, { full: true, caption: "full page" });
  const docH = await page.evaluate(() => document.documentElement.scrollHeight);
  const seg = dev === "d" ? 1800 : 1400;
  for (let y = 0, i = 1; y < docH && i <= 16; y += seg, i++) {
    await shot(page, `${dev}-landing-00-full-seg${String(i).padStart(2, "0")}`, { full: true, clip: { x: 0, y, width: opts.viewport.width, height: Math.min(seg, docH - y) }, caption: `full page y=${y}..${Math.min(y + seg, docH)}` });
  }
  report.pages.push({ ...page.__rec, url: LANDING, device: dev, loadMs, quietMs, perf: p, scan: sc });
  save();
  // The main call to action: type a wish, press the button, see where the judge lands (signed out; nothing is created).
  page.__rec = newRec(`${dev} landing CTA -> studio`);
  try {
    await page.fill("#wish-input", "a lamp shaped like a cloud that glows blue the evening before it rains");
    await page.waitForTimeout(400);
    await shot(page, `${dev}-landing-30-wish-typed`, { caption: "wish typed in the hero box" });
    const c = Date.now();
    await Promise.all([page.waitForURL(/\/studio\/login/, { timeout: 40000 }), page.click(".wish-go")]);
    await page.waitForSelector(".login-card", { timeout: 40000 });
    const ctaMs = Date.now() - c;
    await page.waitForTimeout(1200);
    await shot(page, `${dev}-landing-31-cta-lands-on-login`, { caption: `after Make a wish (${ctaMs} ms to the login card)` });
    report.pages.push({ ...page.__rec, url: page.url(), device: dev, ctaMs, scan: await scan(page) });
  } catch (e) {
    report.notes.push(`${dev} CTA: ${String(e).split("\n")[0]} (url ${page.url()})`);
    await shot(page, `${dev}-landing-31-cta-FAILED`);
    report.pages.push({ ...page.__rec, url: page.url(), device: dev });
  }
  save();
  await ctx.close();
}

async function studioLogin(browser, dev, opts) {
  const ctx = await browser.newContext(opts);
  const page = await instrument(await ctx.newPage());
  page.__rec = newRec(`${dev} studio login (signed out)`);
  const s = Date.now();
  await page.goto(`${STUDIO}/studio/login`, { waitUntil: "load", timeout: 90000 });
  const loadMs = Date.now() - s;
  const cardMs = await page.waitForSelector(".login-card", { timeout: 30000 }).then(() => Date.now() - s).catch(() => null);
  await quiet(page, 800, 15000);
  await page.waitForTimeout(800);
  const p = await perf(page);
  await shot(page, `${dev}-studio-01-login`, { caption: `studio login, signed out (card visible at ${cardMs} ms)` });
  await shot(page, `${dev}-studio-01-login-full`, { full: true, caption: "studio login, full page" });
  report.pages.push({ ...page.__rec, url: page.url(), device: dev, loadMs, cardMs, perf: p, scan: await scan(page) });
  save();
  await ctx.close();
}

async function tabs(page, dev) {
  const names = ["Parts", "Order", "Build", "PCB", "Code"];
  for (let i = 0; i < names.length; i++) {
    const name = names[i];
    page.__rec = newRec(`${dev} tab ${name}`);
    const s = Date.now();
    try {
      await page.click(`.tabs .tab:has-text("${name}")`, { timeout: 10000 });
    } catch (e) {
      report.notes.push(`${dev} tab ${name} click failed: ${String(e).split("\n")[0]}`);
      continue;
    }
    let readyMs = null;
    if (name === "Build" || name === "PCB") {
      readyMs = await page.waitForSelector('.viewer[data-ready="1"]', { state: "visible", timeout: 60000 }).then(() => Date.now() - s).catch(() => null);
    }
    const q = await quiet(page, 800, 30000);
    await page.waitForTimeout(2200);
    const tag = `${dev}-studio-${21 + i}-tab-${name.toLowerCase()}`;
    await shot(page, tag, { caption: `${name} tab` });
    const sc = await page.evaluate(() => {
      const el = [...document.querySelectorAll(".tab-scroll")].find((e) => e.offsetParent !== null);
      return el ? { sh: el.scrollHeight, ch: el.clientHeight } : null;
    });
    if (sc && sc.sh > sc.ch + 40) {
      const steps = Math.min(3, Math.ceil((sc.sh - sc.ch) / sc.ch));
      for (let k = 1; k <= steps; k++) {
        await page.evaluate((frac) => {
          const el = [...document.querySelectorAll(".tab-scroll")].find((e) => e.offsetParent !== null);
          el.scrollTop = Math.round((el.scrollHeight - el.clientHeight) * frac);
        }, k / steps);
        await page.waitForTimeout(1000);
        await shot(page, `${tag}-scroll${k}`, { caption: `${name} tab scrolled ${k}/${steps}` });
      }
      await page.evaluate(() => { const el = [...document.querySelectorAll(".tab-scroll")].find((e) => e.offsetParent !== null); if (el) el.scrollTop = 0; });
    }
    report.pages.push({ ...page.__rec, url: page.url(), device: dev, clickToQuietMs: q, readyMs, tabScroll: sc, scan: await scan(page) });
    save();
  }
}

async function signedIn(browser, dev, opts, storageState) {
  const ctx = await browser.newContext({ ...opts, ...(storageState ? { storageState } : {}) });
  await guard(ctx);
  const page = await instrument(await ctx.newPage());
  if (!storageState) {
    await page.goto(`${STUDIO}/studio/login`, { waitUntil: "load", timeout: 90000 });
    await page.waitForSelector(".login-card", { timeout: 30000 });
    const st = await page.evaluate(async (k) => {
      const r = await fetch("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ key: k }), credentials: "same-origin" });
      return r.status;
    }, KEY);
    report.notes.push(`login status ${st}`);
    if (st !== 200) { await ctx.close(); return null; }
  }
  // home
  page.__rec = newRec(`${dev} studio home (signed in)`);
  const s = Date.now();
  await page.goto(`${STUDIO}/studio/`, { waitUntil: "load", timeout: 90000 });
  const heroMs = await page.waitForSelector(".home-composer, .hero h1", { timeout: 30000 }).then(() => Date.now() - s).catch(() => null);
  await quiet(page, 800, 20000);
  await page.waitForTimeout(1500);
  const p = await perf(page);
  await shot(page, `${dev}-studio-10-home`, { caption: `studio home, signed in (composer visible at ${heroMs} ms)` });
  await shot(page, `${dev}-studio-11-home-full`, { full: true, caption: "studio home, full page (project list)" });
  report.pages.push({ ...page.__rec, url: page.url(), device: dev, heroMs, perf: p, scan: await scan(page) });
  save();
  // choose a finished project
  let pid = PID_OVERRIDE;
  if (!report.projects) {
    const list = await page.evaluate(() => fetch("/api/projects", { credentials: "same-origin" }).then((r) => r.json()));
    const rows = list.projects || [];
    report.projects = rows.map((r) => ({ id: r.id, name: r.name, stage: r.stage, cv: r.current_version, done: !!(r.thumb_url && r.thumb_url.includes("/v/")), updated: r.updated }));
    const done = rows.filter((r) => r.thumb_url && r.thumb_url.includes("/v/"));
    if (!pid) pid = (done[0] || {}).id;
    report.chosen = { pid };
    if (pid) {
      const d = await page.evaluate((x) => fetch(`/api/projects/${x}`, { credentials: "same-origin" }).then((r) => r.json()), pid);
      const cv = (d.versions || []).find((v) => v.n === d.current_version) || {};
      report.chosen = { pid, name: d.name, stage: d.stage, current_version: d.current_version, cvState: cv.state, cvSource: cv.source,
        versions: (d.versions || []).map((v) => `${v.n}:${v.state}:${v.source}`) };
    }
    save();
  } else pid = report.chosen && report.chosen.pid;
  if (!pid) { report.notes.push("no finished project found"); await ctx.close(); return null; }
  // project, preview tab
  page.__rec = newRec(`${dev} project preview`);
  const s2 = Date.now();
  await page.goto(`${STUDIO}/studio/p/${pid}`, { waitUntil: "load", timeout: 90000 });
  const shellMs = await page.waitForSelector(".ws-header", { timeout: 30000 }).then(() => Date.now() - s2).catch(() => null);
  const modelMs = await page.waitForSelector('.viewer[data-ready="1"]', { timeout: 90000 }).then(() => Date.now() - s2).catch(() => null);
  await quiet(page, 800, 30000);
  await page.waitForTimeout(2500);
  const p2 = await perf(page);
  await shot(page, `${dev}-studio-20-project-preview`, { caption: `project, Preview tab (header at ${shellMs} ms, 3D model ready at ${modelMs} ms)` });
  if (dev === "m") {
    // on a phone the chat sits under the 3D panel; show what is below the fold of the app shell
    const chatScroll = await page.evaluate(() => { const el = document.querySelector(".chat-list"); return el ? { sh: el.scrollHeight, ch: el.clientHeight } : null; });
    report.notes.push(`m chat-list ${JSON.stringify(chatScroll)}`);
  }
  report.pages.push({ ...page.__rec, url: page.url(), device: dev, shellMs, modelMs, perf: p2, scan: await scan(page) });
  save();
  await tabs(page, dev);
  const st = await ctx.storageState();
  await ctx.close();
  return st;
}

async function throttled(browser, url, name, readySel) {
  const ctx = await browser.newContext(PHONE);
  const page = await instrument(await ctx.newPage());
  page.__rec = newRec(`m slow-4G + 4x CPU: ${name}`);
  await page.__cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 });
  await page.__cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  const s = Date.now();
  let readyMs = null;
  const nav = page.goto(url, { waitUntil: "load", timeout: 120000 }).then(() => Date.now() - s).catch((e) => String(e).split("\n")[0]);
  const ready = page.waitForSelector(readySel, { state: "visible", timeout: 120000 }).then(() => { readyMs = Date.now() - s; }).catch(() => {});
  const film = [];
  for (const m of [1000, 2500, 4000, 6000, 9000]) {
    const w = s + m - Date.now();
    if (w > 0) await new Promise((r) => setTimeout(r, w));
    const at = Date.now() - s;
    await shot(page, `m-slow4g-${name}-t${String(m).padStart(5, "0")}`, { caption: `${name} on slow 4G + 4x CPU, shot started at ${at} ms` });
    film.push(at);
  }
  const loadMs = await nav;
  await ready;
  await page.waitForTimeout(1000);
  const p = await perf(page).catch(() => null);
  report.pages.push({ ...page.__rec, url, device: "m-slow4g-cpu4x", loadMs, readyMs, film, perf: p });
  save();
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] });
  report.browser = browser.version();
  const run = async (k, fn) => { if (!want(k)) return; try { await fn(); } catch (e) { report.notes.push(`${k} crashed: ${String(e).split("\n")[0]}`); log("CRASH", k, String(e).split("\n")[0]); save(); } };
  await run("landing-d", () => landing(browser, "d", DESKTOP));
  await run("landing-m", () => landing(browser, "m", PHONE));
  await run("wish-studio", async () => {
    const ctx = await browser.newContext(DESKTOP);
    const page = await instrument(await ctx.newPage());
    page.__rec = newRec("d wish.plugbook.si/studio/");
    const r = await page.goto("https://wish.plugbook.si/studio/", { waitUntil: "load", timeout: 60000 }).catch(() => null);
    await page.waitForTimeout(800);
    await shot(page, "d-wish-studio-path", { caption: `https://wish.plugbook.si/studio/ -> HTTP ${r && r.status()}` });
    report.pages.push({ ...page.__rec, url: page.url(), device: "d", status: r && r.status(), body: (await page.evaluate(() => document.body.innerText)).slice(0, 200) });
    save();
    await ctx.close();
  });
  await run("login-d", () => studioLogin(browser, "d", DESKTOP));
  await run("login-m", () => studioLogin(browser, "m", PHONE));
  let state = null;
  if (KEY) {
    await run("studio-d", async () => { state = await signedIn(browser, "d", DESKTOP, null); });
    if (state) await run("studio-m", () => signedIn(browser, "m", PHONE, state));
  } else report.notes.push("no access key on stdin: signed-in pages skipped");
  await run("slow4g", async () => {
    await throttled(browser, LANDING, "landing", "#hero-title");
    await throttled(browser, `${STUDIO}/studio/login`, "login", ".login-card");
  });
  report.finished = new Date().toISOString();
  report.seconds = Math.round((Date.now() - t0) / 1000);
  save();
  await browser.close();
  log("done", report.shots.length, "shots");
})().catch((e) => { report.crash = String(e); save(); console.error("CRASH", e); process.exit(2); });
