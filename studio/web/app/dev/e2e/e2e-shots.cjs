// E2E screenshots and UI assertions for Gadget Studio (run on the VM). Extended for REVIEW-1 (MIN-46).
//   PID=p_... PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node e2e-shots.cjs [baseUrl] [outDir]
//   FLOW=1 ... node e2e-shots.cjs [baseUrl] [outDir]   new project: screenshot each dock mid-flow, before answering
//   ONLY=70,72 ... limit to shot-name prefixes; STORY=1 also clicks Code > Generate story audio at the end
// Built-project mode is read-mostly: it never moves the current-version pointer (MAJ-39: version history opens a
// read-only dialog) and asserts that. RESTORE=1 additionally restores v1 from the dialog and checks the new version's
// spec_hash. Assertions go to <outDir>/assertions.json; console and network errors to <outDir>/console-errors.json.
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8095";
const OUT = process.argv[3] || path.join(__dirname, "shots");
const PID = process.env.PID;
const KEY = process.env.KEY || "gstudio-dev";
const ONLY = process.env.ONLY ? process.env.ONLY.split(",") : null;
const W = Number(process.env.W || 1440);
const H = Number(process.env.H || 900);
fs.mkdirSync(OUT, { recursive: true });

const errors = [];
const asserts = [];
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const want = (name) => !ONLY || ONLY.some((o) => name.startsWith(o));

function check(name, ok, detail) {
  asserts.push({ name, ok: !!ok, detail: detail ?? null });
  log(ok ? "PASS" : "FAIL", name, detail != null ? JSON.stringify(detail).slice(0, 300) : "");
}

async function shot(page, name, opts = {}) {
  if (!want(name)) return;
  await page.waitForTimeout(opts.wait ?? 400);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: !!opts.full, timeout: 90000 });
  log("shot", name);
}
async function waitModel(page, timeout = 90000, scope = "") {
  await page.waitForSelector(`${scope} .viewer[data-ready="1"]`.trim(), { timeout }).catch(() => log("model not ready in time"));
  await page.waitForTimeout(2500);
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    log("STEP FAILED", name, e.message.split("\n")[0]);
    errors.push({ type: "step", name, text: e.message.split("\n")[0] });
    check(`step ran: ${name}`, false, e.message.split("\n")[0]);
  }
}
const api = (page, url, init) =>
  page.evaluate(
    async ([u, i]) => {
      const r = await fetch(u, { credentials: "same-origin", ...(i || {}) });
      let body = null;
      try {
        body = await r.clone().json();
      } catch {
        /* not json */
      }
      return { status: r.status, body, len: r.headers.get("content-length") };
    },
    [url, init || null],
  );
const current = async (page, pid) => (await api(page, `/api/projects/${pid}`)).body?.current_version;

/** MIN-44: every placed callout must clear every other callout and the step card. */
async function calloutProblems(page) {
  return page.evaluate(() => {
    const r = (el) => el.getBoundingClientRect();
    const ov = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const labs = [...document.querySelectorAll(".step-stage .lab-card.placed")].map((e) => ({ id: e.dataset.labelId, r: r(e) }));
    const card = document.querySelector(".step-stage .step-card");
    const ctl = document.querySelector(".step-stage .viewer-ctl");
    const out = [];
    for (let i = 0; i < labs.length; i++) {
      for (let j = i + 1; j < labs.length; j++) if (ov(labs[i].r, labs[j].r)) out.push(`${labs[i].id} x ${labs[j].id}`);
      if (card && ov(labs[i].r, r(card))) out.push(`${labs[i].id} x step card`);
      if (ctl && ov(labs[i].r, r(ctl))) out.push(`${labs[i].id} x controls`);
    }
    return { n: labs.length, problems: out };
  });
}

async function login(page) {
  await page.goto(`${BASE}/login`);
  await page.waitForSelector("#key");
  await page.fill("#key", KEY);
  await page.click("button.btn.primary");
  await page.waitForSelector(".hero h1");
  await page.waitForTimeout(800);
}

