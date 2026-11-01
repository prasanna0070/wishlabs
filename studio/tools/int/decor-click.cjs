// REVIEW-1 MAJ-35 e2e (integrator): click across the Preview model until a click opens "Edit ears" (a decor pick mesh),
// then check the popover names the decor spec path. Read-only: closes the popover, sends nothing.
//   PID=p_... PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node decor-click.cjs <baseUrl> <outDir>
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8096";
const OUT = process.argv[3] || path.join(__dirname, "decor-click");
const PID = process.env.PID;
const KEY = process.env.KEY || "gstudio-dev";
const WANT = new RegExp(process.env.WANT || "^Edit (ears|ear)\\b", "i");
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

(async () => {
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(`${BASE}/login`);
  await page.fill("#key", KEY);
  await page.click("button.btn.primary");
  await page.waitForSelector(".hero h1");
  await page.goto(`${BASE}/p/${PID}`);
  await page.waitForSelector('.viewer[data-ready="1"]', { timeout: 120000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: path.join(OUT, "00-preview.png") });
  const box = await page.locator(".stage canvas").boundingBox();
  const seen = {};
  let hit = null;
  outer: for (let fy = 0.08; fy <= 0.6; fy += 0.035) {
    for (let fx = 0.2; fx <= 0.8; fx += 0.03) {
      await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
      await page.waitForTimeout(250);
      const t = await page.$eval(".edit-pop strong", (el) => el.textContent).catch(() => null);
      if (t) {
        seen[t] = (seen[t] || 0) + 1;
        if (WANT.test(t)) {
          const sub = await page.$eval(".edit-pop .muted.small", (el) => el.textContent).catch(() => "");
          hit = { title: t, sub, at: [Math.round(fx * 100) / 100, Math.round(fy * 100) / 100] };
          await page.screenshot({ path: path.join(OUT, "01-edit-ears-popover.png") });
          break outer;
        }
        await page.click(".edit-pop .icon-btn").catch(() => {});
        await page.waitForTimeout(120);
      }
    }
  }
  const res = { want: String(WANT), hit, popovers_seen: seen, ok: !!(hit && /decor\[\d+\]/.test(hit.sub || "")) };
  fs.writeFileSync(path.join(OUT, "result.json"), JSON.stringify(res, null, 1));
  log(JSON.stringify(res));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
