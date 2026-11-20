// Browser end-to-end check of "Talk to make a wish" (run on the VM, never on the laptop). Chromium's fake microphone
// plays a synthesized wish (MIC_WAV) into the studio's voice dialog, the server bridges it to real Gemini Live, and this
// checks: captions in the dialog, the hand-over to a new project whose first chat turn streams, the Spoken wish card in
// the project and on its read-only share link, keyboard use (Escape cancels), and the text fallback when the
// microphone is blocked. Screenshots and report.json go to OUT. Run by web/app/dev/voice-ui.sh.
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers KEY_FILE=<0600 file> MIC_WAV=<wav> node web/app/dev/voice-ui.cjs <port> <out>
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.argv[2] || 9201);
const OUT = path.resolve(process.argv[3] || "_out/voice-ui");
const BASE = `http://127.0.0.1:${PORT}`;
const KEY = fs.readFileSync(process.env.KEY_FILE, "utf8").trim();
const MIC = process.env.MIC_WAV;
fs.mkdirSync(OUT, { recursive: true });
const report = { steps: [], errors: [] };
const step = (name, data = {}) => {
  const row = { t: new Date().toISOString().slice(11, 19), name, ...data };
  report.steps.push(row);
  console.log(JSON.stringify(row));
};

async function signIn(page) {
  await page.goto(`${BASE}/studio/login`);
  await page.locator('input[type="password"]').fill(KEY);
  await page.locator('input[type="password"]').press("Enter");
  await page.waitForURL((u) => /\/studio\/?$/.test(new URL(u).pathname), { timeout: 20000 });
}

function watch(page, tag) {
  page.on("console", (m) => {
    if (m.type() === "error") report.errors.push(`${tag} console: ${m.text().slice(0, 300)}`);
  });
  page.on("pageerror", (e) => report.errors.push(`${tag} pageerror: ${String(e).slice(0, 300)}`));
}

