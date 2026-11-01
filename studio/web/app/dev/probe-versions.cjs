// Exercise the version flow on an existing project (run on the VM after shots.cjs):
// apply a tweak (POST /tweak -> new version builds -> becomes current), undo, redo, restore from history.
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/probe-versions.cjs <base> <pid> <outdir>
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const path = require("path");
const [BASE, PID, OUT] = process.argv.slice(2);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
(async () => {
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && !/status of 401/.test(m.text()) && errors.push(m.text().slice(0, 200)));
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
  const shot = async (name) => {
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), timeout: 90000 });
    log("shot", name);
  };
  const ready = () => page.waitForSelector('.viewer[data-ready="1"]', { timeout: 90000 }).catch(() => log("model not ready"));
  await page.goto(`${BASE}/login`);
  await page.fill("#key", "dev");
  await page.click("button.btn.primary");
  await page.waitForSelector(".hero h1");
  await page.goto(`${BASE}/p/${PID}`);
  await ready();

  // tweak: colour + roundness, apply as a new version
  await page.click(".view-toggles .btn");
  await page.waitForSelector(".tweak .swatches");
  await page.click(".tweak .sw >> nth=2");
  await page.fill('.tweak-foot input[aria-label="Version label"]', "Sky blue and rounder");
  await page.click('.tweak-foot .btn.primary');
  log("applied tweak");
  await page.waitForSelector(".tweak", { state: "detached", timeout: 30000 });
  await page.waitForTimeout(2500);
  await shot("90-tweak-building");
  await page.waitForFunction(() => !document.querySelector(".taking-shape"), null, { timeout: 120000 });
  await ready();
  await shot("91-tweak-done-v2");

  // undo / redo
  await page.click('button[aria-label="Undo design change"]');
  await page.waitForFunction(() => /v1/.test(document.querySelector('button[aria-label^="Version history"]')?.textContent || ""), null, { timeout: 30000 });
  await ready();
  await shot("92-undo-v1");
  await page.click('button[aria-label="Redo design change"]');
  await page.waitForFunction(() => /v2/.test(document.querySelector('button[aria-label^="Version history"]')?.textContent || ""), null, { timeout: 30000 });
  log("redo ok");

  // restore v1 from version history -> v3
  await page.click('button[aria-label^="Version history"]');
  await shot("93-history-two-versions");
  await page.click('.ver-row:has(.ver-n:text("v1")) .btn');
  await page.waitForFunction(() => /v3/.test(document.querySelector('button[aria-label^="Version history"]')?.textContent || ""), null, { timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector(".taking-shape"), null, { timeout: 120000 });
  await ready();
  await page.click('button[aria-label^="Version history"]');
  await shot("94-history-after-restore");
  log("errors", JSON.stringify(errors));
  await browser.close();
})().catch((e) => {
  console.error("FAILED", e.message.split("\n")[0]);
  process.exit(1);
});
