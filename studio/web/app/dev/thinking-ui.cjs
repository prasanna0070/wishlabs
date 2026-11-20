// Browser end-to-end check of the working card (run on the VM, never on the laptop; run by thinking-ui.sh).
// AGENT=gemini (real Vertex AI, Deep mode): a first turn is reloaded mid-turn (the card, its step and the thinking
// come back from GET /turn; the composer does not send), then the concept turn is reloaded too and shot on desktop
// (collapsed and open) and on a phone; each result must load once the turn ends.
// AGENT=scripted (canned thoughts, no model calls): reload mid-turn, a dropped connection (offline for a moment),
// a second tab's 409 (friendly note + Show progress), and a stale lock (16 min) whose Retry sends the message again.
// Screenshots and report.json go to OUT.
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers KEY_FILE=<0600 file> AGENT=gemini STORE=<store dir> \
//     node web/app/dev/thinking-ui.cjs <port> <out>
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.argv[2] || 9220);
const OUT = path.resolve(process.argv[3] || "_out/thinking-ui");
const BASE = `http://127.0.0.1:${PORT}`;
const KEY = fs.readFileSync(process.env.KEY_FILE, "utf8").trim();
const AGENT = process.env.AGENT || "gemini";
const STORE = process.env.STORE || "";
const WISH = "I want a cute robot that tells my daughter bedtime stories and remembers her favourite characters.";
fs.mkdirSync(OUT, { recursive: true });
const report = { agent: AGENT, steps: [], checks: {}, errors: [], requests: {} };
const failed = [];
const t0 = Date.now();
const step = (name, data = {}) => {
  const row = { t: ((Date.now() - t0) / 1000).toFixed(1), name, ...data };
  report.steps.push(row);
  console.log(JSON.stringify(row));
};
const check = (name, ok, data = {}) => {
  report.checks[name] = { ok: !!ok, ...data };
  console.log(JSON.stringify({ check: name, ok: !!ok, ...data }));
  if (!ok) failed.push(name);
};

async function signIn(page) {
  await page.goto(`${BASE}/studio/login`);
  await page.locator('input[type="password"]').fill(KEY);
  await page.locator('input[type="password"]').press("Enter");
  await page.waitForURL((u) => /\/studio\/?$/.test(new URL(u).pathname), { timeout: 20000 });
}

function watch(page, tag) {
  report.requests[tag] = { chat: 0, turn: 0, conflict: 0 };
  page.on("console", (m) => {
    if (m.type() === "error" && !/net::ERR_INTERNET_DISCONNECTED|Failed to fetch|409/.test(m.text())) report.errors.push(`${tag} console: ${m.text().slice(0, 300)}`);
  });
  page.on("pageerror", (e) => report.errors.push(`${tag} pageerror: ${String(e).slice(0, 300)}`));
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() === "POST" && /\/chat$/.test(p)) report.requests[tag].chat++;
    if (r.method() === "GET" && /\/turn$/.test(p)) report.requests[tag].turn++;
  });
  page.on("response", (r) => {
    if (r.status() === 409) report.requests[tag].conflict++;
  });
}

const composer = (page) => page.getByRole("textbox", { name: "Message", exact: true });

/** A dropped connection, exactly: the page's own fetch of POST /chat errors after `window.__dropChatAfter` chunks
 * (0 = never), as a network drop does mid-stream. The turn goes on on the server. */