(async () => {
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${MIC}%noloop`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  let ok = false;
  try {
    // ---------------------------------------------------------------- 1. a real call from Home
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    await ctx.grantPermissions(["microphone"], { origin: BASE });
    const page = await ctx.newPage();
    watch(page, "home");
    await signIn(page);
    step("signed in");
    const talk = page.getByRole("button", { name: "Talk", exact: true });
    await talk.click();
    const dialog = page.getByRole("dialog", { name: "Talk to make a wish" });
    await dialog.waitFor({ timeout: 10000 });
    await page.screenshot({ path: path.join(OUT, "01-connecting.png") });
    await page.locator(".vw-status", { hasText: /Listening|talking/ }).waitFor({ timeout: 40000 });
    step("live ready", { status: await page.locator(".vw-status").innerText() });
    await page.getByRole("checkbox", { name: /Save my voice recording/ }).check(); // opt in: the share link plays it
    await page.locator(".vw-line.assistant").first().waitFor({ timeout: 30000 });
    await page.locator(".vw-line.user").first().waitFor({ timeout: 60000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, "02-captions.png") });
    step("captions", { lines: await page.locator(".vw-line").allInnerTexts() });
    // the meter moved and the captions are an aria-live log
    report.a11y = {
      log: await page.locator(".vw-captions").getAttribute("role"),
      live: await page.locator(".vw-captions").getAttribute("aria-live"),
      meter: await page.locator('[role="meter"]').getAttribute("aria-label"),
      status: await page.locator('.vw-status[role="status"]').count(),
    };
    await page.waitForURL(/\/studio\/p\/p_[a-z0-9]+/, { timeout: 150000 });
    const pid = /\/p\/(p_[a-z0-9]+)/.exec(page.url())[1];
    step("handed over to project", { pid });
    await page.locator(".vw-card").waitFor({ timeout: 30000 });
    await page.waitForTimeout(6000);
    await page.screenshot({ path: path.join(OUT, "03-project.png") });
    await page.locator(".vw-card").scrollIntoViewIfNeeded();
    await page.locator(".vw-card").screenshot({ path: path.join(OUT, "03b-project-card.png") });
    let p = {};
    for (let i = 0; i < 20; i++) {
      // the final record (with the recording) is written when the call closes, just after the hand-over
      p = await (await page.request.get(`${BASE}/api/projects/${pid}`)).json();
      if (p.voice && p.voice.ended) break;
      await page.waitForTimeout(500);
    }
    report.project = {
      pid,
      chat: (p.chat || []).map((m) => `${m.role}/${m.kind}: ${String(m.text || "").slice(0, 160)}`),
      voice: p.voice && { model: p.voice.model, wish: p.voice.wish, details: p.voice.details, turns: (p.voice.turns || []).length, seconds: p.voice.seconds, audio_url: p.voice.audio_url },
      live_receipt: ((p.usage || {}).turns || []).filter((t) => t.kind === "live").map((t) => t.calls && t.calls[0]),
    };
    step("project", { chat: report.project.chat.length, voice_turns: report.project.voice && report.project.voice.turns });

    // ---------------------------------------------------------------- 2. the read-only share link
    const share = await (await page.request.post(`${BASE}/api/projects/${pid}/share`)).json();
    const anon = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const sp = await anon.newPage();
    watch(sp, "share");
    await sp.goto(`${BASE}/studio/s/${share.token}`);
    await sp.locator(".vw-card").waitFor({ timeout: 30000 });
    await sp.waitForTimeout(1500);
    await sp.screenshot({ path: path.join(OUT, "04-share.png") });
    await sp.locator(".vw-card").scrollIntoViewIfNeeded();
    await sp.locator(".vw-card").screenshot({ path: path.join(OUT, "04b-share-card.png") });
    report.share = { token: share.token, card: await sp.locator(".vw-card").innerText().then((t) => t.slice(0, 600)) };
    const audio = sp.locator(".vw-card audio");
    if (await audio.count()) {
      report.share.audio_src = await audio.getAttribute("src");
      await audio.evaluate((a) => a.play());
      await sp.waitForTimeout(2000);
      report.share.audio = await audio.evaluate((a) => ({ t: a.currentTime, duration: a.duration, error: a.error && a.error.code, paused: a.paused }));
    }
    step("share card", { chars: report.share.card.length, audio: report.share.audio || null });
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
    const pp = await phone.newPage();
    await pp.goto(`${BASE}/studio/s/${share.token}`);
    await pp.locator(".vw-card").waitFor({ timeout: 30000 });
    await pp.screenshot({ path: path.join(OUT, "05-share-phone.png") });
    report.share.phone_overflow = await pp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);

    // ---------------------------------------------------------------- 2b. Talk inside a still-empty project
    const empty = await (await page.request.post(`${BASE}/api/projects`, { data: { mode: "fast" } })).json();
    await page.goto(`${BASE}/studio/p/${empty.id}`);
    await page.getByRole("button", { name: "Talk", exact: true }).click();
    await page.locator(".vw-status", { hasText: /Listening|talking/ }).waitFor({ timeout: 40000 });
    await page.getByRole("dialog").waitFor({ state: "detached", timeout: 150000 });
    await page.locator(".vw-card").waitFor({ timeout: 30000 });
    await page.waitForTimeout(5000);
    await page.screenshot({ path: path.join(OUT, "07-empty-project-talk.png") });
    const e2 = await (await page.request.get(`${BASE}/api/projects/${empty.id}`)).json();
    report.empty_project = { pid: empty.id, url: page.url(), voice_turns: ((e2.voice || {}).turns || []).length, chat: (e2.chat || []).map((m) => `${m.role}/${m.kind}`) };
    step("talk in an empty project", report.empty_project);

    // ---------------------------------------------------------------- 3. microphone blocked -> type instead
    const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    await ctx2.addCookies(await ctx.cookies());
    await ctx2.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    });
    const p2 = await ctx2.newPage();
    watch(p2, "blocked");
    await p2.goto(`${BASE}/studio/`);
    await p2.getByRole("button", { name: "Talk", exact: true }).click();
    await p2.getByText("The microphone is blocked").waitFor({ timeout: 10000 });
    await p2.waitForTimeout(600); // the dialog's entrance animation
    await p2.screenshot({ path: path.join(OUT, "06-mic-blocked.png") });
    await p2.getByRole("button", { name: "Type instead" }).click();
    await p2.getByRole("dialog").waitFor({ state: "detached", timeout: 5000 });
    await p2.waitForTimeout(300);
    report.blocked = { closed: true, focus: await p2.evaluate(() => (document.activeElement && document.activeElement.textContent || "").trim().slice(0, 40)) };
    step("mic blocked fallback", report.blocked);

    // ---------------------------------------------------------------- 4. keyboard: Tab to Talk, Enter opens, Escape cancels
    await ctx2.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = () => new Promise(() => {}); // the browser is still asking
    });
    const p3 = await ctx2.newPage();
    await p3.goto(`${BASE}/studio/`);
    await p3.getByRole("button", { name: "Talk", exact: true }).focus();
    await p3.keyboard.press("Enter");
    await p3.getByRole("dialog").waitFor({ timeout: 5000 });
    await p3.keyboard.press("Escape");
    await p3.getByRole("dialog").waitFor({ state: "detached", timeout: 5000 });
    report.keyboard = { enter_opens: true, escape_cancels: true };
    step("keyboard");
    const projects = (await (await p3.request.get(`${BASE}/api/projects`)).json()).projects;
    report.projects_after = projects.map((x) => x.id);
    ok = !!(report.project.voice && report.project.chat.length && report.share.card.includes("Spoken wish") &&
      report.empty_project.voice_turns && report.empty_project.url.includes(empty.id) && report.empty_project.chat.length);
  } catch (e) {
    report.failure = String(e && e.stack ? e.stack : e).slice(0, 2000);
    console.log("FAILED", report.failure);
  } finally {
    report.ok = ok;
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
    await browser.close();
  }
  process.exit(ok ? 0 : 1);
})();
