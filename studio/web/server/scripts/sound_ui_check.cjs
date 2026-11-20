// Browser check of the SoundCard (VM only): the owner's Preview tab and the read-only share page, desktop and phone.
// Run after scripts/sound_e2e.py (it writes the project and share token to $E2E):
//   NODE_PATH=/opt/gs/pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers \
//   BASE=http://127.0.0.1:9202 KEY_FILE=/path/to/test-key E2E=/tmp/lyria-e2e.json OUTDIR=/tmp/lyria-shots \
//   node scripts/sound_ui_check.cjs
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const base = process.env.BASE || "http://127.0.0.1:9202";
  const key = fs.readFileSync(process.env.KEY_FILE, "utf8").trim();
  const e2e = JSON.parse(fs.readFileSync(process.env.E2E || "/tmp/lyria-e2e.json", "utf8"));
  const out = process.env.OUTDIR || "/tmp/lyria-shots";
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const res = {};
  const errors = [];
  const watch = (page, tag) => {
    page.on("console", (m) => m.type() === "error" && errors.push(`${tag}: ${m.text()}`));
    page.on("pageerror", (e) => errors.push(`${tag}: ${e.message}`));
  };
  const pressed = (card, name) => card.getByRole("button", { name }).first().getAttribute("aria-pressed");

  // owner: Preview tab strip
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  watch(page, "owner");
  const login = await page.request.post(`${base}/api/login`, { data: { key } });
  res.login = login.status();
  await page.goto(`${base}/studio/p/${e2e.pid}`);
  const card = page.locator('[data-testid="sound-card"]');
  await card.waitFor({ timeout: 30000 });
  await card.getByRole("button", { name: /Compose again/ }).waitFor({ timeout: 15000 });
  res.owner_credit = await card.getByText("Composed by Lyria on Vertex AI").count();
  await page.screenshot({ path: `${out}/1-owner-preview.png` });
  await card.getByRole("button", { name: /Chime/ }).click();
  await page.waitForTimeout(700);
  res.owner_chime_playing = await pressed(card, /Chime/);
  await card.getByRole("button", { name: /^Prompt$/ }).click();
  res.owner_prompt_shown = await card.locator(".sound-prompt").count();
  await page.screenshot({ path: `${out}/2-owner-preview-prompt.png` });
  await page.waitForTimeout(2500);
  res.owner_chime_ended = await pressed(card, /Chime/);

  // share link: no cookie, read-only
  const sctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const sp = await sctx.newPage();
  watch(sp, "share");
  await sp.goto(`${base}/studio/s/${e2e.share}`);
  const scard = sp.locator('[data-testid="sound-card"]');
  await scard.waitFor({ timeout: 30000 });
  res.share_compose_buttons = await scard.getByRole("button", { name: /Compose/ }).count();
  await scard.getByRole("button", { name: /Theme/ }).click();
  await sp.waitForTimeout(2000);
  res.share_theme_playing = await pressed(scard, /Theme/);
  res.share_theme_label = (await scard.getByRole("button", { name: /Theme/ }).innerText()).replace(/\s+/g, " ");
  await sp.screenshot({ path: `${out}/3-share-desktop.png` });

  // share link on a phone
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const mp = await mctx.newPage();
  watch(mp, "phone");
  await mp.goto(`${base}/studio/s/${e2e.share}`);
  const mcard = mp.locator('[data-testid="sound-card"]');
  await mcard.waitFor({ timeout: 30000 });
  await mcard.scrollIntoViewIfNeeded();
  const box = await mcard.boundingBox();
  res.phone_card_box = box && { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.width), h: Math.round(box.height) };
  await mp.screenshot({ path: `${out}/4-share-phone.png` });
  await mcard.screenshot({ path: `${out}/5-share-phone-card.png` });

  console.log(JSON.stringify({ res, errors }, null, 2));
  await browser.close();
})().catch((e) => {
  console.error("UI CHECK FAILED", e);
  process.exit(1);
});