function dropChatStreams() {
  const orig = window.fetch;
  window.__dropChatAfter = 0;
  window.fetch = async (...args) => {
    const res = await orig(...args);
    const url = String((args[0] && args[0].url) || args[0]);
    const limit = window.__dropChatAfter;
    if (!limit || !res.body || !/\/chat$/.test(new URL(url, location.href).pathname)) return res;
    window.__dropChatAfter = 0;
    const reader = res.body.getReader();
    let n = 0;
    const body = new ReadableStream({
      async pull(ctrl) {
        if (n >= limit) {
          reader.cancel().catch(() => {});
          ctrl.error(new TypeError("network error (simulated drop)"));
          return;
        }
        const { value, done } = await reader.read();
        if (done) return ctrl.close();
        n += 1;
        ctrl.enqueue(value);
      },
    });
    return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}

async function newProject(page, mode) {
  const r = await page.request.post(`${BASE}/api/projects`, { data: { mode } });
  return (await r.json()).id;
}

/** Contrast of an element's text against the card it sits on (WCAG ratio). */
async function contrast(page, sel) {
  return page.locator(sel).first().evaluate((el) => {
    const rgb = (s) => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const lum = ([r, g, b]) => {
      const f = (c) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    let bg = null;
    for (let n = el; n && !bg; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) bg = c;
    }
    const a = lum(rgb(getComputedStyle(el).color));
    const b = lum(rgb(bg || "rgb(255,255,255)"));
    return Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100;
  });
}

/** After a reload mid-turn: the card, its step and (when the model shares them) the thinking come back. */
async function reloadMidTurn(page, tag, { wantThought = true } = {}) {
  const posts = report.requests[tag].chat;
  await page.reload();
  const card = page.locator(".working-card");
  await card.waitFor({ timeout: 15000 });
  const stepText = (await page.locator(".wc-step").innerText()).trim();
  let thought = "";
  if (wantThought) {
    await page.locator(".wc-latest").waitFor({ timeout: 30000 });
    thought = (await page.locator(".wc-latest").innerText()).trim();
  }
  check(`${tag}: card after reload`, (await card.isVisible()) && stepText.length > 1 && (!wantThought || thought.length > 1), { step: stepText, thought });
  const busy = page.locator(".composer-busy");
  check(`${tag}: composer says it is still working`, await busy.isVisible(), { text: (await busy.innerText().catch(() => "")).trim() });
  await composer(page).fill("Are you still there?");
  await composer(page).press("Enter");
  await page.waitForTimeout(800);
  check(`${tag}: composer does not send mid-turn`, report.requests[tag].chat === posts && (await composer(page).inputValue()) === "Are you still there?" && report.requests[tag].conflict === 0);
  await composer(page).fill("");
  return { step: stepText, thought };
}

async function waitTurnEnd(page, timeout) {
  await page.locator(".working-card").waitFor({ state: "detached", timeout });
  await page.locator(".activity-pill").last().waitFor({ timeout: 15000 });
}

async function realVertex(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  watch(page, "desk");
  await signIn(page);
  const pid = await newProject(page, "pro");
  report.pid = pid;
  await page.goto(`${BASE}/studio/p/${pid}`);
  await composer(page).fill(WISH);
  await composer(page).press("Enter");
  await page.locator(".working-card").waitFor({ timeout: 15000 });
  step("turn 1 sent (Deep)", { pid });
  await page.locator(".wc-latest").waitFor({ timeout: 60000 });
  step("turn 1 live thought", { text: (await page.locator(".wc-latest").innerText()).trim() });
  await page.screenshot({ path: path.join(OUT, "01-live.png") });
  const r1 = await reloadMidTurn(page, "desk");
  step("turn 1 reloaded mid-turn", r1);
  await page.screenshot({ path: path.join(OUT, "02-after-reload.png") });
  await waitTurnEnd(page, 240000);
  const q = page.getByRole("group", { name: "Questions" });
  const anyAnswer = (await q.count()) || (await page.locator(".msg.assistant").count());
  check("turn 1: result loads after the turn ends", anyAnswer > 0, { questions: await q.count() });
  step("turn 1 done");
  // ---------------------------------------------------------------- turn 2: concepts (minutes in Deep mode)
  if (await q.count()) await page.getByRole("button", { name: "Use your suggestions" }).click();
  else {
    await composer(page).fill("Use your defaults and show me concepts.");
    await composer(page).press("Enter");
  }
  await page.locator(".working-card").waitFor({ timeout: 15000 });
  step("turn 2 sent");
  await page.locator(".wc-latest").waitFor({ timeout: 90000 });
  const r2 = await reloadMidTurn(page, "desk");
  step("turn 2 reloaded mid-turn", r2);
  await page.locator(".working-card").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(OUT, "desktop-working.png") });
  const toggle = page.locator(".wc-toggle");
  await toggle.focus();
  await page.keyboard.press("Enter");
  check("toggle opens with the keyboard", (await toggle.getAttribute("aria-expanded")) === "true" && (await page.locator(".wc-thoughts").isVisible()));
  check("step is a polite live region", (await page.locator(".wc-step").getAttribute("aria-live")) === "polite");
  for (const sel of [".wc-step", ".wc-latest", ".wc-note", ".wc-toggle", ".wc-thoughts .md p"]) {
    if (await page.locator(sel).count()) report.checks[`contrast ${sel}`] = { ok: (await contrast(page, sel)) >= 4.5, ratio: await contrast(page, sel) };
  }
  await page.waitForTimeout(400);
  await page.locator(".working-card").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(OUT, "desktop-thinking-open.png") });
  await page.keyboard.press("Enter");
  check("toggle closes with the keyboard", (await toggle.getAttribute("aria-expanded")) === "false");
  // phone: the same running turn in a second browser
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const pp = await phone.newPage();
  watch(pp, "phone");
  await signIn(pp);
  await pp.goto(`${BASE}/studio/p/${pid}`);
  await pp.locator(".working-card").waitFor({ timeout: 15000 });
  await pp.locator(".wc-latest").waitFor({ timeout: 30000 }).catch(() => {});
  await pp.locator(".working-card").scrollIntoViewIfNeeded();
  await pp.waitForTimeout(500);
  await pp.screenshot({ path: path.join(OUT, "phone-working.png") });
  check("phone: card, no horizontal overflow", (await pp.locator(".working-card").isVisible()) && !(await pp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)), {
    step: (await pp.locator(".wc-step").innerText()).trim(),
  });
  await pp.locator(".wc-toggle").tap();
  await pp.waitForTimeout(400);
  await pp.screenshot({ path: path.join(OUT, "phone-thinking-open.png") });
  step("screenshots taken");
  // the concepts load when the turn ends (both pages)
  await waitTurnEnd(page, 15 * 60 * 1000);
  const concepts = page.getByRole("group", { name: "Concepts" });
  await concepts.waitFor({ timeout: 30000 }).catch(() => {});
  check("turn 2: concepts load after the turn ends", (await concepts.count()) > 0 || (await page.locator(".msg.assistant").count()) > 1);
  await pp.locator(".working-card").waitFor({ state: "detached", timeout: 30000 });
  check("phone: card goes when the turn ends", (await pp.locator(".working-card").count()) === 0);
  await page.screenshot({ path: path.join(OUT, "03-result.png") });
  const view = await (await page.request.get(`${BASE}/api/projects/${pid}/turn`)).json();
  report.final_view = { state: view.state, steps: view.steps.map((s) => s.label + (s.detail ? ` [${s.detail}]` : "")), thoughts_chars: view.thoughts.length };
  check("turn record done", view.state === "done" && !view.running);
  check("polling happened", report.requests.desk.turn >= 3, { desk: report.requests.desk.turn, phone: report.requests.phone.turn });
}

