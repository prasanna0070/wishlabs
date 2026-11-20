// Landing check against a local copy (hack/landing, 2026-11-20). Runs on the temp VM only:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node landing-check.cjs <outDir> [baseUrl]
// baseUrl defaults to http://127.0.0.1:9204/ (the site served by its own nginx image). Takes screenshots at 1440x900
// and 390x844, records console errors, page errors, failed requests and HTTP errors, checks every link and asset, and
// runs a second pass with the config switches on (SHARE_URL, NEW_FEATURES) by rewriting js/config.js in the browser
// only. Nothing is written anywhere except <outDir>.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const OUT = process.argv[2] || "landing-check-out";
const BASE = process.argv[3] || "http://127.0.0.1:9204/";
const LIVE = "https://wish.plugbook.si/";
const DEMO_SHARE = "/studio/s/DEMO-TOKEN";
fs.mkdirSync(OUT, { recursive: true });
const t0 = Date.now();
const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6) + "s", ...a);
const report = { base: BASE, started: new Date().toISOString(), runs: [], links: [], asserts: [], shots: [] };
const save = () => fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
const check = (name, ok, detail = "") => { report.asserts.push({ name, ok: !!ok, detail }); log(ok ? "PASS" : "FAIL", name, detail); };

const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
  userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36" };

async function shot(page, name, opts = {}) {
  const file = path.join(OUT, name + ".png");
  try {
    if (opts.el) await (await page.$(opts.el)).screenshot({ path: file, timeout: 60000 });
    else await page.screenshot({ path: file, timeout: 60000 });
    report.shots.push({ name, caption: opts.caption || "" });
    log("shot", name);
  } catch (e) { report.asserts.push({ name: "shot " + name, ok: false, detail: String(e).split("\n")[0] }); }
}

function instrument(page, rec) {
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") rec.console.push(`${m.type()}: ${m.text().slice(0, 300)}`); });
  page.on("pageerror", (e) => rec.pageerrors.push(String(e).slice(0, 300)));
  page.on("requestfailed", (r) => rec.failed.push(`${r.failure() && r.failure().errorText} ${r.url().slice(0, 200)}`));
  page.on("response", (r) => { if (r.status() >= 400) rec.http.push(`${r.status()} ${r.url().slice(0, 200)}`); });
}

/* The switches are flipped in the browser only: js/config.js is served with SHARE_URL and NEW_FEATURES set. */
async function flagsOn(ctx) {
  await ctx.route(/\/js\/config\.js(\?|$)/, async (route) => {
    const res = await route.fetch();
    let body = await res.text();
    body = body.replace(/var SHARE_URL = "[^"]*";/, `var SHARE_URL = "${DEMO_SHARE}";`).replace(/var NEW_FEATURES = false;/, "var NEW_FEATURES = true;");
    await route.fulfill({ response: res, body });
  });
}

async function storyAt(page, s) {
  const y = await page.evaluate((sv) => {
    const st = window.__ww && window.__ww.ScrollTrigger && window.__ww.ScrollTrigger.getAll().find((t) => t.pin);
    if (!st) return null;
    return Math.round(st.start + (sv / 6) * (st.end - st.start));
  }, s);
  if (y == null) return null;
  await page.evaluate((yy) => window.scrollTo(0, yy), y);
  await page.waitForTimeout(1800);
  return page.evaluate(() => window.__ww && window.__ww.s);
}

async function scrollTo(page, sel, offset = 80) {
  return page.evaluate(([s, o]) => {
    const el = document.querySelector(s);
    if (!el) return false;
    window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - o);
    return true;
  }, [sel, offset]);
}

async function scrollThrough(page) {
  const H = await page.evaluate(() => document.documentElement.scrollHeight);
  const vh = await page.evaluate(() => window.innerHeight);
  for (let y = 0; y < H; y += Math.round(vh * 0.7)) { await page.evaluate((yy) => window.scrollTo(0, yy), y); await page.waitForTimeout(160); }
  await page.waitForTimeout(800);
}

const visible = (page, sel) => page.evaluate((s) => [...document.querySelectorAll(s)].map((el) => {
  const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
  return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
}), sel);