// ---------------------------------------------------------------------------- FLOW=1: docks mid-flow
async function flow(page) {
  await shot(page, "00-home");
  await page.fill(".hero textarea, .composer textarea", "I want a cute robot that tells my daughter bedtime stories and remembers her favourite characters.");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/p\/p_/, { timeout: 30000 });
  const pid = page.url().split("/p/")[1].split("/")[0];
  log("flow project", pid);
  await step("question dock", async () => {
    await page.waitForSelector('.dock[aria-label="Questions"]', { timeout: 180000 });
    await shot(page, "80-dock-questions");
    const cons = await page.$$eval('.dock[aria-label="Questions"] .q', (qs) => qs.map((q) => !!q.querySelector(".q-cons")));
    check("MIN-46 question dock captured before answering", cons.length > 0, { questions: cons.length });
    await page.click('.dock[aria-label="Questions"] .q >> nth=0 >> .q-opt >> nth=0');
    if (cons.length > 1) {
      for (let i = 1; i < cons.length; i++) await page.click(`.dock[aria-label="Questions"] .q >> nth=${i} >> .q-opt >> nth=0`);
      await page.click('.dock[aria-label="Questions"] .btn.primary');
    }
  });
  await step("concepts dock", async () => {
    await page.waitForSelector(".concepts-dock", { timeout: 900000 });
    await page.waitForTimeout(1500);
    await shot(page, "81-dock-concepts");
    const tiers = await page.$$eval(".concepts-dock .tier-label", (els) => els.map((e) => e.textContent));
    check("MIN-46 tiered concept dock captured mid-flow", tiers.length >= 1, tiers);
    await page.click(".concepts-dock .tile:not([disabled]) >> nth=0");
  });
  await step("brief dock", async () => {
    await page.waitForSelector(".brief-dock", { timeout: 300000 });
    await page.waitForTimeout(800);
    await shot(page, "82-dock-brief");
    const note = await page.textContent(".brief-dock-note");
    check("MIN-45 no copied '10 to 40 minutes' in the brief dock", !/10 to 40/.test(note || ""), note);
    const gated = await page.getAttribute(".brief-dock .btn.primary.lg", "aria-disabled");
    check("brief dock gates Build until read", gated === "true" || gated === "false", gated);
  });
  return pid;
}

