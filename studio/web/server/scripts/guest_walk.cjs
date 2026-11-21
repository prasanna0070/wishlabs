// VM only: the magic-link guest flow in headless Chrome (app/guest.py). Reads the magic-link PATH from $LINK_FILE
// (never printed), opens it, shows the showcase list, opens a showcase (banner, no composer), makes its own wish,
// then hits the daily wish limit (run the server with GUEST_DAILY_PROJECTS=1). Screenshots to $OUTDIR.
//   NODE_PATH=/opt/gs/pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers BASE=http://127.0.0.1:9301 \
//   LINK_FILE=/tmp/ml-walk/link SEED=/tmp/ml-walk/seed.json OUTDIR=/tmp/ml-walk/shots node scripts/guest_walk.cjs
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const base = process.env.BASE || "http://127.0.0.1:9301";
  const link = fs.readFileSync(process.env.LINK_FILE, "utf8").trim();
  const seed = JSON.parse(fs.readFileSync(process.env.SEED, "utf8"));
  const out = process.env.OUTDIR || "/tmp/ml-walk/shots";
  fs.mkdirSync(out, { recursive: true });
  const res = { errors: [] };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => res.errors.push(e.message));
  try {
    await page.goto(base + link);
    res.landed = new URL(page.url()).pathname;
    await page.waitForSelector(".proj-grid", { timeout: 20000 });
    const text = await page.locator("body").innerText();
    res.showcase_label = await page.locator(".proj-card.is-showcase .showcase-tag").count();
    res.secret_visible = text.includes("CONFIDENTIAL");
    res.guest_note = (await page.locator(".guest-note").innerText()).replace(/\s+/g, " ");
    res.heading = await page.locator("h1").innerText();
    await page.screenshot({ path: `${out}/1-guest-home.png`, fullPage: true });

    await page.click(".proj-card.is-showcase a");
    await page.waitForSelector(".showcase-banner", { timeout: 20000 });
    await page.waitForTimeout(2500); // the viewer settles
    res.showcase_url = new URL(page.url()).pathname;
    res.composer_in_showcase = await page.locator(".chat-bottom textarea").count();
    res.banner = (await page.locator(".showcase-banner").innerText()).replace(/\s+/g, " ");
    await page.screenshot({ path: `${out}/2-showcase-view-only.png` });

    const secret = await page.request.get(`${base}/api/projects/${seed.secret}`);
    res.secret_status = secret.status();

    await page.goto(`${base}/studio/`);
    await page.waitForSelector(".hero textarea", { timeout: 20000 });
    await page.fill(".hero textarea", "A pocket fan that clips onto a backpack strap.");
    await page.click(".send-btn");
    await page.waitForURL(/\/studio\/p\/p_/, { timeout: 20000 });
    res.own_project = new URL(page.url()).pathname;
    await page.waitForTimeout(4000);

    await page.goto(`${base}/studio/`);
    await page.waitForSelector(".hero textarea", { timeout: 20000 });
    await page.fill(".hero textarea", "A desk clock that shows the tide.");
    await page.click(".send-btn");
    await page.waitForSelector(".callout.danger", { timeout: 20000 });
    res.limit_message = (await page.locator(".callout.danger").innerText()).replace(/\s+/g, " ");
    res.guest_note_after = (await page.locator(".guest-note").innerText()).replace(/\s+/g, " ");
    await page.screenshot({ path: `${out}/3-limit-reached.png`, fullPage: true });
  } catch (e) {
    res.failed = String(e && e.message ? e.message : e).slice(0, 400);
    await page.screenshot({ path: `${out}/x-failure.png` }).catch(() => {});
  }
  await browser.close();
  console.log(JSON.stringify(res, null, 1));
})();
