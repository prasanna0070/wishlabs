// Section view check: the Preview tab of a read-only share link at 1440x900 and 390x844. Runs on the temp VM only.
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node section_shots.cjs <base> <token> <outDir>
// <base> is a server origin (http://127.0.0.1:9245 on the VM). It only reads: GETs of the share page, its API and
// files. Per size: the whole model, then Section view on (Y, 50 %), X at 40 %, Z flipped, and Y with Exploded view.
// Each shot also counts the canvas pixels in the cut colour (the viewer draws cut faces in the accent violet), so
// "the screenshot shows the cut" is a number in report.json and not only a picture; console errors are recorded too.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const [BASE, TOKEN, OUT] = process.argv.slice(2);
if (!BASE || !TOKEN || !OUT) {
  console.error("usage: node section_shots.cjs <base> <token> <outDir>");
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

function instrument(page, rec) {
  page.on("console", (m) => (m.type() === "error" || m.type() === "warning") && rec.console.push(`${m.type()}: ${m.text().slice(0, 300)}`));
  page.on("pageerror", (e) => rec.pageerrors.push(String(e).slice(0, 300)));
  page.on("requestfailed", (r) => rec.failed.push(`${r.failure()?.errorText} ${r.method()} ${r.url().slice(0, 200)}`));
  page.on("response", (r) => r.status() >= 400 && rec.http.push(`${r.status()} ${r.request().method()} ${r.url().slice(0, 200)}`));
}

// pixels of the viewer canvas in the cut colour (#6D4BFF after tone mapping: blue well above red and green)
async function cutPixels(page) {
  return page.evaluate(() => {
    const c = document.querySelector(".preview .viewer canvas");
    if (!c || !c.width) return null;
    const tmp = document.createElement("canvas");
    tmp.width = c.width;
    tmp.height = c.height;
    const g = tmp.getContext("2d");
    g.drawImage(c, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 200 && d[i + 2] > 150 && d[i + 2] - d[i + 1] > 90 && d[i + 2] - d[i] > 60) n++;
    return { cut: n, total: c.width * c.height, permille: Math.round((n / (c.width * c.height)) * 10000) / 10 };
  });
}

async function layout(page) {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom) };
    };
    const ctl = box(".section-ctl");
    return {
      vw,
      pageOverflowX: document.documentElement.scrollWidth > vw,
      toggles: [...document.querySelectorAll(".view-toggles label.check")].map((l) => l.textContent.trim()),
      togglesBox: box(".view-toggles"),
      sectionCtl: ctl,
      sectionCtlOffScreen: ctl ? ctl.right > vw + 0.5 || ctl.left < -0.5 : null,
      stage: box(".preview .stage"),
      slider: document.querySelector(".section-ctl input[type=range]")?.getAttribute("aria-valuetext") || null,
      pressed: [...document.querySelectorAll(".section-ctl button[aria-pressed=true]")].map((b) => b.textContent.trim()),
    };
  });
}

async function shot(page, rec, name) {
  await sleep(1600); // frameloop is "demand": give the canvas a few frames after a change
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, timeout: 60000 }).catch((e) => rec.notes.push(`shot ${name}: ${String(e).split("\n")[0]}`));
  const px = await cutPixels(page).catch((e) => ({ error: String(e).slice(0, 200) }));
  rec.shots.push({ name, px, layout: await layout(page) });
  log("shot", name, JSON.stringify(px));
}

// click a point of the canvas (fractions of its box) and return the part the viewer selected (its edit card), if any
async function pickAt(page, fx, fy) {
  const b = await page.locator(".preview .viewer canvas").boundingBox();
  await page.mouse.click(b.x + b.width * fx, b.y + b.height * fy);
  await sleep(700);
  const name = await page.locator(".edit-pop .mono").first().textContent({ timeout: 2000 }).catch(() => null);
  if (name) await page.locator('.edit-pop button[aria-label="Close"]').click().catch(() => undefined);
  return name;
}

async function setSlider(page, v) {
  await page.evaluate((x) => {
    const el = document.querySelector(".section-ctl input[type=range]");
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(el, String(x));
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, v);
}

async function run(browser, size) {
  const rec = { size, shots: [], console: [], pageerrors: [], failed: [], http: [], notes: [] };
  report.runs.push(rec);
  const ctx = await browser.newContext(SIZES[size]);
  const page = await ctx.newPage();
  instrument(page, rec);
  await page.goto(`${BASE}/studio/s/${TOKEN}/preview`, { waitUntil: "domcontentloaded", timeout: 90000 });
  rec.modelReady = await page.waitForSelector('.preview .viewer[data-ready="1"]', { timeout: 120000 }).then(() => true, () => false);
  await sleep(1500);
  await shot(page, rec, `${size}-1-whole`);
  await page.locator(".view-toggles label.check", { hasText: "Section view" }).click();
  await page.waitForSelector(".section-ctl", { timeout: 10000 });
  await shot(page, rec, `${size}-2-section-y50`);
  // picking goes through the cut: the same point selects the shell when whole and an inner part when cut (desktop
  // only: the point is where the board sits in the 1440x900 layout of the demo project)
  if (size === "desktop") rec.pickCut = await pickAt(page, 0.526, 0.643);
  await page.locator('.section-ctl button[aria-label="Cut across the width (X)"]').click();
  await setSlider(page, 0.4);
  await shot(page, rec, `${size}-3-section-x40`);
  await page.locator('.section-ctl button[aria-label="Cut across the height (Z)"]').click();
  await setSlider(page, 0.5);
  await page.locator(".section-ctl button", { hasText: "Flip" }).click();
  await shot(page, rec, `${size}-4-section-z50-flip`);
  await page.locator('.section-ctl button[aria-label="Cut across the depth (Y)"]').click();
  await page.locator(".section-ctl button", { hasText: "Flip" }).click();
  await page.locator(".view-toggles label.check", { hasText: "Exploded view" }).click();
  await shot(page, rec, `${size}-5-section-y50-exploded`);
  await page.locator(".view-toggles label.check", { hasText: "Exploded view" }).click();
  // see-through shell with the cut: the shell's cut colour hides (it would cover the inside), inner parts keep it
  await page.locator(".view-toggles label.check", { hasText: "Transparent" }).click();
  await shot(page, rec, `${size}-5b-section-y50-transparent`);
  await page.locator(".view-toggles label.check", { hasText: "Transparent" }).click();
  await page.locator(".view-toggles label.check", { hasText: "Section view" }).click();
  await shot(page, rec, `${size}-6-section-off-again`);
  if (size === "desktop") rec.pickWhole = await pickAt(page, 0.526, 0.643);
  await ctx.close();
  save();
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  try {
    for (const size of ["desktop", "phone"]) await run(browser, size).catch((e) => report.runs.push({ size, crash: String(e).slice(0, 600) }));
  } finally {
    report.ended = new Date().toISOString();
    save();
    await browser.close();
    log("done", path.join(OUT, "report.json"));
  }
})();
