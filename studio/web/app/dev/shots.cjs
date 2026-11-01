// Drive the SPA end to end against a dev backend and screenshot every tab and state (run on the VM).
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/shots.cjs [baseUrl] [outDir]
// Backend: web/app/dev/serve.sh (AGENT=scripted, BUILDER=fake, ACCESS_KEY=dev).
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8090";
const OUT = process.argv[3] || path.join(__dirname, "..", "_out", "shots");
const ONLY = process.env.ONLY ? process.env.ONLY.split(",") : null;
fs.mkdirSync(OUT, { recursive: true });

const errors = [];
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function shot(page, name, opts = {}) {
  if (ONLY && !ONLY.some((o) => name.startsWith(o))) return;
  await page.waitForTimeout(opts.wait ?? 400);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: !!opts.full, timeout: 90000 });
  log("shot", name);
}

async function waitModel(page, timeout = 60000) {
  // the viewer marks data-ready="1" once the GLB is parsed; give SwiftShader a few frames after
  await page.waitForSelector('.viewer[data-ready="1"]', { timeout }).catch(() => log("model not ready in time"));
  await page.waitForTimeout(2500);
}

async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    log("STEP FAILED", name, e.message.split("\n")[0]);
    errors.push({ type: "step", name, text: e.message.split("\n")[0] });
  }
}