async function scripted(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  await ctx.addInitScript(dropChatStreams);
  const page = await ctx.newPage();
  watch(page, "desk");
  await signIn(page);
  const pid = await newProject(page, "pro");
  report.pid = pid;
  await page.goto(`${BASE}/studio/p/${pid}`);
  await composer(page).fill(WISH);
  await composer(page).press("Enter");
  await page.locator(".working-card").waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  const r1 = await reloadMidTurn(page, "desk");
  step("reloaded mid-turn", r1);
  check("scripted: thought text after reload", /Reading the wish|Deciding what to ask/.test(r1.thought), r1);
  await page.screenshot({ path: path.join(OUT, "s-desktop-working.png") });
  await waitTurnEnd(page, 60000);
  check("scripted: questions load", (await page.getByRole("group", { name: "Questions" }).count()) > 0);
  // a dropped connection mid-turn (the chat stream errors after its first events): no red error, the card keeps
  // going from the server's record, the result loads
  const polls = report.requests.desk.turn;
  await page.evaluate(() => (window.__dropChatAfter = 1));
  const followed = page.waitForRequest((r) => r.method() === "GET" && /\/turn$/.test(new URL(r.url()).pathname), { timeout: 15000 }).catch(() => null);
  await page.getByRole("button", { name: "Use your suggestions" }).click();
  await page.locator(".working-card").waitFor({ timeout: 15000 });
  step("chat stream dropped after its first event", { followed: !!(await followed) });
  await page.waitForTimeout(4500);
  check("dropped stream: still working, no error", (await page.locator(".working-card").isVisible()) && (await page.locator(".callout.danger").count()) === 0, {
    step: (await page.locator(".wc-step").innerText().catch(() => "")).trim(),
    turn_polls_since_drop: report.requests.desk.turn - polls, // > 0: the page followed the turn on the server
  });
  await waitTurnEnd(page, 90000);
  const concepts = page.getByRole("group", { name: "Concepts" });
  await concepts.waitFor({ timeout: 30000 }).catch(() => {});
  check("dropped stream: concepts load", (await concepts.count()) > 0);
  // a second tab that did not know about the turn: its send gets a 409 -> a friendly note and the card
  const tabB = await ctx.newPage();
  watch(tabB, "tabB");
  await tabB.goto(`${BASE}/studio/p/${pid}`);
  await composer(tabB).waitFor();
  await composer(page).fill("Make it pink");
  await composer(page).press("Enter");
  await page.locator(".working-card").waitFor({ timeout: 15000 });
  await composer(tabB).fill("Make it blue");
  await composer(tabB).press("Enter");
  const note = tabB.locator(".callout.warning", { hasText: "still working on your previous message" });
  await note.waitFor({ timeout: 10000 });
  await tabB.locator(".working-card").waitFor({ timeout: 10000 });
  await tabB.getByRole("button", { name: "Show progress" }).click();
  await tabB.waitForTimeout(600);
  await tabB.screenshot({ path: path.join(OUT, "s-409-friendly.png") });
  check("409: friendly note, Show progress, the card", (await note.isVisible()) && (await tabB.locator(".working-card").isVisible()) && report.requests.tabB.conflict === 1, {
    note: (await note.innerText()).trim(),
  });
  check("409: the typed words are back in the box", (await composer(tabB).inputValue()) === "Make it blue");
  await waitTurnEnd(page, 90000);
  await tabB.locator(".working-card").waitFor({ state: "detached", timeout: 30000 });
  check("409 tab: card goes when the turn ends", (await tabB.locator(".working-card").count()) === 0);
  // a stale lock (16 min): the card says it stopped, Retry sends the same message again
  if (STORE) {
    const f = path.join(STORE, "projects", pid, "project.json");
    const p = JSON.parse(fs.readFileSync(f, "utf8"));
    p.turn = { id: "t_stale_e2e", started: new Date(Date.now() - 16 * 60000).toISOString().replace(/\.\d+Z$/, "Z") };
    fs.writeFileSync(f, JSON.stringify(p, null, 1));
    await page.reload();
    const stale = page.locator(".working-card.stale");
    await stale.waitFor({ timeout: 15000 });
    await stale.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(OUT, "s-stale.png") });
    check("stale: the last step stopped unexpectedly", /stopped unexpectedly/.test(await stale.innerText()));
    check("stale: composer can send", (await page.locator(".composer-busy").count()) === 0);
    const posts = report.requests.desk.chat;
    await page.getByRole("button", { name: "Retry" }).click();
    await page.locator(".working-card:not(.stale)").waitFor({ timeout: 15000 });
    check("stale: Retry sends the last message again", report.requests.desk.chat === posts + 1);
    await waitTurnEnd(page, 90000);
    const users = await page.locator(".msg.user").allInnerTexts();
    check("stale: the retried message is the last one", /Make it pink/.test(users[users.length - 1] || ""), { last: users[users.length - 1] });
  }
}

(async () => {
  const browser = await chromium.launch();
  try {
    if (AGENT === "scripted") await scripted(browser);
    else await realVertex(browser);
  } catch (e) {
    report.errors.push(`fatal: ${String(e && e.stack ? e.stack : e).slice(0, 1500)}`);
    failed.push("fatal");
  } finally {
    await browser.close();
  }
  for (const [k, v] of Object.entries(report.checks)) if (k.startsWith("contrast") && !v.ok) failed.push(k);
  report.failed = failed;
  report.seconds = (Date.now() - t0) / 1000;
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ failed, errors: report.errors.length, seconds: report.seconds }));
  process.exit(failed.length || report.errors.length ? 1 : 0);
})();