async function run(browser, dev, opts, flags, rm = false) {
  const ctx = await browser.newContext({ ...opts, reducedMotion: rm ? "reduce" : "no-preference" });
  if (flags) await flagsOn(ctx);
  const page = await ctx.newPage();
  const rec = { dev, flags, rm, console: [], pageerrors: [], failed: [], http: [] };
  instrument(page, rec);
  const tag = `${dev}${rm ? "-rm" : ""}${flags ? "-on" : ""}`;
  await page.goto(BASE, { waitUntil: "load", timeout: 90000 });
  await page.waitForTimeout(rm ? 1500 : 7000);

  rec.title = await page.title();
  rec.price = await page.evaluate(() => [document.querySelector(".price").innerText.replace(/\s+/g, " ").trim(), (document.querySelector(".price .sr-only") || {}).textContent]);
  rec.claude = await page.evaluate(() => /claude/i.test(document.documentElement.outerHTML));
  rec.oldPrice = await page.evaluate(() => /7,826|7826/.test(document.documentElement.outerHTML));
  const shareVis = await visible(page, "[data-share]");
  const newVis = await visible(page, "[data-new-features]");
  const shareHrefs = await page.evaluate(() => [...document.querySelectorAll("a[data-share-link]")].map((a) => a.getAttribute("href")));
  if (flags) {
    check(`${tag}: all 3 "See a real build" blocks visible`, shareVis.length === 3 && shareVis.every(Boolean), JSON.stringify(shareVis));
    check(`${tag}: share links point at SHARE_URL`, shareHrefs.length === 3 && shareHrefs.every((h) => h === DEMO_SHARE), JSON.stringify(shareHrefs));
    check(`${tag}: new-features block visible`, newVis.length === 1 && newVis[0], JSON.stringify(newVis));
  } else {
    check(`${tag}: "See a real build" hidden while SHARE_URL is empty`, shareVis.length === 3 && shareVis.every((v) => !v), JSON.stringify(shareVis));
    check(`${tag}: new-features block hidden while NEW_FEATURES is false`, newVis.length === 1 && !newVis[0], JSON.stringify(newVis));
  }
  check(`${tag}: no "Claude" anywhere in the page`, !rec.claude);
  check(`${tag}: old price 7,826 gone`, !rec.oldPrice);

  if (!flags) {
    await shot(page, `${tag}-01-hero`, { caption: "first viewport, settled" });
    if (dev === "d" && !rm) {
      await scrollTo(page, "#how", -200); await page.waitForTimeout(1500);
      await shot(page, `${tag}-10-story-approach`, { caption: "How it works, before the pin: the YOUR WISH card (was an empty white box)" });
      for (const s of [0.3, 2.03, 2.05, 2.35, 4.5]) {
        const got = await storyAt(page, s);
        await shot(page, `${tag}-1${String(s).replace(".", "")}-story-s${s}`, { caption: `pinned story at s=${got && got.toFixed ? got.toFixed(3) : got}` });
      }
    } else if (!rm) {
      await scrollTo(page, "#how", 60); await page.waitForTimeout(1200);
      await shot(page, `${tag}-10-how-head`, { caption: "How it works intro" });
      await scrollTo(page, '.story-step[data-step="4"]', 120); await page.waitForTimeout(1500);
      await shot(page, `${tag}-11-step5`, { caption: "step 5 copy (no Claude)" });
    }
    await scrollTo(page, "#price-card", dev === "d" ? 160 : 100); await page.waitForTimeout(rm ? 600 : 3200);
    await shot(page, `${tag}-20-price`, { caption: "price card" });
    const cost = await page.$('.faq details:nth-child(3) summary');
    if (cost) {
      await scrollTo(page, ".faq details:nth-child(3)", 200); await page.waitForTimeout(800);
      await cost.click(); await page.waitForTimeout(1200);
      await shot(page, `${tag}-21-faq-cost`, { caption: "FAQ: what does it cost" });
    }
    if (!rm) {
      await scrollTo(page, "#shelf", dev === "d" ? 520 : 420); await page.waitForTimeout(1800);
      await shot(page, `${tag}-22-final-labels-pop`, { caption: "final shelf as its name tags pop in (edge labels used to clip)" });
    }
    await scrollTo(page, "#shelf", dev === "d" ? 120 : 80); await page.waitForTimeout(1800);
    await shot(page, `${tag}-23-final-shelf`, { caption: "final shelf in full view" });
  } else {
    await shot(page, `${tag}-30-hero-share`, { caption: 'hero with "See a real build" (flags on)' });
    await scrollTo(page, "#how", dev === "d" ? -120 : 60); await page.waitForTimeout(1600);
    await shot(page, `${tag}-31-how-new-features`, { caption: "How it works intro with the new-features block (flags on)" });
    await scrollTo(page, ".ex-cta", dev === "d" ? 520 : 460); await page.waitForTimeout(2600);
    await shot(page, `${tag}-32-example-cta`, { caption: 'price card + "See a real build" (flags on)' });
    await scrollTo(page, ".final-card", dev === "d" ? -60 : 0); await page.waitForTimeout(2200);
    await shot(page, `${tag}-33-final-alt`, { caption: 'final card with "Or see a real build first" (flags on)' });
    const nav = await page.evaluate(() => { const a = document.querySelector(".hero-actions a[data-share-link]"); return a ? a.href : null; });
    check(`${tag}: hero share button resolves to the share path`, nav && nav.endsWith(DEMO_SHARE), nav);
  }

  rec.scan = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, w: window.innerWidth }));
  check(`${tag}: no horizontal overflow`, rec.scan.sw <= rec.scan.w + 1, JSON.stringify(rec.scan));
  if (!flags && !rm) {
    await scrollThrough(page);
    rec.urls = await page.evaluate(() => {
      const u = new Set();
      document.querySelectorAll("a[href]").forEach((a) => u.add(a.href));
      document.querySelectorAll("img").forEach((i) => { if (i.currentSrc || i.src) u.add(i.currentSrc || i.src); if (i.dataset.src) u.add(new URL(i.dataset.src, location.href).href); });
      document.querySelectorAll("link[href]").forEach((l) => { if (!/preconnect/.test(l.rel)) u.add(l.href); });
      document.querySelectorAll("script[src]").forEach((s) => u.add(s.src));
      document.querySelectorAll('meta[property="og:image"], meta[name="twitter:image"], meta[property="og:url"]').forEach((m) => u.add(m.content));
      return [...u];
    });
  }
  rec.console = [...new Set(rec.console)];
  report.runs.push(rec);
  check(`${tag}: 0 console errors`, !rec.console.some((c) => c.startsWith("error")), rec.console.join(" | ").slice(0, 400));
  check(`${tag}: 0 page errors`, rec.pageerrors.length === 0, rec.pageerrors.join(" | ").slice(0, 400));
  check(`${tag}: 0 failed requests and 0 HTTP errors`, rec.failed.length === 0 && rec.http.length === 0, [...rec.failed, ...rec.http].join(" | ").slice(0, 400));
  save();
  await ctx.close();
  return rec;
}

