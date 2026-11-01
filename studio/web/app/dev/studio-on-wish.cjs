// End-to-end check of Wish Labs on one domain, routed the way the wish.plugbook.si load balancer will route it
// (run on the VM, never on the laptop). A local stand-in for the load balancer sends /studio, /studio/* and /api/*
// to a dev gstudio-web (scripted agent, fake builder) and every other path to the landing page (studio/site, with
// nginx's try_files rule). A browser then walks landing -> wish -> sign-in -> pre-filled composer -> agent reply ->
// build -> share, on a desktop and on a 390x844 phone, and checks branding, the mark, console errors and overflow.
//   web/app/dev/serve.sh 8297                                   # backend serving dist/ (ACCESS_KEY=dev)
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/studio-on-wish.cjs 8297 8299 _out/studio-on-wish
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const http = require("http");
const path = require("path");

const BACK = Number(process.argv[2] || 8297);
const PORT = Number(process.argv[3] || 8299);
const OUT = path.resolve(process.argv[4] || path.join(__dirname, "..", "..", "..", "_out", "studio-on-wish"));
const KEY = process.env.ACCESS_KEY || "dev";
const SITE = path.resolve(__dirname, "..", "..", "..", "site");
const DIST = path.resolve(__dirname, "..", "dist");
const ORIGIN = `http://127.0.0.1:${PORT}`;
const BACKEND = `http://127.0.0.1:${BACK}`;
fs.mkdirSync(OUT, { recursive: true });

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
  ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".glb": "model/gltf-binary", ".json": "application/json",
  ".woff2": "font/woff2", ".mp4": "video/mp4", ".ico": "image/x-icon" };
// The path rules of deploy/wish-studio-routes.sh: /studio, /studio/* and /api/* go to gstudio-web-be.
const toStudio = (p) => p === "/studio" || p.startsWith("/studio/") || p.startsWith("/api/");

const lb = http.createServer((req, res) => {
  const p = new URL(req.url, ORIGIN).pathname;
  if (toStudio(p)) {
    const up = http.request({ host: "127.0.0.1", port: BACK, path: req.url, method: req.method,
      headers: { ...req.headers, host: `127.0.0.1:${PORT}` } }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    req.pipe(up);
    return;
  }
  // site/nginx.conf: try_files $uri $uri/ /index.html
  let f = path.join(SITE, decodeURIComponent(p));
  if (f.startsWith(SITE) && fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, "index.html");
  if (!f.startsWith(SITE) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(SITE, "index.html");
  res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream", "x-served-by": "landing" });
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(f).pipe(res);
});

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  console.log(ok ? "PASS" : "FAIL", name, detail ? `| ${String(detail).slice(0, 200)}` : "");
};
const shots = [];
const shot = async (page, name, full = false) => {
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, fullPage: full, timeout: 90000 });
  shots.push(file);
};

// Console errors, page errors, failed requests and HTTP errors, per page, across the whole walk.
const problems = [];
function watch(page, label) {
  page.on("console", (m) => { if (m.type() === "error") problems.push({ label, kind: "console.error", text: m.text(), at: page.url() }); });
  page.on("pageerror", (e) => problems.push({ label, kind: "pageerror", text: String(e), at: page.url() }));
  page.on("requestfailed", (r) => {
    const why = r.failure()?.errorText || "";
    if (why.includes("ERR_ABORTED")) return; // a navigation away cancels in-flight requests
    problems.push({ label, kind: "requestfailed", text: `${why} ${r.url()}`, at: page.url() });
  });
  page.on("response", (r) => { if (r.status() >= 400) problems.push({ label, kind: `http ${r.status()}`, text: r.url(), at: page.url() }); });
}

