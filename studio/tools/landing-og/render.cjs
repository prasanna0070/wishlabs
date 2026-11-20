// Renders the landing's link-preview card and icons from site assets. Runs on the temp VM only:
//   cd studio/tools/landing-og && PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node render.cjs <outDir>
// Writes og-1200x630.png, icon-256.png, icon-32.png, apple-touch-icon-180.png into <outDir> (then make-assets.py
// turns them into the JPEG and the .ico under studio/site/assets/img/).
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const path = require("path");
const fs = require("fs");
const OUT = process.argv[2] || "out";
fs.mkdirSync(OUT, { recursive: true });
const here = (f) => "file://" + path.join(__dirname, f);
(async () => {
  const browser = await chromium.launch();
  const og = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await og.goto(here("og.html"), { waitUntil: "networkidle" });
  await og.evaluate(() => document.fonts.ready);
  await og.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0));
  const fam = await og.evaluate(() => [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family));
  console.log("fonts loaded:", [...new Set(fam)].join(", ") || "NONE");
  await og.screenshot({ path: path.join(OUT, "og-1200x630.png") });
  for (const [name, size, full] of [["icon-256.png", 256, 0], ["icon-32.png", 32, 0], ["apple-touch-icon-180.png", 180, 1]]) {
    const p = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    await p.goto(here("icon.html") + (full ? "?full=1" : ""), { waitUntil: "load" });
    await p.screenshot({ path: path.join(OUT, name), omitBackground: !full });
    await p.close();
  }
  await browser.close();
  console.log("wrote", fs.readdirSync(OUT).join(", "));
})().catch((e) => { console.error(e); process.exit(1); });