async function linkCheck(browser, urls) {
  const ctx = await browser.newContext();
  const base = new URL(BASE);
  const seen = new Set();
  for (const raw of urls) {
    if (!raw || raw.startsWith("data:") || raw.startsWith("mailto:")) continue;
    const u = new URL(raw, BASE); u.hash = "";
    let target = u.href;
    let note = "";
    if (u.origin === new URL(LIVE).origin) { target = new URL(u.pathname + u.search, BASE).href; note = "live URL checked on the local copy"; }
    const tu = new URL(target);
    if (tu.origin === base.origin && tu.pathname.startsWith("/studio")) { target = new URL(tu.pathname + tu.search, LIVE).href; note = "studio route: checked on the live domain (load balancer)"; }
    if (seen.has(target)) continue;
    seen.add(target);
    let status = null;
    for (let i = 0; i < 2 && status == null; i++) {
      try { const r = await ctx.request.get(target, { timeout: 30000, maxRedirects: 5 }); status = r.status(); } catch (e) { note += ` ${String(e).split("\n")[0].slice(0, 120)}`; }
    }
    report.links.push({ url: raw, checked: target, status, note: note.trim() });
  }
  await ctx.close();
  const bad = report.links.filter((l) => l.status !== 200);
  check(`links: ${report.links.length} unique URLs, all 200`, bad.length === 0, bad.map((b) => `${b.status} ${b.checked}`).join(" | "));
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  report.browser = browser.version();
  const d = await run(browser, "d", DESKTOP, false);
  await run(browser, "m", PHONE, false);
  await run(browser, "d", DESKTOP, true);
  await run(browser, "m", PHONE, true);
  await run(browser, "d", DESKTOP, false, true);
  await linkCheck(browser, d.urls || []);
  await browser.close();
  report.seconds = Math.round((Date.now() - t0) / 1000);
  report.failed = report.asserts.filter((a) => !a.ok).length;
  save();
  log("done:", report.asserts.length, "checks,", report.failed, "failed,", report.shots.length, "shots");
})().catch((e) => { report.crash = String(e && e.stack || e); save(); console.error("CRASH", e); process.exit(2); });