// "Gadget Studio" anywhere in the rendered DOM (text, attributes, title), plus the Wish Labs mark on studio pages.
const brandHits = [];
async function brand(page, label, { studio = true } = {}) {
  const r = await page.evaluate(() => {
    const re = /gadget[\s_-]*studio/i;
    const html = document.documentElement.outerHTML;
    const svg = document.querySelector(".logo svg");
    const rect = svg && svg.querySelector("rect");
    const p = svg && svg.querySelector("path");
    const b = svg && svg.getBoundingClientRect();
    return { hit: re.test(html) || re.test(document.title), title: document.title,
      mark: svg ? { fill: rect && rect.getAttribute("fill"), spark: p && p.getAttribute("fill"), w: b.width, h: b.height } : null,
      word: !!document.querySelector(".logo") && document.querySelector(".logo").textContent.includes("Wish Labs") };
  });
  if (r.hit) brandHits.push(label);
  check(`${label}: no "Gadget Studio" in the page`, !r.hit, r.title);
  if (studio) {
    const m = r.mark;
    check(`${label}: Wish Labs mark (violet square, white sparkle)`,
      m && String(m.fill).toUpperCase() === "#7C5CFF" && /^#f{3}(f{3})?$/i.test(String(m.spark)) && m.w > 10 && m.h > 10,
      JSON.stringify(m));
  }
  return r;
}

async function noOverflow(page, label) {
  const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, bw: document.body.scrollWidth, w: window.innerWidth }));
  check(`${label}: no horizontal overflow at ${r.w}px`, r.sw <= r.w + 1 && r.bw <= r.w + 1, JSON.stringify(r));
}

async function get(url, opts = {}) {
  const r = await fetch(url, { redirect: "manual", ...opts });
  const body = opts.method === "HEAD" ? "" : await r.text();
  return { status: r.status, type: r.headers.get("content-type") || "", location: r.headers.get("location"),
    by: r.headers.get("x-served-by"), body };
}

const WISH = "a lamp shaped like a cloud that glows blue the evening before it rains";
const PHONE_WISH = "a small speaker shaped like a seashell that plays ocean sounds at bedtime";

async function clarifyConceptsBrief(page) {
  // scripted agent: three questions -> concepts -> brief -> approve -> fake build -> result
  await page.waitForSelector(".dock .q", { timeout: 60000 });
  for (const q of await page.$$(".dock .q")) {
    const opt = (await q.$(".q-opt:nth-child(2)")) || (await q.$(".q-opt"));
    await opt.click();
  }
  const sendBtn = await page.$(".dock .dock-foot .btn.primary");
  if (sendBtn) await sendBtn.click();
  await page.waitForSelector(".concepts-dock .tile", { timeout: 120000 });
  await page.waitForTimeout(1200);
  await page.click(".concepts-dock .tile >> nth=0");
  await page.waitForSelector(".brief-dock", { timeout: 120000 });
  await page.waitForTimeout(800);
}

