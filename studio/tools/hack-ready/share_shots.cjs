// Screenshots and checks of a read-only share link (Overview tab) at 1440x900 and 390x844. Runs on the temp VM only.
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node share_shots.cjs <base> <token> <outDir> [errorToken]
// <base> is a server origin (http://127.0.0.1:9203 on the VM, or https://wish.plugbook.si). It only reads: GETs of
// the share page, its API and files. [errorToken] is a shared project whose current version failed (friendly error).
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const [BASE, TOKEN, OUT, ERR_TOKEN] = process.argv.slice(2);
if (!BASE || !TOKEN || !OUT) {
  console.error("usage: node share_shots.cjs <base> <token> <outDir> [errorToken]");
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });
const t0 = Date.now();
const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6) + "s", ...a);
const SIZES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  phone: {
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
  },
};
const report = { base: BASE.replace(/\/\/[^/]*@/, "//"), started: new Date().toISOString(), runs: [] };
const save = () => fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(page, rec, name) {
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, timeout: 60000 }).catch((e) => rec.notes.push(`shot ${name}: ${String(e).split("\n")[0]}`));
  rec.shots.push(name);
  log("shot", name);
}

function instrument(page, rec) {
  page.on("console", (m) => (m.type() === "error" || m.type() === "warning") && rec.console.push(`${m.type()}: ${m.text().slice(0, 300)}`));
  page.on("pageerror", (e) => rec.pageerrors.push(String(e).slice(0, 300)));
  page.on("requestfailed", (r) => rec.failed.push(`${r.failure()?.errorText} ${r.method()} ${r.url().slice(0, 200)}`));
  page.on("response", (r) => r.status() >= 400 && rec.http.push(`${r.status()} ${r.request().method()} ${r.url().slice(0, 200)}`));
}

async function layoutChecks(page) {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const tabs = [...document.querySelectorAll(".tabs .tab")].map((t) => {
      const r = t.getBoundingClientRect();
      return { label: t.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right), on: t.classList.contains("on") };
    });
    const ov = document.querySelector(".ov-scroll");
    const chat = document.querySelector(".chat");
    return {
      vw,
      pageOverflowX: document.documentElement.scrollWidth > vw,
      overviewOverflowX: ov ? ov.scrollWidth > ov.clientWidth + 1 : null,
      tabs,
      tabsOffScreen: tabs.filter((t) => t.right > vw + 0.5 || t.left < -0.5).map((t) => t.label),
      activeTab: tabs.find((t) => t.on)?.label || null,
      chatVisible: !!chat && getComputedStyle(chat).display !== "none",
      receiptsRows: document.querySelectorAll(".ov-receipts .rc-row:not(.rc-head)").length,
      receiptsText: (document.querySelector(".ov-receipts")?.textContent || "").replace(/\s+/g, " ").slice(0, 600),
      priceText: (document.querySelector("#ov-price")?.textContent || "").replace(/\s+/g, " ").slice(0, 400),
      heroText: (document.querySelector(".ov-hero-text")?.textContent || "").replace(/\s+/g, " ").slice(0, 600),
      engineeringText: (document.querySelector("#ov-engineering")?.textContent || "").replace(/\s+/g, " ").slice(0, 900),
      headerText: (document.querySelector(".ws-header")?.textContent || "").replace(/\s+/g, " ").slice(0, 200),
      concepts: document.querySelectorAll(".ov-concept").length,
      mounted: { voice: !!document.querySelector("#ov-voice"), sound: !!document.querySelector("#ov-sound") },
      voiceCards: document.querySelectorAll(".vw-card").length,
      voiceText: (document.querySelector("#ov-voice")?.textContent || "").replace(/\s+/g, " ").slice(0, 500),
      voiceAudio: document.querySelector("#ov-voice audio")?.getAttribute("src") || null,
      soundText: (document.querySelector("#ov-sound")?.textContent || "").replace(/\s+/g, " ").slice(0, 500),
      soundComposeButtons: [...document.querySelectorAll("#ov-sound button")].filter((b) => /compose/i.test(b.textContent || "")).length,
    };
  });
}

async function compression(page) {
  return page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .filter((e) => /\/assets\/.*\.(js|css)$|\.glb$|manifest\.json$|build\.json$/.test(e.name))
      .map((e) => ({ url: e.name.replace(location.origin, "").slice(0, 120), encoded: e.encodedBodySize, decoded: e.decodedBodySize }))
      .sort((a, b) => b.decoded - a.decoded)
      .slice(0, 8),
  );
}

