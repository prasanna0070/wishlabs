// "Start from an image" end to end with the REAL design agent (Gemini) and real concept photos (Nano Banana), in a
// real browser (run on the VM, never on the laptop). Home -> attach the image + words -> Start -> the image bubble
// and the "What I read from your image" card -> the agent's answer (questions) -> answer with the suggested options
// -> concepts -> pick one -> build (BUILDER=fake) -> Share dialog: "Show my starting image" on, Create link -> the
// share link logged out (Overview). Screenshots at 1440x900 and 390x844; the phone pages open the same project.
//   server: dev/image-e2e/live.sh <port> <store_dir> (AGENT=gemini BUILDER=fake PHOTOS=1, throwaway key in $KEY_FILE)
//   KEY=... PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/image-e2e/live.cjs <base> <out> <image> "<words>"
// Optional env: SHARE=0 (stop after the build), PHONE=0 (desktop only), BUILD=0 (stop at the concepts).
// The image is only sent to that local server and, through it, to Vertex AI. <out> holds screenshots of it: keep
// <out> out of git when the image is not yours.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:9252";
const OUT = path.resolve(process.argv[3] || "_out/image-live/shots");
const IMAGE = path.resolve(process.argv[4] || "");
const WORDS = process.argv[5] ?? "my friend made this in ChatGPT, can you build it?";
const KEY = process.env.KEY || "dev";
const PHONE = process.env.PHONE !== "0";
const SHARE = process.env.SHARE !== "0";
const BUILD = process.env.BUILD !== "0";
fs.mkdirSync(OUT, { recursive: true });

