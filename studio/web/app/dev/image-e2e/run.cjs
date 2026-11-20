// Start from an image, end to end in a real browser (run on the VM, never on the laptop): Home -> "Start from an
// image" -> thumbnail -> paste and drop -> Start (create, upload with progress, first message with attachments) ->
// the image in the chat bubble -> the live "image_brief" card -> lightbox (mouse and keyboard) -> share dialog
// setting -> share Overview with the image hidden, then shown. Desktop 1440x900 and phone 390x844 screenshots.
//   python make_sketch.py <img_dir>                       # synthetic images (no real user image is ever used here)
//   server: gstudio-web with the uploads routes, AGENT=scripted BUILDER=fake ACCESS_KEY=dev, serving web/app/dist;
//     uploads need python-multipart, which the VM's shared /opt/gs/venv lacks: pip install --target <dir>
//     Pillow==12.3.0 python-multipart==0.0.34 pillow-heif==1.8.1, then PYTHONPATH=<dir> for uvicorn
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/image-e2e/run.cjs http://127.0.0.1:9251 <out> <img_dir> [brief.json]
// The optional brief JSON (default: mood-cloud-brief.json next to this file) replaces the scripted agent's
// placeholder brief in GET /api/projects/<pid> and the share JSON, so the card shows a realistic reading.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:9251";
const OUT = path.resolve(process.argv[3] || "_out/image-e2e/shots");
const IMG = path.resolve(process.argv[4] || "_out/image-e2e");
const BRIEF = JSON.parse(fs.readFileSync(process.argv[5] || path.join(__dirname, "mood-cloud-brief.json"), "utf8"));
const SHEET = path.join(IMG, process.env.SHEET || "mood-cloud-sheet.png");
const SKETCH = path.join(IMG, "desk-sketch.jpg");
const KEY = process.env.KEY || "dev";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 300) });
  console.log(ok ? "PASS" : "FAIL", name, detail ? `| ${String(detail).slice(0, 160)}` : "");
};
const shot = async (page, name, full = false) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full, timeout: 90000 });
const problems = [];
function watch(page, label) {
  page.on("console", (m) => m.type() === "error" && problems.push(`${label} console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`${label} pageerror: ${e}`));
  page.on("response", (r) => {
    // 404 for a share image while share_uploads is off is the point of the test
    if (r.status() >= 400 && !/\/api\/share\/[^/]+\/uploads\//.test(r.url())) problems.push(`${label} http ${r.status()} ${r.url()}`);
  });
}
const b64 = (f) => fs.readFileSync(f).toString("base64");

/** Paste a file into the focused textarea the way a browser does (ClipboardEvent with a DataTransfer). */
async function paste(page, sel, file, name, type) {
  await page.evaluate(
    ({ sel, data, name, type }) => {
      const bin = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bin], name, { type }));
      const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
      document.querySelector(sel).dispatchEvent(ev);
      return ev.defaultPrevented;
    },
    { sel, data: b64(file), name, type },
  );
}

/** Drop a file on an element (or anywhere on the page) the way a browser does. */
async function drop(page, sel, file, name, type) {
  return page.evaluate(
    ({ sel, data, name, type }) => {
      const bin = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bin], name, { type }));
      const el = document.querySelector(sel);
      for (const t of ["dragenter", "dragover"]) el.dispatchEvent(new DragEvent(t, { dataTransfer: dt, bubbles: true, cancelable: true }));
      const ev = new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true });
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    },
    { sel, data: b64(file), name, type },
  );
}

/** A card on its own at this width: the viewport grows tall enough for the whole card, then goes back. */
async function cardShot(page, sel, name) {
  const vp = page.viewportSize();
  const h = await page.$eval(sel, (e) => Math.ceil(e.getBoundingClientRect().height));
  await page.setViewportSize({ width: vp.width, height: Math.max(vp.height, h + 1100) });
  await page.waitForTimeout(400);
  // centred, so a sticky bar above it (the share Overview's section nav) cannot cover its top
  await page.$eval(sel, (e) => e.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(200);
  await page.locator(sel).first().screenshot({ path: path.join(OUT, `${name}.png`) });
  await page.setViewportSize(vp);
}

async function noOverflow(page, label) {
  const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, w: window.innerWidth }));
  check(`${label}: no horizontal overflow at ${r.w}px`, r.sw <= r.w + 1, JSON.stringify(r));
}

/** Serve the realistic brief (with this project's real upload ids) in place of the scripted placeholder. */
async function realisticBrief(page, pid, token, ids, version) {
  const brief = { ...BRIEF, upload_ids: ids };
  await page.route(new RegExp(`/api/projects/${pid}$`), async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const resp = await route.fetch();
    const json = await resp.json();
    route.fulfill({ response: resp, json: { ...json, image_brief: brief } });
  });
  if (token)
    await page.route(new RegExp(`/api/share/${token}$`), async (route) => {
      const resp = await route.fetch();
      const json = await resp.json();
      // the share Overview needs a version to show its panel; a scripted project has none yet
      const versions = json.versions?.length ? json.versions : [version];
      route.fulfill({ response: resp, json: { ...json, image_brief: brief, versions, current_version: json.current_version ?? 1 } });
    });
}

async function login(ctx) {
  const r = await ctx.request.post(`${BASE}/api/login`, { data: { key: KEY } });
  if (!r.ok()) throw new Error(`login ${r.status()}`);
}

async function homeWithImage(page, label, { wide }) {
  await page.goto(`${BASE}/studio/`);
  await page.waitForSelector(".from-image");
  const line = await page.textContent(".from-image");
  check(`${label}: home offers "Start from an image"`, /Start from an image/.test(line) && /ChatGPT concept/.test(line), line);
  if (wide) await shot(page, `01-home-${label}-empty`);
  await page.setInputFiles('input[accept="image/*,.heic,.heif"]', SHEET);
  await page.waitForSelector(".img-draft img");
  const st = await page.getAttribute(".img-draft", "class");
  check(`${label}: picked image shows as a thumbnail, waiting for Start (nothing uploaded yet)`, /waiting/.test(st), st);
  const ph = await page.getAttribute(".home-composer textarea", "placeholder");
  check(`${label}: the box says words are optional with an image`, /optional/.test(ph), ph);
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const version = { n: 1, created: "2026-11-20T10:50:00Z", label: "v1", source: "build", state: "error", summary: null, error: { message: "Example version (screenshot run)" }, percent: null, started: null, ended: null, finalized: true };

  // ------------------------------------------------------------------ desktop 1440 x 900
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await login(ctx);
  const page = await ctx.newPage();
  watch(page, "desk");
  const uploads = [];
  const chats = [];
  page.on("request", (r) => {
    if (/\/api\/projects\/[^/]+\/chat$/.test(r.url()) && r.method() === "POST") chats.push(r.postDataJSON());
  });
  page.on("response", async (r) => {
    if (/\/api\/projects\/[^/]+\/uploads$/.test(r.url()) && r.request().method() === "POST") uploads.push({ status: r.status(), body: await r.json().catch(() => null) });
  });

  await homeWithImage(page, "1440", { wide: true });
  await page.fill(".home-composer textarea", "Make this for my desk. It should feel calm, and I would love the rain chime.");
  // paste a second image (a phone sketch) into the box, then remove it again
  await page.focus(".home-composer textarea");
  await paste(page, ".home-composer textarea", SKETCH, "image.jpg", "image/jpeg");
  await page.waitForFunction(() => document.querySelectorAll(".img-draft").length === 2);
  const names = await page.$$eval(".img-draft", (els) => els.map((e) => e.getAttribute("title")));
  check("paste: a pasted image is attached with a readable name", /^pasted-image-\d{6}\.jpg$/.test(names[1] || ""), names.join(", "));
  const typed = await page.inputValue(".home-composer textarea");
  check("paste: the typed words stay", typed.startsWith("Make this for my desk"), typed.slice(0, 40));
  await shot(page, "02-home-1440-two-images");
  await page.click(".img-draft:nth-child(2) .img-draft-x");
  await page.waitForFunction(() => document.querySelectorAll(".img-draft").length === 1);
  // a drop anywhere on the page lands in the composer (not the browser opening the file)
  const dropped = await drop(page, ".examples", SKETCH, "desk-sketch.jpg", "image/jpeg");
  await page.waitForFunction(() => document.querySelectorAll(".img-draft").length === 2);
  check("drop: an image dropped outside the box is attached, the browser does not open it", dropped);
  await page.click(".img-draft:nth-child(2) .img-draft-x");
  await page.waitForFunction(() => document.querySelectorAll(".img-draft").length === 1);
  await shot(page, "03-home-1440-image-attached");

  // Start on a slow uplink, to see the progress bar
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 40, downloadThroughput: 4 * 1024 * 1024, uploadThroughput: 40 * 1024 });
  await page.click('.home-composer .send-btn[aria-label="Start"]');
  const bar = await page.waitForSelector(".img-draft-bar", { timeout: 20000 }).catch(() => null);
  check("Start: the image uploads with a progress bar", !!bar);
  if (bar) await shot(page, "04-home-1440-uploading");
  const note = await page.textContent(".home-img-note").catch(() => "");
  check("Start: a status line says what is happening", /Uploading your image|Creating your project|Opening/.test(note || ""), note);
  await page.waitForURL(/\/studio\/p\/p_/, { timeout: 60000 });
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  const pid = new URL(page.url()).pathname.split("/").pop();
  check("Start: upload answered 201 with the contract's fields", uploads[0]?.status === 201 && /^u_[a-z0-9]{10}$/.test(uploads[0]?.body?.id || "") && uploads[0]?.body?.mime === "image/jpeg", JSON.stringify(uploads[0]));
  await page.waitForSelector(".msg-imgs img", { timeout: 30000 });
  await page.waitForSelector(".ib-card", { timeout: 60000 });
  const upId = uploads[0]?.body?.id;
  check("first message carries the image as an attachment", chats[0]?.attachments?.[0]?.id === upId && typeof chats[0]?.text === "string", JSON.stringify(chats[0]));
  const bubbleSrc = await page.getAttribute(".msg-imgs img", "src");
  check("the chat bubble shows the image by its owner thumbnail URL", bubbleSrc === `/api/projects/${pid}/uploads/${upId}?thumb=1`, bubbleSrc);
  const loaded = await page.$eval(".msg-imgs img", (i) => i.complete && i.naturalWidth > 0);
  check("the thumbnail loads from the server", loaded);
  const card = await page.textContent(".ib-card");
  check("the live image_brief event shows the card in the chat", /What I read from your image/.test(card), card.slice(0, 120));
  await page.waitForTimeout(800);
  await shot(page, "05-project-1440-scripted-brief");

  // lightbox, by mouse then by keyboard
  await page.click(".msg-imgs .msg-img");
  await page.waitForSelector(".img-lightbox img");
  const full = await page.getAttribute(".img-lightbox img", "src");
  check("lightbox: the full image (owner URL)", full === `/api/projects/${pid}/uploads/${upId}`, full);
  await page.waitForFunction(() => document.querySelector(".img-lightbox img")?.naturalWidth > 0);
  await page.waitForTimeout(500); // let the pop-in finish
  await shot(page, "06-lightbox-1440");
  await page.keyboard.press("Escape");
  await page.waitForSelector(".img-lightbox", { state: "detached" });
  const back = await page.evaluate(() => document.activeElement?.classList.contains("msg-img"));
  check("lightbox: Escape closes it and focus returns to the image", back);
  await page.focus(".msg-imgs .msg-img");
  await page.keyboard.press("Enter");
  const kb = await page.waitForSelector(".img-lightbox", { timeout: 5000 }).catch(() => null);
  check("lightbox: opens from the keyboard (Enter)", !!kb);
  await page.keyboard.press("Escape");

  // the realistic brief (as the design agent would write it) for the card screenshots
  await realisticBrief(page, pid, null, [upId], version);
  await page.reload();
  await page.waitForSelector(".ib-card .ib-feature");
  await page.$eval(".ib-card", (e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "07-project-1440-brief-card");
  await cardShot(page, ".chat-col .ib-card", "07b-card-in-chat-1440");
  const chips = await page.$$eval(".ib-chip", (els) => els.map((e) => e.textContent.trim()));
  const LABEL = { buildable: "Buildable", closest: "Closest", not_yet: "Not yet" };
  const want = [...new Set((BRIEF.features || []).map((f) => LABEL[f.status]).filter(Boolean))];
  check(`card: a status chip per feature (${want.join(", ")})`, want.every((c) => chips.includes(c)) && chips.length === (BRIEF.features || []).length, chips.join(","));
  const seenOpen = await page.$eval(".ib-seen", (d) => d.open);
  check("card: the text seen in the image starts collapsed", seenOpen === false);
  await page.click(".ib-seen summary");
  await shot(page, "08-project-1440-brief-text-seen", false);

  // through the scripted questions, concepts and brief to a (fake) build, so the project can be shared
  await page.waitForSelector(".dock .q", { timeout: 60000 });
  for (const q of await page.$$(".dock .q")) {
    const opt = (await q.$(".q-opt:nth-child(2)")) || (await q.$(".q-opt"));
    if (opt) await opt.click();
  }
  const sendAnswers = await page.$(".dock .dock-foot .btn.primary");
  if (sendAnswers) await sendAnswers.click();
  await page.waitForSelector(".concepts-dock .tile", { timeout: 180000 });
  await page.waitForTimeout(1200);
  await page.click(".concepts-dock .tile >> nth=0");
  await page.waitForSelector(".brief-dock", { timeout: 120000 });
  await page.$eval(".brief-scroll", (el) => (el.scrollTop = el.scrollHeight)).catch(() => {});
  await page.waitForTimeout(400);
  await page.click(".brief-dock .btn.primary");
  await page.waitForSelector(".msg.result", { timeout: 240000 });
  check("the image project goes on to a build like any other", true);

  // share dialog: the setting, off by default
  await page.click('.ws-header .btn:has-text("Share")');
  await page.waitForSelector(".share-images input[type=checkbox]");
  const off = await page.isChecked(".share-images input[type=checkbox]");
  check('share dialog: "Show my starting image on the share link" is there and off', !off);
  await page.click('[role=dialog] .btn.primary:has-text("Create link")');
  await page.waitForSelector('input[aria-label="Share link"]');
  const link = await page.inputValue('input[aria-label="Share link"]');
  const token = link.split("/").pop();
  await shot(page, "09-share-dialog-1440");

  // the share link, image hidden
  const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const sp = await anon.newPage();
  watch(sp, "share");
  await realisticBrief(sp, pid, token, [upId], version);
  const shareJson = await (await anon.request.get(`${BASE}/api/share/${token}`)).json();
  const m0 = (shareJson.chat || []).find((m) => m.role === "user");
  check("share JSON (server): images hidden by default, count only", !m0?.attachments && m0?.attachments_hidden === 1 && !shareJson.uploads, JSON.stringify({ a: m0?.attachments, h: m0?.attachments_hidden }));
  await sp.goto(`${BASE}/studio/s/${token}`);
  await sp.waitForSelector("#ov-image .ib-card", { timeout: 30000 });
  const ovHtml = await sp.$eval("#ov-image", (e) => e.outerHTML);
  check("share Overview: the brief card is there, worded for a visitor", /What the assistant read from the starting image/.test(ovHtml));
  check("share Overview: no image and no owner URL while share_uploads is off", !/<img/.test(ovHtml) && !/\/api\/projects\//.test(ovHtml) && /not shared on this link/.test(ovHtml));
  const anyOwner = await sp.evaluate(() => document.documentElement.outerHTML.includes("/api/projects/"));
  check("share page: no /api/projects/ URL anywhere", !anyOwner);
  await sp.$eval("#ov-image", (e) => e.scrollIntoView({ block: "start" }));
  await shot(sp, "10-share-overview-1440-image-hidden");
  await cardShot(sp, "#ov-image .ib-card", "10b-card-share-1440-image-hidden");

  // the owner turns the image on, the share link shows it
  await page.click(".share-images input[type=checkbox]");
  await page.waitForFunction(() => document.querySelector(".share-images input")?.checked === true && !document.querySelector(".share-images .spinner"));
  const own = await (await ctx.request.get(`${BASE}/api/projects/${pid}`)).json();
  check("share dialog: the setting PATCHes share_uploads", own.share_uploads === true, own.share_uploads);
  await shot(page, "11-share-dialog-1440-on");
  await sp.reload();
  await sp.waitForSelector("#ov-image .ib-card img", { timeout: 30000 });
  const shareSrc = await sp.getAttribute("#ov-image .ib-card img", "src");
  check("share Overview: the image by its share URL once allowed", shareSrc === `/api/share/${token}/uploads/${upId}?thumb=1`, shareSrc);
  await sp.waitForFunction(() => document.querySelector("#ov-image .ib-card img")?.naturalWidth > 0);
  await sp.$eval("#ov-image", (e) => e.scrollIntoView({ block: "start" }));
  await shot(sp, "12-share-overview-1440-image-shown");
  await cardShot(sp, "#ov-image .ib-card", "12b-card-share-1440-image-shown");

  // ------------------------------------------------------------------ phone 390 x 844
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await login(phone);
  const pp = await phone.newPage();
  watch(pp, "phone");
  await homeWithImage(pp, "390", { wide: false });
  await pp.fill(".home-composer textarea", "A calm weather lamp like this, please");
  await noOverflow(pp, "phone home with an image");
  await shot(pp, "13-home-390-image-attached");
  await pp.click('.home-composer .send-btn[aria-label="Start"]');
  await pp.waitForURL(/\/studio\/p\/p_/, { timeout: 60000 });
  await pp.waitForSelector(".ib-card", { timeout: 60000 });
  const ppid = new URL(pp.url()).pathname.split("/").pop();
  const pup = (await (await phone.request.get(`${BASE}/api/projects/${ppid}`)).json()).uploads?.[0]?.id;
  await realisticBrief(pp, ppid, null, [pup], version);
  await pp.reload();
  await pp.waitForSelector(".ib-card .ib-feature");
  await noOverflow(pp, "phone project with the brief card");
  await pp.$eval(".msg-imgs", (e) => e.scrollIntoView({ block: "start" }));
  await shot(pp, "14-project-390-image-and-card");
  await pp.$eval(".ib-card .ib-features", (e) => e.scrollIntoView({ block: "start" }));
  await shot(pp, "15-project-390-card-features");
  await cardShot(pp, ".chat-col .ib-card", "15b-card-in-chat-390");
  const sp2 = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage();
  watch(sp2, "phone-share");
  await realisticBrief(sp2, pid, token, [upId], version);
  await sp2.goto(`${BASE}/studio/s/${token}`);
  await sp2.waitForSelector("#ov-image .ib-card", { timeout: 30000 });
  await noOverflow(sp2, "phone share Overview");
  await sp2.$eval("#ov-image", (e) => e.scrollIntoView({ block: "start" }));
  await shot(sp2, "16-share-overview-390");
  await cardShot(sp2, "#ov-image .ib-card", "16b-card-share-390");

  check("no console errors, page errors or failed API calls", problems.length === 0, problems.slice(0, 6).join(" || "));
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ results, problems }, null, 2));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed; screenshots in ${OUT}`);
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error("RUN FAILED", e);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ results, problems, error: String(e && e.stack) }, null, 2));
  process.exit(2);
});