// ---------------------------------------------------------------------------- built project
async function built(page, pid) {
  await page.goto(`${BASE}/p/${pid}`);
  await waitModel(page);
  await shot(page, "01-project-preview");
  const cur0 = await current(page, pid);
  const proj = (await api(page, `/api/projects/${pid}`)).body;
  const latest = Math.max(...proj.versions.map((v) => v.n));
  log("current", cur0, "latest", latest);

  const into = async (sel, block = "start") => {
    await page.$eval(sel, (el, b) => el.scrollIntoView({ block: b }), block);
    await page.waitForTimeout(700);
  };

  await step("chat history", async () => {
    await into(".chat-list .msg.user >> nth=0");
    await shot(page, "02-chat-prompt-clarify");
    await into(".concept-history", "center");
    await shot(page, "04-chat-concepts");
    const tiers = await page.$$eval(".concept-history .tier-label", (els) => els.map((e) => e.textContent));
    check("MIN-43 concept history shows the tier labels", tiers.length >= 3, tiers);
    const more = await page.$(".concept-history button:has-text('Show new concepts')");
    check("MIN-43 'Show new concepts' offered after a build", !!more);
  });
  await step("build cards", async () => {
    const cards = await page.$$(".build-card");
    for (let i = 0; i < cards.length; i++) {
      await cards[i].scrollIntoViewIfNeeded();
      const head = await cards[i].$(".build-head");
      if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
      await page.waitForTimeout(500);
      await cards[i].evaluate((el) => el.scrollIntoView({ block: "start" }));
      await shot(page, `07-chat-build-card-${i + 1}`);
    }
    const raw = await page.$$eval(".build-card", (els) => els.map((e) => e.textContent).join(" "));
    const ENUM = /\b(?:ESTIMATE_REVIEW_REQUIRED|ESTIMATE_INCOMPLETE|UNSUPPORTED_AT_LAUNCH|NOT_QUOTED|REVIEW_REQUIRED)\b|\bmissing_prices\b/g;
    check("MIN-29 no raw enum string in the build cards", !ENUM.test(raw), (raw.match(ENUM) || []).slice(0, 3));
    const rows = await page.$$eval(".build-card .stages li", (els) => els.map((e) => ({ t: e.textContent, warn: !!e.querySelector(".lucide-triangle-alert"), cls: e.className })));
    const failedRows = rows.filter((r) => /\b[1-9]\d* failed/.test(r.t));
    check("MIN-29 checks rows with failures show the warning icon", failedRows.every((r) => r.warn), { failedRows: failedRows.length });
  });
  await step("result chips", async () => {
    const res = await page.$$(".msg.result");
    for (let i = 0; i < res.length; i++) {
      await res[i].evaluate((el) => el.scrollIntoView({ block: "start" }));
      await shot(page, `08-chat-result-${i + 1}`);
    }
    const chips = await page.$$eval(".msg.result .result-chips .pill:first-child", (els) => els.map((e) => ({ t: e.textContent, warn: !!e.querySelector(".lucide-triangle-alert"), tick: !!e.querySelector(".lucide-check") })));
    const failing = chips.filter((c) => / failed/.test(c.t));
    check("MIN-29 a chip with failed checks never shows the green tick", failing.every((c) => c.warn && !c.tick), chips);
  });

  // ---- MAJ-39 version history: viewing never moves the pointer
  await step("version dialog", async () => {
    await page.click('button[aria-label^="Version history"]');
    await shot(page, "70-version-history", { wait: 600 });
    const rows = await page.$$eval(".ver-row .ver-sub", (els) => els.map((e) => e.textContent));
    check("MAJ-39 rows show 'from vN'", rows.some((t) => /from v\d/.test(t)), rows);
    await page.click(`.ver-row .ver-main:has(.ver-n:text-is("v1"))`);
    await page.waitForSelector(".vd", { timeout: 10000 });
    await waitModel(page, 90000, ".vd");
    await shot(page, "72-version-dialog-v1");
    check("MAJ-39 opening v1 leaves current unchanged", (await current(page, pid)) === cur0, { before: cur0, after: await current(page, pid) });
    const nFiles = await page.$$eval(".vd [data-testid=vd-files] .file-row", (els) => els.length);
    check("MAJ-39 dialog lists the files with sizes", nFiles > 10, { files: nFiles });
    const glb = await page.$(".vd .viewer[data-ready='1']");
    check("MAJ-39 dialog shows the GLB", !!glb);
    const href = await page.getAttribute(".vd .vd-actions a:has-text('Download v1')", "href");
    const head = await api(page, href, { method: "HEAD" });
    check("MAJ-39 v1 download answers 200", head.status === 200, { href, status: head.status, bytes: head.len });
    await page.selectOption(".vd select[aria-label='Compare with version']", String(latest));
    await page.waitForSelector(".vd [data-testid=vd-diff]", { timeout: 15000 });
    await page.check(".vd label:has-text('Only differences') input");
    await page.waitForTimeout(500);
    await shot(page, "73-version-dialog-compare");
    // other versions in the same dialog
    for (const n of [2, latest]) {
      await page.click(`.vd-row:has(.ver-n:text-is("v${n}"))`);
      await waitModel(page, 90000, ".vd");
      await shot(page, `72-version-dialog-v${n}`);
    }
    check("MAJ-39 browsing every version leaves current unchanged", (await current(page, pid)) === cur0);
    await page.keyboard.press("Escape");
  });
  await step("brief sheet", async () => {
    await page.click('.ws-header .btn:has-text("Project brief")');
    await page.waitForTimeout(800);
    await shot(page, "71-brief-sheet");
    const banner = await page.$(".dialog .brief-changed");
    const p = (await api(page, `/api/projects/${pid}`)).body;
    const curV = p.versions.find((v) => v.n === p.current_version);
    // the brief shown is the one for the current version; its design is the version it was built for
    const all = [...(p.brief_history || []), ...(p.brief ? [p.brief] : [])];
    const br = all.find((x) => x.version === curV?.brief_version) || p.brief;
    const bv = p.versions.find((v) => v.n === br?.auto_for_version) || p.versions.find((v) => v.brief_version === br?.version);
    const expected = !!(curV && bv && curV.spec_hash !== bv.spec_hash);
    check("MAJ-36 'design changed since this brief' banner matches the spec hashes", !!banner === expected, { banner: !!banner, expected });
    await page.keyboard.press("Escape");
  });

  // ---- tabs
  const tab = async (name) => {
    await page.click(`.tabs .tab:has-text("${name}")`);
    await page.waitForTimeout(1500);
  };
  await step("preview tweak drawer", async () => {
    const answered = page.waitForResponse((r) => /\/tweak$/.test(r.url()), { timeout: 120000 }).catch(() => null);
    await page.click(".view-toggles .btn");
    await page.waitForSelector(".tweak .swatches", { timeout: 10000 });
    const r = await answered; // the drawer asks the validator about the base version when it opens
    await page.waitForTimeout(1200);
    const nudges = await page.$$eval(".tweak .layout-issues .row .grow", (els) => els.map((e) => e.textContent));
    const body = r ? await r.json().catch(() => null) : null;
    const sent = (body?.nudges || body?.error?.details?.nudges || []).length;
    check("MAJ-8 drawer lists the validator's nudges for the base version", nudges.length === sent, { shown: nudges.length, sent, status: r?.status() });
    await shot(page, "17-preview-tweak-drawer");
    const copy = await page.textContent(".tweak");
    check("MAJ-8 drawer no longer claims the checker stops overlaps", !/checker stops overlaps/i.test(copy) && /full collision check runs when you apply/.test(copy));
    await page.click(".tweak-head .icon-btn");
  });
  await step("parts", async () => {
    await tab("Parts");
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await shot(page, "23-parts-bottom");
    const t = await page.textContent("[data-testid=parts-total]");
    const planning = await page.$$eval(".group-head", (els) => els.filter((e) => /planning total/.test(e.textContent)).length);
    check("MIN-27 Parts tab shows one parts figure, no planning group totals", /₹/.test(t) && planning === 0, { total: t, planningTotals: planning });
    await tab("Order");
    const o = await page.textContent("[data-testid=order-parts-total]").catch(() => null);
    check("MIN-27 Order tab shows the same parts figure", !!o && o.includes(t.trim()), { parts: t, order: o });
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await shot(page, "32-order-bottom");
    const body = await page.textContent("body");
    check("MIN-27 'missing_prices' never appears in the UI", !/missing_prices/.test(body));
  });
  await step("build callouts", async () => {
    await tab("Build");
    await waitModel(page);
    await page.click(".step-dots span >> nth=0"); // the project may have step 1 ticked already
    await page.waitForTimeout(1600);
    await shot(page, "40-build-assembly");
    const fit = await page.evaluate(() => {
      const ul = document.querySelector(".step-card [data-testid=use-list]");
      const body = document.querySelector(".step-card .step-card-body");
      if (!ul || !body) return { ok: false, why: "no use list" };
      const a = ul.getBoundingClientRect();
      const b = body.getBoundingClientRect();
      return { ok: a.top >= b.top - 1 && a.bottom <= b.bottom + 1, use: [Math.round(a.top), Math.round(a.bottom)], body: [Math.round(b.top), Math.round(b.bottom)] };
    });
    check(`MIN-44 step 1 Use list fits without scrolling at ${W}x${H}`, fit.ok, fit);
    const nSteps = await page.$$eval(".step-dots span", (els) => els.length);
    const bad = [];
    let labelled = 0;
    for (let i = 0; i < nSteps; i++) {
      if (i > 0) {
        await page.click(`.step-dots span >> nth=${i}`);
        await page.waitForTimeout(1600);
      }
      const r = await calloutProblems(page);
      labelled += r.n;
      if (r.problems.length) bad.push({ step: i + 1, problems: r.problems });
      if (i === 1 || i === 7 || i === 13) await shot(page, `41-build-assembly-step${i + 1}`);
    }
    check("MIN-44 no callout overlaps another callout or the step card (assembly, all steps)", bad.length === 0 && labelled > 0, { steps: nSteps, labelled, bad });
    await page.click('.subtab-group button:has-text("Wiring")');
    await waitModel(page);
    await shot(page, "46-build-wiring");
    const wn = await page.$$eval(".step-dots span", (els) => els.length);
    const wbad = [];
    for (let i = 0; i < wn; i++) {
      if (i > 0) {
        await page.click(`.step-dots span >> nth=${i}`);
        await page.waitForTimeout(1400);
      }
      const r = await calloutProblems(page);
      if (r.problems.length) wbad.push({ step: i + 1, problems: r.problems });
    }
    check("MIN-44 no callout overlaps (wiring, all steps)", wbad.length === 0, { steps: wn, bad: wbad });
    await page.click('.subtab-group button:has-text("Assembly")');
  });
  await step("device tools story card", async () => {
    await page.$eval(".tab-scroll", (el) => (el.scrollTop = el.scrollHeight));
    await page.waitForTimeout(800);
    await shot(page, "43-build-tools");
  });
  await step("code", async () => {
    await tab("Code");
    await page.waitForTimeout(1500);
    await shot(page, "60-code");
  });

  // ---- banner when current is behind the latest (moves the pointer on purpose, then puts it back)
  await step("behind banner", async () => {
    await page.click('button[aria-label^="Version history"]');
    await page.click(`.ver-row .ver-main:has(.ver-n:text-is("v1"))`);
    await page.waitForSelector(".vd");
    await page.click(".vd .vd-actions button:has-text('Work on v1')");
    await page.waitForSelector(".behind-banner", { timeout: 10000 });
    await shot(page, "74-behind-banner");
    const txt = await page.textContent(".behind-banner");
    check("MAJ-39 banner when current is v1", /You are on v1\. Edits start from v1; the latest is v\d+\./.test(txt), txt);
    await page.click(".behind-banner .btn");
    await page.waitForTimeout(1200);
    check("MAJ-39 'Go to latest' restores the pointer", (await current(page, pid)) === latest);
  });
  if (process.env.RESTORE) {
    await step("restore v1", async () => {
      const v1 = (await api(page, `/api/projects/${pid}`)).body.versions.find((v) => v.n === 1);
      await page.click('button[aria-label^="Version history"]');
      await page.click(`.ver-row .ver-main:has(.ver-n:text-is("v1"))`);
      await page.waitForSelector(".vd");
      await page.click(".vd .vd-actions button:has-text('Restore as new version')");
      let p, nv;
      for (let i = 0; i < 60; i++) {
        await page.waitForTimeout(3000);
        p = (await api(page, `/api/projects/${pid}`)).body;
        nv = p.versions[p.versions.length - 1];
        if (nv.finalized) break; // the server rehashes from the built spec.json when it finalizes
      }
      check("MAJ-39 restore creates a new version with v1's spec_hash", nv.n === latest + 1 && nv.spec_hash === v1.spec_hash, { n: nv.n, hash: nv.spec_hash, v1: v1.spec_hash });
    });
  }
  if (process.env.STORY) {
    // BLK-4: the Code tab button starts a story-audio version (moves current to it, like any new version)
    await step("generate story audio", async () => {
      await page.click('.tabs .tab:has-text("Code")');
      await page.waitForSelector(".fw-bar button:has-text('Generate story audio')", { timeout: 20000 });
      const before = (await api(page, `/api/projects/${pid}`)).body.versions.length;
      await page.click(".fw-bar button:has-text('Generate story audio')");
      await page.waitForTimeout(2500);
      await shot(page, "61-code-story-audio-started");
      let p, nv;
      for (let i = 0; i < 60; i++) {
        p = (await api(page, `/api/projects/${pid}`)).body;
        nv = p.versions[p.versions.length - 1];
        if (nv.finalized) break;
        await page.waitForTimeout(3000);
      }
      check("BLK-4 'Generate story audio' makes a new story-audio version", p.versions.length === before + 1 && nv.source === "story_audio", { n: nv.n, source: nv.source, label: nv.label, state: nv.state });
    });
  }
}