async function overview(browser, size) {
  const rec = { kind: "overview", size, shots: [], console: [], pageerrors: [], failed: [], http: [], notes: [] };
  report.runs.push(rec);
  const ctx = await browser.newContext(SIZES[size]);
  const page = await ctx.newPage();
  instrument(page, rec);
  const t = Date.now();
  await page.goto(`${BASE}/studio/s/${TOKEN}`, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForSelector(".ov-hero", { timeout: 90000 });
  await page.waitForFunction(() => [...document.querySelectorAll(".ov-photo img, .ov-concept img")].every((i) => i.complete), null, { timeout: 60000 }).catch(() => rec.notes.push("images still loading"));
  rec.overviewMs = Date.now() - t;
  await sleep(1200);
  await shot(page, rec, `${size}-1-top`);
  rec.layout = await layoutChecks(page);
  for (const id of ["ov-voice", "ov-concepts", "ov-model", "ov-sound", "ov-engineering", "ov-price", "ov-ai"]) {
    // the feature cards (ov-voice, ov-sound) render nothing when the project has no spoken wish or sound
    const present = await page.evaluate((x) => (document.getElementById(x)?.getBoundingClientRect().height || 0) > 0, id);
    if (!present) {
      rec.notes.push(`${id}: not shown`);
      continue;
    }
    await page.evaluate((x) => document.getElementById(x)?.scrollIntoView({ block: "start" }), id);
    if (id === "ov-model") {
      const ok = await page.waitForSelector('.ov-viewer .viewer[data-ready="1"]', { timeout: 90000 }).then(() => true, () => false);
      rec.modelReady = ok;
      await sleep(1500);
    } else if (id === "ov-engineering") {
      await page.waitForFunction(() => !/Loading the file list/.test(document.querySelector("#ov-engineering")?.textContent || ""), null, { timeout: 30000 }).catch(() => rec.notes.push("file list still loading"));
      await sleep(800);
    } else await sleep(700);
    await shot(page, rec, `${size}-2-${id}`);
  }
  rec.compression = await compression(page);
  rec.exportHref = await page.getAttribute(".ov-files a[download]", "href").catch(() => null);
  if (size === "phone") {
    const btn = page.locator(".ov-chat-link");
    rec.chatLinkVisible = await btn.isVisible();
    if (rec.chatLinkVisible) {
      await page.evaluate(() => document.getElementById("ov-wish")?.scrollIntoView({ block: "start" }));
      await btn.click();
      await page.waitForURL(/\/preview$/, { timeout: 15000 }).catch(() => rec.notes.push("chat link did not open preview"));
      await sleep(1500);
      rec.afterChatLink = await layoutChecks(page);
      await shot(page, rec, `${size}-3-preview-with-chat`);
    }
  }
  await ctx.close();
  save();
}

async function errorProject(browser, size) {
  const rec = { kind: "error-project", size, shots: [], console: [], pageerrors: [], failed: [], http: [], notes: [] };
  report.runs.push(rec);
  const ctx = await browser.newContext(SIZES[size]);
  const page = await ctx.newPage();
  instrument(page, rec);
  await page.goto(`${BASE}/studio/s/${ERR_TOKEN}/parts`, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForSelector(".err-text", { timeout: 60000 }).catch(() => rec.notes.push("no .err-text on the failed version"));
  await sleep(1000);
  rec.errorTexts = await page.$$eval(".err-text", (els) => els.map((e) => e.textContent.replace(/\s+/g, " ").slice(0, 300)));
  await shot(page, rec, `${size}-4-error-collapsed`);
  const sum = page.locator(".panel .err-details summary").first();
  if (await sum.count()) {
    await sum.click();
    await sleep(500);
    await shot(page, rec, `${size}-5-error-details`);
  }
  await page.goto(`${BASE}/studio/s/${ERR_TOKEN}`, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForSelector(".ov-hero", { timeout: 60000 }).catch(() => rec.notes.push("error project: no overview"));
  await sleep(1500);
  rec.layout = await layoutChecks(page);
  await shot(page, rec, `${size}-6-error-project-overview`);
  await ctx.close();
  save();
}

async function login(browser, size) {
  const rec = { kind: "login", size, shots: [], console: [], pageerrors: [], failed: [], http: [], notes: [] };
  report.runs.push(rec);
  const ctx = await browser.newContext(SIZES[size]);
  const page = await ctx.newPage();
  instrument(page, rec);
  await page.goto(`${BASE}/studio/login`, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForSelector(".login-card", { timeout: 60000 });
  await sleep(1200);
  rec.demoHref = await page.getAttribute(".login-demo a", "href").catch(() => null);
  await shot(page, rec, `${size}-7-login`);
  rec.compression = await compression(page);
  await ctx.close();
  save();
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  try {
    for (const size of ["desktop", "phone"]) {
      await overview(browser, size).catch((e) => report.runs.push({ kind: "overview", size, crash: String(e).slice(0, 400) }));
      if (ERR_TOKEN) await errorProject(browser, size).catch((e) => report.runs.push({ kind: "error-project", size, crash: String(e).slice(0, 400) }));
      await login(browser, size).catch((e) => report.runs.push({ kind: "login", size, crash: String(e).slice(0, 400) }));
    }
  } finally {
    report.ended = new Date().toISOString();
    save();
    await browser.close();
    log("done", path.join(OUT, "report.json"));
  }
})();