const t0 = Date.now();
const secs = () => ((Date.now() - t0) / 1000).toFixed(1);
const log = (...a) => console.log(`${secs().padStart(6)}s`, ...a);
const results = [];
const timings = {};
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  log(ok ? "PASS" : "FAIL", name, detail ? `| ${String(detail).slice(0, 200)}` : "");
};
const shot = async (page, name, full = false) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full, timeout: 90000 });
  log("shot", name);
};
const problems = [];
function watch(page, label) {
  page.on("console", (m) => m.type() === "error" && problems.push(`${label} console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`${label} pageerror: ${e}`));
  page.on("response", (r) => {
    if (r.status() >= 400) problems.push(`${label} http ${r.status()} ${r.url()}`);
  });
}

/** A card on its own at this width: the viewport grows tall enough for the whole card, then goes back. */
async function cardShot(page, sel, name) {
  const vp = page.viewportSize();
  const h = await page.$eval(sel, (e) => Math.ceil(e.getBoundingClientRect().height));
  await page.setViewportSize({ width: vp.width, height: Math.max(vp.height, h + 1100) });
  await page.waitForTimeout(400);
  await page.$eval(sel, (e) => e.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(300);
  await page.locator(sel).first().screenshot({ path: path.join(OUT, `${name}.png`) });
  await page.setViewportSize(vp);
  log("shot", name);
}

async function noOverflow(page, label) {
  const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, w: window.innerWidth }));
  check(`${label}: no horizontal overflow at ${r.w}px`, r.sw <= r.w + 1, JSON.stringify(r));
}

async function login(ctx) {
  const r = await ctx.request.post(`${BASE}/api/login`, { data: { key: KEY } });
  if (!r.ok()) throw new Error(`login ${r.status()}`);
}

async function homeWithImage(page, label) {
  await page.goto(`${BASE}/studio/`);
  await page.waitForSelector(".from-image");
  await page.setInputFiles('input[accept="image/*,.heic,.heif"]', IMAGE);
  await page.waitForSelector(".img-draft img");
  if (WORDS) await page.fill(".home-composer textarea", WORDS);
  await page.waitForFunction(() => document.querySelector(".img-draft img")?.naturalWidth > 0);
  check(`${label}: the picked image shows as a thumbnail before Start`, true);
}

/** Pick, for every question, the option the agent's own "Unless you say otherwise" defaults name, else option 1. */
async function answerQuestions(page) {
  const dock = '.dock[aria-label="Questions"]';
  const qa = await page.$$eval(`${dock} .q`, (qs) =>
    qs.map((q) => ({ q: q.querySelector(".q-text")?.textContent || "", opts: [...q.querySelectorAll(".q-opt .grow")].map((o) => o.textContent || "") })),
  );
  const defaults = await page.$$eval(`${dock} .q-defaults .pill`, (ps) => ps.map((p) => (p.textContent || "").toLowerCase()));
  const picked = [];
  for (let i = 0; i < qa.length; i++) {
    const { opts } = qa[i];
    if (!opts.length) {
      await page.fill(`${dock} .q >> nth=${i} >> .q-free`, "Whatever you think works best.");
      picked.push({ q: qa[i].q, a: "Whatever you think works best." });
      continue;
    }
    let k = opts.findIndex((o) => defaults.some((d) => d.includes(o.toLowerCase()) || o.toLowerCase().includes(d)));
    if (k < 0) k = 0;
    picked.push({ q: qa[i].q, a: opts[k], options: opts });
    await page.click(`${dock} .q >> nth=${i} >> .q-opt >> nth=${k}`);
  }
  const send = await page.$(`${dock} .dock-foot .btn.primary`);
  if (send && !(await send.isDisabled())) await send.click();
  return picked;
}

const api = async (ctx, url) => (await ctx.request.get(`${BASE}${url}`)).json();

(async () => {
  if (!IMAGE || !fs.existsSync(IMAGE)) throw new Error(`no image at ${IMAGE}`);
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await login(ctx);
  const page = await ctx.newPage();
  watch(page, "desk");
  const chats = [];
  page.on("request", (r) => {
    if (/\/api\/projects\/[^/]+\/chat$/.test(r.url()) && r.method() === "POST") chats.push(r.postDataJSON());
  });
  let phone = null;
  let pp = null;
  if (PHONE) {
    phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    await login(phone);
    pp = await phone.newPage();
    watch(pp, "phone");
  }

  // ---------------------------------------------------------------- home with the image attached
  await homeWithImage(page, "1440");
  await shot(page, "01-home-1440-image-attached");
  if (pp) {
    await homeWithImage(pp, "390");
    await noOverflow(pp, "phone home with an image");
    await shot(pp, "01-home-390-image-attached");
  }

  // ---------------------------------------------------------------- Start: upload, first message, the agent reads
  await page.click('.home-composer .send-btn[aria-label="Start"]');
  await page.waitForURL(/\/studio\/p\/p_/, { timeout: 120000 });
  const pid = new URL(page.url()).pathname.split("/").pop();
  log("project", pid);
  await page.waitForSelector(".msg-imgs img", { timeout: 60000 });
  timings.bubble = secs();
  check("the first message carries the image (attachments) and the words", chats[0]?.attachments?.length === 1 && (chats[0]?.text || "") === WORDS, JSON.stringify(chats[0]));
  await page.waitForSelector(".ib-card", { timeout: 240000 });
  timings.brief_card = secs();
  log("brief card");
  const ans = await page
    .waitForSelector('.dock[aria-label="Questions"], .concepts-dock .tile', { timeout: 600000 })
    .then(async (el) => ((await el.evaluate((e) => e.classList.contains("tile"))) ? "concepts" : "questions"));
  timings.first_answer = secs();
  log("agent answered with", ans);
  let proj = await api(ctx, `/api/projects/${pid}`);
  const brief = proj.image_brief;
  check("the agent recorded an image brief", !!brief && Array.isArray(brief.features), brief ? `${brief.kind}: ${brief.title}` : "none");
  fs.writeFileSync(path.join(OUT, "brief.json"), JSON.stringify(brief, null, 2));
  const bubbleOk = await page.$eval(".msg-imgs img", (i) => i.complete && i.naturalWidth > 0);
  check("the chat bubble shows the uploaded image", bubbleOk);
  await page.$eval(".msg-imgs", (e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "02-chat-1440-image-and-brief");
  await cardShot(page, ".chat-col .ib-card", "02b-card-in-chat-1440");
  await page.$eval(".chat-col .msg:last-of-type", (e) => e.scrollIntoView({ block: "end" })).catch(() => {});
  await shot(page, "03-agent-answer-1440");
  if (pp) {
    await pp.goto(`${BASE}/studio/p/${pid}`);
    await pp.waitForSelector(".ib-card", { timeout: 60000 });
    await pp.waitForFunction(() => document.querySelector(".msg-imgs img")?.naturalWidth > 0, null, { timeout: 30000 });
    await noOverflow(pp, "phone project with the image and the card");
    await pp.$eval(".msg-imgs", (e) => e.scrollIntoView({ block: "start" }));
    await shot(pp, "02-chat-390-image");
    await pp.$eval(".ib-card", (e) => e.scrollIntoView({ block: "start" }));
    await shot(pp, "02b-chat-390-brief-card");
    await cardShot(pp, ".chat-col .ib-card", "02c-card-in-chat-390");
    await pp.$eval(".chat-col", (e) => e.scrollIntoView({ block: "end" })).catch(() => {});
    await shot(pp, "03-agent-answer-390");
  }

  // ---------------------------------------------------------------- answer the questions until concepts come
  const answered = [];
  for (let round = 0; round < 4; round++) {
    const what = await page
      .waitForSelector('.dock[aria-label="Questions"], .concepts-dock .tile', { timeout: 900000 })
      .then(async (el) => ((await el.evaluate((e) => e.classList.contains("tile"))) ? "concepts" : "questions"));
    if (what === "concepts") break;
    const picked = await answerQuestions(page);
    answered.push(...picked);
    log("answered", JSON.stringify(picked));
    // the answered dock goes away at once; only then look for the next dock (more questions or the concepts)
    await page.waitForSelector('.dock[aria-label="Questions"]', { state: "detached", timeout: 60000 });
  }
  await page.waitForSelector(".concepts-dock .tile", { timeout: 900000 });
  timings.concepts = secs();
  await page.waitForTimeout(2500);
  await page.$$eval(".concepts-dock img", (imgs) => Promise.all(imgs.map((i) => (i.complete ? 0 : new Promise((r) => (i.onload = i.onerror = r))))));
  proj = await api(ctx, `/api/projects/${pid}`);
  const concepts = (proj.concepts || []).map((c) => ({ id: c.id, title: c.title, tier: c.tier, blurb: c.blurb, photo: !!c.photo_url }));
  check("concepts came after the questions", concepts.length > 0, concepts.map((c) => c.title).join(" | "));
  await shot(page, "04-concepts-1440");
  await page.$eval(".concepts-dock", (e) => e.scrollIntoView({ block: "start" })).catch(() => {});
  if (pp) {
    await pp.goto(`${BASE}/studio/p/${pid}`);
    await pp.waitForSelector(".concepts-dock .tile", { timeout: 60000 });
    await pp.waitForTimeout(2500);
    await noOverflow(pp, "phone concepts");
    await shot(pp, "04-concepts-390");
  }

  // ---------------------------------------------------------------- pick one, build (fake builder)
  let token = null;
  if (BUILD) {
    await page.click(".concepts-dock .tile:not([disabled]) >> nth=0");
    await page.waitForSelector(".brief-dock", { timeout: 600000 });
    await page.$eval(".brief-scroll", (el) => (el.scrollTop = el.scrollHeight)).catch(() => {});
    await page.waitForTimeout(600);
    await page.click(".brief-dock .btn.primary");
    await page.waitForSelector(".msg.result", { timeout: 600000 });
    timings.built = secs();
    check("the image project goes on to a build like any other", true);
    await page.waitForTimeout(1500);
    await shot(page, "05-built-1440");
  }

  // ---------------------------------------------------------------- share: images on, link, logged out
  if (SHARE && BUILD) {
    await page.click('.ws-header .btn:has-text("Share")');
    await page.waitForSelector(".share-images input[type=checkbox]");
    check("share dialog: the image setting is off by default", !(await page.isChecked(".share-images input[type=checkbox]")));
    await page.click(".share-images input[type=checkbox]");
    await page.waitForFunction(() => document.querySelector(".share-images input")?.checked === true && !document.querySelector(".share-images .spinner"));
    const own = await api(ctx, `/api/projects/${pid}`);
    check("share dialog: turning it on PATCHes share_uploads", own.share_uploads === true, own.share_uploads);
    await page.click('[role=dialog] .btn.primary:has-text("Create link")');
    await page.waitForSelector('input[aria-label="Share link"]');
    const link = await page.inputValue('input[aria-label="Share link"]');
    token = link.split("/").pop();
    await shot(page, "06-share-dialog-1440");
    const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const sj = await (await anon.request.get(`${BASE}/api/share/${token}`)).json();
    check("share JSON: the image brief and the shared images, with share URLs only",
      !!sj.image_brief && sj.share_uploads === true && !sj.uploads && (sj.chat || []).some((m) => (m.attachments || []).length > 0) && !JSON.stringify(sj).includes("/api/projects/"),
      JSON.stringify({ brief: !!sj.image_brief, share_uploads: sj.share_uploads, uploads: !!sj.uploads, sent: (sj.chat || []).filter((m) => (m.attachments || []).length).length }));
    const sp = await anon.newPage();
    watch(sp, "share");
    await sp.goto(`${BASE}/studio/s/${token}`);
    await sp.waitForSelector("#ov-image .ib-card", { timeout: 60000 });
    await sp.waitForFunction(() => document.querySelector("#ov-image .ib-card img")?.naturalWidth > 0, null, { timeout: 30000 });
    const src = await sp.getAttribute("#ov-image .ib-card img", "src");
    check("share Overview: the image comes from the share URL", (src || "").startsWith(`/api/share/${token}/uploads/`), src);
    check("share page: no owner URL anywhere", !(await sp.evaluate(() => document.documentElement.outerHTML.includes("/api/projects/"))));
    await shot(sp, "07-share-overview-1440-top");
    await sp.$eval("#ov-image", (e) => e.scrollIntoView({ block: "start" }));
    await shot(sp, "07b-share-overview-1440-image-card");
    await cardShot(sp, "#ov-image .ib-card", "07c-share-card-1440");
    if (PHONE) {
      const ap = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage();
      watch(ap, "phone-share");
      await ap.goto(`${BASE}/studio/s/${token}`);
      await ap.waitForSelector("#ov-image .ib-card", { timeout: 60000 });
      await ap.waitForFunction(() => document.querySelector("#ov-image .ib-card img")?.naturalWidth > 0, null, { timeout: 30000 });
      await noOverflow(ap, "phone share Overview");
      await shot(ap, "07-share-overview-390-top");
      await ap.$eval("#ov-image", (e) => e.scrollIntoView({ block: "start" }));
      await shot(ap, "07b-share-overview-390-image-card");
      await cardShot(ap, "#ov-image .ib-card", "07c-share-card-390");
    }
  }

  proj = await api(ctx, `/api/projects/${pid}`);
  const transcript = proj.chat.map((m) => ({
    role: m.role,
    kind: m.kind,
    text: m.text,
    attachments: m.attachments,
    questions: m.payload?.questions?.map((q) => ({ q: q.question, options: q.options })),
    defaults: m.payload?.defaults,
  }));
  fs.writeFileSync(
    path.join(OUT, "run.json"),
    JSON.stringify({ pid, token, timings, answered, concepts, brief: proj.image_brief, usage: proj.usage?.totals, transcript, results, problems }, null, 2),
  );
  check("no console errors, page errors or failed API calls", problems.length === 0, problems.slice(0, 6).join(" || "));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed; timings ${JSON.stringify(timings)}; ${OUT}`);
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error("RUN FAILED", e);
  fs.writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ results, problems, timings, error: String(e && e.stack) }, null, 2));
  process.exit(2);
});