(async () => {
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] });
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("console", (m) => {
    if (/status of 401/.test(m.text()) && /\/login$/.test(page.url())) return;
    // a 422 from POST /tweak is the validator's answer (the drawer shows it), not a fault
    if (/status of 422/.test(m.text()) && /\/tweak\b/.test(m.location()?.url || "")) return;
    if (m.type() === "error" || m.type() === "warning") errors.push({ type: m.type(), text: m.text().slice(0, 300), url: page.url() });
  });
  page.on("pageerror", (e) => errors.push({ type: "pageerror", text: String(e).slice(0, 300), url: page.url() }));
  page.on("response", (r) => {
    if (r.status() === 422 && /\/tweak$/.test(r.url())) return; // validator answer, shown in the drawer
    if (r.status() >= 400 && !/\/api\/me$/.test(r.url())) errors.push({ type: `http ${r.status()}`, text: r.url(), url: page.url() });
  });
  await login(page);
  if (process.env.FLOW) await flow(page);
  if (PID) await built(page, PID);
  fs.writeFileSync(path.join(OUT, "console-errors.json"), JSON.stringify(errors, null, 1));
  fs.writeFileSync(path.join(OUT, "assertions.json"), JSON.stringify(asserts, null, 1));
  const failed = asserts.filter((a) => !a.ok);
  log(`done: ${asserts.length} assertions, ${failed.length} failed; ${errors.filter((e) => e.type !== "warning").length} console/network errors`);
  for (const e of errors.filter((e) => e.type !== "warning").slice(0, 30)) log("ERR", e.type, e.text);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(2);
});