(async () => {
  const browser = await chromium.launch({
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("console", (m) => {
    // the login page's /api/me probe answers 401 by design (it is how the SPA learns it is signed out)
    if (/status of 401/.test(m.text()) && /\/login$/.test(page.url())) return;
    if (m.type() === "error" || m.type() === "warning") errors.push({ type: m.type(), text: m.text().slice(0, 300), url: page.url() });
  });
  page.on("pageerror", (e) => errors.push({ type: "pageerror", text: String(e).slice(0, 300), url: page.url() }));
  page.on("requestfailed", (r) => {
    if (!/favicon/.test(r.url())) errors.push({ type: "requestfailed", text: `${r.failure()?.errorText} ${r.url()}`, url: page.url() });
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && !/\/api\/me$/.test(r.url())) errors.push({ type: `http ${r.status()}`, text: r.url(), url: page.url() });
  });

  // ---------------------------------------------------------------- login + home
  await page.goto(`${BASE}/login`);
  await page.waitForSelector("#key");
  await shot(page, "01-login");
  await page.fill("#key", "dev");
  await page.click("button.btn.primary");
  await page.waitForSelector(".hero h1");
  await page.waitForTimeout(600);
  await shot(page, "02-home");

  // ---------------------------------------------------------------- new project via the prompt box
  await page.click(".examples .chip >> nth=0");
  await shot(page, "03-home-example");
  await page.click(".home-composer .send-btn");
  await page.waitForURL(/\/p\/p_/);
  const pid = page.url().match(/\/p\/(p_[a-z0-9]+)/)[1];
  log("project", pid);

  await step("clarify", async () => {
    await page.waitForSelector(".dock .q", { timeout: 60000 });
    await shot(page, "04-chat-clarify");
    const qs = await page.$$(".dock .q");
    for (const q of qs) {
      const opt = await q.$(".q-opt:nth-child(2)") || (await q.$(".q-opt"));
      await opt.click();
    }
    const sendBtn = await page.$(".dock .dock-foot .btn.primary");
    if (sendBtn) await sendBtn.click();
  });

  await step("concepts", async () => {
    await page.waitForSelector(".concepts-dock .tile", { timeout: 120000 });
    await page.waitForTimeout(1500);
    await shot(page, "05-chat-concepts");
    await page.click(".concepts-dock .tile >> nth=0");
  });

  await step("brief", async () => {
    await page.waitForSelector(".brief-dock", { timeout: 120000 });
    await page.waitForTimeout(800);
    await shot(page, "06-chat-brief-unread");
    await page.$eval(".brief-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await page.waitForTimeout(500);
    await shot(page, "07-chat-brief-read");
    await page.click(".brief-dock .btn.primary");
  });

  await step("building", async () => {
    await page.waitForSelector(".build-card", { timeout: 60000 });
    await page.waitForTimeout(2500);
    await shot(page, "08-building");
  });

  await step("result", async () => {
    await page.waitForSelector(".msg.result", { timeout: 180000 });
    await waitModel(page);
    await shot(page, "09-result-preview");
    await page.click(".progress-disc summary");
    await page.waitForTimeout(1200);
    await page.$eval(".chat-list", (el) => (el.scrollTop = el.scrollHeight));
    await shot(page, "10-result-design-progress");
  });

  // ---------------------------------------------------------------- preview toggles
  const toggle = async (label) => page.click(`.view-toggles label:has-text("${label}") input`);
  await step("preview toggles", async () => {
    await toggle("Exploded view");
    await page.waitForTimeout(1800);
    await shot(page, "11-preview-exploded");
    await toggle("Exploded view");
    await toggle("Separate parts");
    await page.waitForTimeout(1800);
    await shot(page, "12-preview-separate");
    await toggle("Separate parts");
    await toggle("Transparent");
    await page.waitForTimeout(800);
    await shot(page, "13-preview-transparent");
    await toggle("Transparent");
    await toggle("Dimensions");
    await toggle("Size reference");
    await page.waitForTimeout(2500);
    await shot(page, "14-preview-dims-sizeref");
    await toggle("Dimensions");
    await toggle("Size reference");
    await toggle("Model parts");
    await page.waitForTimeout(1500);
    await shot(page, "15-preview-model-parts");
    await toggle("Model parts");
  });

  await step("part edit", async () => {
    const box = await page.locator(".stage canvas").boundingBox();
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.waitForSelector(".edit-pop", { timeout: 5000 });
    await page.fill(".edit-pop textarea", "Make this part a little rounder");
    await shot(page, "16-preview-part-edit");
    await page.click(".edit-pop .icon-btn");
  });

  await step("tweak", async () => {
    await page.click(".view-toggles .btn");
    await page.waitForSelector(".tweak .swatches", { timeout: 10000 });
    await page.click(".tweak .sw >> nth=1");
    await page.waitForTimeout(1200);
    await shot(page, "17-preview-tweak");
    await page.click(".tweak-foot .btn >> text=Check");
    await page.waitForTimeout(2500);
    await shot(page, "18-preview-tweak-checked");
    await page.click(".tweak-head .icon-btn");
  });

  // ---------------------------------------------------------------- other tabs
  const tab = async (name) => {
    await page.click(`.tabs .tab:has-text("${name}")`);
    await page.waitForTimeout(1500);
  };
  await step("parts", async () => {
    await tab("Parts");
    await shot(page, "20-parts");
    await page.click(".checks-card .btn");
    await page.waitForTimeout(500);
    await shot(page, "21-parts-checks", { full: false });
    const ev = await page.$(".parts-table .icon-btn");
    if (ev) {
      await ev.click();
      await shot(page, "22-parts-evidence");
      await page.keyboard.press("Escape");
    }
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await shot(page, "23-parts-bottom");
  });
  await step("order", async () => {
    await tab("Order");
    await shot(page, "30-order");
    const segs = await page.$$(".seg button");
    if (segs[2]) await segs[2].click();
    const part = await page.$(".seg-part");
    if (part) await part.hover();
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = 420));
    await shot(page, "31-order-scenario");
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    const pin = await page.$("#pin");
    if (pin) {
      await page.fill("#pin", "500032");
      await page.click(".request .btn.primary");
      await page.waitForTimeout(1500);
      await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    }
    await shot(page, "32-order-request");
  });
  await step("build assembly", async () => {
    await tab("Build");
    await waitModel(page);
    await shot(page, "40-build-assembly");
    const next = await page.$(".step-card .btn.primary");
    if (next) {
      await next.click();
      await page.waitForTimeout(2500);
      await shot(page, "41-build-assembly-step2");
    }
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = 560));
    const row = await page.$(".cs-title >> nth=1");
    if (row) await row.click();
    await page.waitForTimeout(1200);
    await shot(page, "42-build-checklist");
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await shot(page, "43-build-tools");
    await page.click(".tools-tabs button >> nth=2");
    await shot(page, "44-build-selftest");
    await page.click(".tools-tabs button >> nth=3");
    await shot(page, "45-build-reports");
  });
  await step("build wiring", async () => {
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = 0));
    await page.click('.subtab-group button:has-text("Wiring")');
    await waitModel(page);
    await shot(page, "46-build-wiring");
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = 900));
    await page.waitForTimeout(800);
    await shot(page, "47-build-wiring-table");
  });
  await step("manual", async () => {
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = 0));
    await page.click('.sec-head .btn:has-text("Complete manual")');
    await page.waitForTimeout(1500);
    await shot(page, "48-build-manual");
    await page.keyboard.press("Escape");
  });
  await step("pcb", async () => {
    await tab("PCB");
    await page.waitForTimeout(1500);
    await shot(page, "50-pcb-top");
    await page.click('.seg button:has-text("3D board")');
    await waitModel(page);
    await shot(page, "51-pcb-3d");
    await page.click('.seg button:has-text("Schematic")');
    await page.waitForTimeout(1500);
    await shot(page, "52-pcb-schematic");
  });
  await step("code", async () => {
    await tab("Code");
    await page.waitForSelector(".shiki-wrap", { timeout: 20000 }).catch(() => log("highlighter not ready"));
    await page.waitForTimeout(800);
    await shot(page, "60-code");
  });

  // ---------------------------------------------------------------- header actions
  await step("header", async () => {
    await tab("Preview");
    await waitModel(page);
    await page.click('button[aria-label^="Version history"]');
    await shot(page, "70-version-history");
    await page.keyboard.press("Escape");
    await page.click('.ws-header .btn:has-text("Project brief")');
    await shot(page, "71-brief-sheet", { wait: 600 });
    await page.keyboard.press("Escape");
    await page.click('.ws-header .btn:has-text("Share")');
    await page.click('.dialog .btn.primary:has-text("Create link")');
    await shot(page, "72-share", { wait: 1200 });
    const url = await page.inputValue('.dialog input[aria-label="Share link"]');
    await page.keyboard.press("Escape");
    await page.click('button[aria-label="Hide chat"]');
    await waitModel(page);
    await shot(page, "73-chat-hidden");
    await page.click('button[aria-label="Show chat"]');
    // read-only share view in a fresh context (no cookie)
    const anon = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const p2 = await anon.newPage();
    p2.on("pageerror", (e) => errors.push({ type: "pageerror(share)", text: String(e).slice(0, 300) }));
    await p2.goto(url);
    await waitModel(p2);
    await shot(p2, "74-share-readonly");
    await anon.close();
  });

  await step("laptop 1280", async () => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitModel(page);
    await shot(page, "80-laptop-1280");
  });
  await step("mobile", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(1500);
    await shot(page, "81-mobile");
    await page.goto(`${BASE}/`);
    await page.waitForTimeout(1200);
    await shot(page, "82-mobile-home");
  });

  fs.writeFileSync(path.join(OUT, "console-errors.json"), JSON.stringify(errors, null, 1));
  const real = errors.filter((e) => e.type !== "warning");
  log(`done: ${errors.length} console/network messages (${real.length} errors). See ${OUT}/console-errors.json`);
  for (const e of real.slice(0, 40)) log("ERR", e.type, e.text);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