(async () => {
  await new Promise((r) => lb.listen(PORT, "127.0.0.1", r));
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });

  // ------------------------------------------------------------------ 0. what the load balancer will serve
  {
    const root = await get(`${ORIGIN}/`);
    check("LB / is the landing page", root.status === 200 && root.by === "landing" && root.body.includes("wish-input"), root.status);
    const s1 = await get(`${ORIGIN}/studio/`);
    check("LB /studio/ is the studio SPA (200 HTML)", s1.status === 200 && s1.type.startsWith("text/html") && s1.body.includes('id="root"') && !s1.by, `${s1.status} ${s1.type}`);
    const s0 = await get(`${ORIGIN}/studio`);
    check("LB /studio (no slash) is the studio SPA", s0.status === 200 && s0.body.includes('id="root"'), s0.status);
    const h = await get(`${ORIGIN}/api/health`);
    let hj = {};
    try { hj = JSON.parse(h.body); } catch { /* checked below */ }
    check("LB /api/health is 200 JSON from the studio", h.status === 200 && h.type.includes("json") && hj.ok === true, h.body.slice(0, 120));
    check("dev backend: scripted agent + fake builder (no Vertex calls)", hj.agent === "scripted" && hj.builder === "fake", `agent=${hj.agent} builder=${hj.builder}`);
    const css = await get(`${ORIGIN}/css/site.css`);
    check("LB /css stays with the landing", css.status === 200 && css.by === "landing" && css.type.includes("css"));
    const cfg = await get(`${ORIGIN}/js/config.js`);
    check("LB /js stays with the landing; STUDIO_URL is /studio/", cfg.by === "landing" && cfg.body.includes('STUDIO_URL = "/studio/"'));
    const img = fs.readdirSync(path.join(SITE, "assets", "img")).find((f) => /\.(webp|png|jpg|svg)$/.test(f));
    const a = await get(`${ORIGIN}/assets/img/${img}`, { method: "HEAD" });
    check("LB /assets stays with the landing", a.status === 200 && a.by === "landing", `/assets/img/${img} ${a.status}`);
    const fav = await get(`${ORIGIN}/studio/favicon.svg`);
    check("studio favicon is the Wish Labs mark", fav.status === 200 && fav.type.includes("svg") && /7C5CFF/i.test(fav.body), fav.status);
    const miss = await get(`${ORIGIN}/studio/assets/nope-123.js`);
    check("a missing /studio/assets file is 404, not index.html", miss.status === 404, miss.status);
    const api404 = await get(`${ORIGIN}/api/nope`);
    check("an unknown /api path is a JSON 404", api404.status === 404 && api404.type.includes("json"), api404.status);
    const docs = await get(`${ORIGIN}/api/openapi.json`);
    check("API title says Wish Labs", docs.status === 200 && JSON.parse(docs.body).info.title === "Wish Labs", docs.status);
    // every built file of the SPA: no "Gadget Studio" string waiting in a screen this walk does not open
    const hits = [];
    const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
      const f = path.join(d, e.name);
      if (e.isDirectory()) return walk(f);
      if (/\.(js|html|css|svg|json|txt|webmanifest)$/.test(e.name) && /gadget[\s_-]*studio/i.test(fs.readFileSync(f, "utf8"))) hits.push(path.relative(DIST, f));
    });
    walk(DIST);
    check('no "Gadget Studio" in any built SPA file (dist/)', hits.length === 0, hits.join(", "));
  }

  // ------------------------------------------------------------------ 1. desktop: landing -> wish -> sign-in
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const page = await ctx.newPage();
  watch(page, "desktop");
  await page.goto(`${ORIGIN}/`, { waitUntil: "load" });
  await page.waitForTimeout(2500);
  check("landing title", (await page.title()).includes("Wish Labs"), await page.title());
  await brand(page, "landing", { studio: false });
  await shot(page, "01-desktop-landing");
  await page.click("#wish-input");
  await page.fill("#wish-input", WISH);
  check("landing links carry the typed wish", (await page.getAttribute("header [data-studio-link]", "href") || "").startsWith("/studio/?wish="),
    await page.getAttribute("header [data-studio-link]", "href"));
  await shot(page, "02-desktop-landing-typed");
  await Promise.all([page.waitForURL(/\/studio\/login/, { timeout: 20000 }), page.click(".wish-go")]);
  await page.waitForSelector(".login-card");
  await page.waitForTimeout(500);
  check("Make a wish -> /studio/login (signed out)", new URL(page.url()).pathname === "/studio/login", page.url());
  const loginText = await page.textContent(".login-card");
  check("login copy: invitation / early access", loginText.includes("Access is by invitation while Wish Labs is in early access."));
  check("login says the wish is saved", loginText.includes("Your wish is saved") && loginText.includes("cloud"), loginText.slice(0, 200));
  check("login tab title", (await page.title()) === "Wish Labs", await page.title());
  await brand(page, "login");
  await shot(page, "03-desktop-login");

  // ------------------------------------------------------------------ 2. sign in -> composer pre-filled, nothing sent
  await page.fill("#key", KEY);
  await page.click(".login-card button.btn.primary");
  await page.waitForSelector(".home-composer textarea");
  await page.waitForTimeout(700);
  const value = await page.inputValue(".home-composer textarea");
  check("composer pre-filled with the wish after sign-in", value === WISH, JSON.stringify(value));
  check("home is /studio/ and the wish left the address bar", ["/studio", "/studio/"].includes(new URL(page.url()).pathname) && !page.url().includes("wish="), page.url());
  const before = await page.evaluate(() => fetch("/api/projects").then((r) => r.json()));
  check("nothing auto-submitted", before.projects.length === 0, `${before.projects.length} projects`);
  await brand(page, "home");
  await shot(page, "04-desktop-home-prefilled");

  // ------------------------------------------------------------------ 3. send it -> the scripted agent replies
  await Promise.all([page.waitForURL(/\/studio\/p\/p_/, { timeout: 20000 }), page.click(".home-composer .send-btn")]);
  const pid = new URL(page.url()).pathname.split("/")[3];
  check("send opens /studio/p/<id>", /^p_/.test(pid), page.url());
  await page.waitForSelector(".msg.assistant", { timeout: 60000 });
  await page.waitForSelector(".dock .q", { timeout: 60000 });
  await page.waitForTimeout(800);
  const userMsg = await page.textContent(".msg.user");
  check("the wish is the first message", (userMsg || "").includes(WISH), (userMsg || "").slice(0, 120));
  const reply = await page.$$eval(".msg.assistant", (els) => els.map((e) => e.innerText).join(" / "));
  check("the scripted agent replies (questions shown)", reply.length > 10 && (await page.$$(".dock .q")).length > 0, reply.slice(0, 160));
  await brand(page, "project chat");
  await shot(page, "05-desktop-agent-reply");

  // ------------------------------------------------------------------ 4. deep links: reload, fresh tab, signed out
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector(".msg.assistant", { timeout: 20000 });
  check("reload of /studio/p/<id> keeps the project", new URL(page.url()).pathname === `/studio/p/${pid}` && (await page.textContent(".msg.user")).includes(WISH), page.url());
  await shot(page, "06-desktop-deeplink-reload");
  const fresh = await ctx.newPage();
  watch(fresh, "desktop-fresh-tab");
  await fresh.goto(`${ORIGIN}/studio/p/${pid}`, { waitUntil: "load" });
  await fresh.waitForSelector(".msg.assistant", { timeout: 20000 });
  check("deep link opened in a new tab", new URL(fresh.url()).pathname === `/studio/p/${pid}`, fresh.url());
  await fresh.close();
  const anon = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const ap = await anon.newPage();
  watch(ap, "desktop-signed-out");
  await ap.goto(`${ORIGIN}/studio/p/${pid}`, { waitUntil: "load" });
  await ap.waitForSelector(".login-card", { timeout: 20000 });
  check("signed-out deep link -> /studio/login", new URL(ap.url()).pathname === "/studio/login", ap.url());
  await ap.fill("#key", KEY);
  await ap.click(".login-card button.btn.primary");
  await ap.waitForURL(new RegExp(`/studio/p/${pid}$`), { timeout: 20000 });
  await ap.waitForSelector(".msg.assistant", { timeout: 20000 });
  check("after sign-in the deep link opens the project", new URL(ap.url()).pathname === `/studio/p/${pid}`, ap.url());
  await anon.close();

  // ------------------------------------------------------------------ 5. through the brief to a built version
  await clarifyConceptsBrief(page);
  await shot(page, "07-desktop-brief");
  await page.$eval(".brief-scroll", (el) => (el.scrollTop = el.scrollHeight)).catch(() => {});
  await page.waitForTimeout(400);
  await page.click(".brief-dock .btn.primary");
  await page.waitForSelector(".msg.result", { timeout: 180000 });
  await page.waitForSelector('.viewer[data-ready="1"]', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(2500);
  const resultText = await page.textContent(".msg.result");
  check("a fake build finishes with a result summary", (resultText || "").length > 20, (resultText || "").slice(0, 160));
  await brand(page, "project result");
  await shot(page, "08-desktop-result");
  await page.click('.ws-header .btn:has-text("Project brief")');
  await page.waitForTimeout(700);
  await brand(page, "brief sheet");
  await shot(page, "09-desktop-brief-sheet");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  // the tab deep link survives a reload too
  const tabBtn = await page.$('.panel-tabs button:has-text("Parts"), [role="tab"]:has-text("Parts")');
  if (tabBtn) {
    await tabBtn.click();
    await page.waitForTimeout(500);
    const tabUrl = new URL(page.url()).pathname;
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector(".ws-header", { timeout: 20000 });
    check("reload of a tab deep link /studio/p/<id>/<tab>", new URL(page.url()).pathname === tabUrl && tabUrl.startsWith(`/studio/p/${pid}/`), tabUrl);
  } else check("reload of a tab deep link /studio/p/<id>/<tab>", false, "no Parts tab button found");

  // ------------------------------------------------------------------ 6. share from the UI -> opens signed out
  await page.click('.ws-header .btn:has-text("Share")');
  await page.waitForSelector(".dialog, [role=dialog]");
  const create = await page.$('[role=dialog] .btn.primary, .dialog .btn.primary');
  if (create && !(await page.$('input[aria-label="Share link"]'))) await create.click();
  await page.waitForSelector('input[aria-label="Share link"]', { timeout: 15000 });
  const shareLink = await page.inputValue('input[aria-label="Share link"]');
  const token = shareLink.split("/").pop();
  check("share dialog link is https://<host>/studio/s/<token>", shareLink === `${ORIGIN}/studio/s/${token}` && token.length > 8, shareLink);
  await shot(page, "10-desktop-share-dialog");
  await page.keyboard.press("Escape");
  const viewer = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const vp = await viewer.newPage();
  watch(vp, "desktop-share-viewer");
  await vp.goto(shareLink, { waitUntil: "load" });
  await vp.waitForSelector(".ws-header", { timeout: 20000 });
  await vp.waitForTimeout(2500);
  check("share link renders signed out, read-only", new URL(vp.url()).pathname === `/studio/s/${token}` && (await vp.textContent("body")).includes("read-only"), vp.url());
  await brand(vp, "share view");
  await shot(vp, "11-desktop-share-signed-out");
  await Promise.all([vp.waitForURL(`${ORIGIN}/`, { timeout: 20000 }), vp.click(".ws-header .ws-brand")]);
  await vp.waitForSelector("#wish-input", { timeout: 20000 });
  check("share view: the Wish Labs mark leads to the landing page", new URL(vp.url()).pathname === "/", vp.url());
  await viewer.close();

  // ------------------------------------------------------------------ 7. old run.app links redirect into /studio
  for (const [from, to] of [["/", "/studio/"], ["/login", "/studio/login"], [`/p/${pid}`, `/studio/p/${pid}`],
    [`/p/${pid}/parts`, `/studio/p/${pid}/parts`], [`/s/${token}`, `/studio/s/${token}`], ["/?wish=hi%20there", "/studio/?wish=hi%20there"]]) {
    const r = await get(`${BACKEND}${from}`);
    check(`run.app ${from} -> 307 ${to}`, r.status === 307 && r.location === to, `${r.status} ${r.location}`);
  }
  const old = await ctx.newPage();
  watch(old, "desktop-old-link");
  await old.goto(`${BACKEND}/p/${pid}`, { waitUntil: "load" });
  await old.waitForSelector(".ws-header", { timeout: 20000 });
  check("old /p/<id> link opens the project in the browser", new URL(old.url()).pathname === `/studio/p/${pid}`, old.url());
  await old.goto(`${BACKEND}/login`, { waitUntil: "load" });
  await old.waitForURL(/\/studio\/(login)?$/, { timeout: 20000 });
  check("old /login link lands in the studio (/studio/login, or home when signed in)", /\/studio\/(login)?$/.test(new URL(old.url()).pathname), old.url());
  await old.close();
  const old2 = await browser.newContext();
  const op = await old2.newPage();
  watch(op, "signed-out-old-link");
  await op.goto(`${BACKEND}/login`, { waitUntil: "load" });
  await op.waitForSelector(".login-card", { timeout: 20000 });
  check("old /login (signed out) shows /studio/login", new URL(op.url()).pathname === "/studio/login", op.url());
  await shot(op, "12-old-login-redirected");
  await old2.close();

  // ------------------------------------------------------------------ 8. phone 390x844
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36" });
  const pp = await phone.newPage();
  watch(pp, "phone");
  await pp.goto(`${ORIGIN}/`, { waitUntil: "load" });
  await pp.waitForTimeout(2500);
  await noOverflow(pp, "phone landing");
  await shot(pp, "20-phone-landing");
  await pp.waitForTimeout(6000); // the hero model assembles itself; give the software renderer time to finish
  await shot(pp, "20b-phone-landing-settled");
  await pp.tap("#wish-input");
  await pp.fill("#wish-input", PHONE_WISH);
  await pp.waitForTimeout(300);
  await Promise.all([pp.waitForURL(/\/studio\/login/, { timeout: 20000 }), pp.tap(".wish-go")]);
  await pp.waitForSelector(".login-card");
  await pp.waitForTimeout(500);
  check("phone: Make a wish -> /studio/login with the wish saved", (await pp.textContent(".login-card")).includes("seashell"));
  await noOverflow(pp, "phone login");
  await brand(pp, "phone login");
  await shot(pp, "21-phone-login");
  await pp.fill("#key", KEY);
  await pp.tap(".login-card button.btn.primary");
  await pp.waitForSelector(".home-composer textarea");
  await pp.waitForTimeout(700);
  check("phone: composer pre-filled", (await pp.inputValue(".home-composer textarea")) === PHONE_WISH);
  await noOverflow(pp, "phone home");
  await brand(pp, "phone home");
  await shot(pp, "22-phone-home-prefilled");
  await Promise.all([pp.waitForURL(/\/studio\/p\/p_/, { timeout: 20000 }), pp.tap(".home-composer .send-btn")]);
  await pp.waitForSelector(".msg.assistant", { timeout: 60000 });
  await pp.waitForSelector(".dock .q", { timeout: 60000 });
  await pp.waitForTimeout(800);
  check("phone: the scripted agent replies", (await pp.$$(".msg.assistant")).length > 0);
  await noOverflow(pp, "phone project");
  await brand(pp, "phone project");
  await shot(pp, "23-phone-agent-reply");
  await pp.goto(shareLink, { waitUntil: "load" });
  await pp.waitForSelector(".ws-header", { timeout: 20000 });
  await pp.waitForTimeout(2500);
  await noOverflow(pp, "phone share view");
  await brand(pp, "phone share view");
  await shot(pp, "24-phone-share-view");
  await phone.close();

  // ------------------------------------------------------------------ console errors and failed requests
  for (const p of problems) console.log("PROBLEM", JSON.stringify(p));
  const consoleErrors = problems.filter((p) => p.kind === "console.error" || p.kind === "pageerror");
  check("0 console errors and page errors", consoleErrors.length === 0, consoleErrors.map((p) => `${p.label}: ${p.text} @ ${p.at}`).join(" | "));
  const net = problems.filter((p) => p.kind !== "console.error" && p.kind !== "pageerror");
  check("0 failed requests / HTTP errors while browsing", net.length === 0, net.map((p) => `${p.label}: ${p.kind} ${p.text}`).join(" | "));
  check('no "Gadget Studio" on any page visited', brandHits.length === 0, brandHits.join(", "));

  await browser.close();
  lb.close();
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ results, problems, shots }, null, 2));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`${results.length - failed}/${results.length} checks passed; screenshots in ${OUT}`);
  process.exit(failed ? 1 : 0);
})().catch(async (e) => {
  console.error("CRASH", e);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ results, problems, shots, crash: String(e) }, null, 2));
  process.exit(2);
});
