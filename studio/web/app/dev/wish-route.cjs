// End-to-end check of "the platform on the main site" (run on the VM, never on the laptop):
// a local stand-in for the wish.plugbook.si load balancer sends /studio, /studio/* and /api/* to gstudio-web and
// everything else to the landing page (studio/site), then a browser walks landing -> wish -> sign-in -> composer.
//   web/app/dev/serve.sh 8097            # backend serving dist/ (ACCESS_KEY=dev)
//   PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/wish-route.cjs [backendPort] [outDir]
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const fs = require("fs");
const http = require("http");
const path = require("path");

const BACK = Number(process.argv[2] || 8097);
const OUT = process.argv[3] || path.join(__dirname, "..", "_out", "wish-route");
const SITE = path.resolve(__dirname, "..", "..", "..", "site");
const PORT = 8099;
const ORIGIN = `http://127.0.0.1:${PORT}`;
fs.mkdirSync(OUT, { recursive: true });

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
  ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".glb": "model/gltf-binary", ".json": "application/json",
  ".woff2": "font/woff2", ".mp4": "video/mp4" };
const toStudio = (p) => p === "/studio" || p.startsWith("/studio/") || p.startsWith("/api/");

// The load balancer stand-in (same path rules as deploy/wish-studio-routes.sh).
const lb = http.createServer((req, res) => {
  const p = new URL(req.url, ORIGIN).pathname;
  if (toStudio(p)) {
    const up = http.request({ host: "127.0.0.1", port: BACK, path: req.url, method: req.method,
      headers: { ...req.headers, host: `127.0.0.1:${PORT}` } }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    req.pipe(up);
    return;
  }
  // nginx try_files $uri $uri/ /index.html
  let f = path.join(SITE, decodeURIComponent(p));
  if (!f.startsWith(SITE) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(SITE, "index.html");
  res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" });
  fs.createReadStream(f).pipe(res);
});

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail }); console.log(ok ? "PASS" : "FAIL", name, detail); };
const WISH = "a lamp shaped like a cloud that glows blue the evening before it rains";

(async () => {
  await new Promise((r) => lb.listen(PORT, "127.0.0.1", r));
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  const bad = [];
  const watch = (page) => {
    page.on("response", (r) => { if (r.status() >= 400 && r.url().includes("/studio/assets/")) bad.push(`${r.status()} ${r.url()}`); });
    page.on("pageerror", (e) => bad.push(`pageerror ${e.message}`));
  };

  // 1. landing -> typed wish -> studio sign-in -> composer pre-filled, nothing sent
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const page = await ctx.newPage();
  watch(page);
  await page.goto(`${ORIGIN}/`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  check("landing at /", (await page.title()).includes("Wish Labs"), await page.title());
  check("header link points at /studio/", (await page.getAttribute("header .btn-ink[data-studio-link]", "href")) === "/studio/");
  await page.fill("#wish-input", WISH);
  const synced = await page.getAttribute("header .btn-ink[data-studio-link]", "href");
  check("links carry the typed wish", synced.startsWith("/studio/?wish="), synced);
  await page.screenshot({ path: path.join(OUT, "1-landing-typed.png") });
  await Promise.all([page.waitForURL(/\/studio\/login/, { timeout: 20000 }), page.click(".wish-go")]);
  await page.waitForSelector(".login-card");
  check("signed-out visitor lands on /studio/login", new URL(page.url()).pathname === "/studio/login", page.url());
  const loginText = await page.textContent(".login-card");
  check("login copy", loginText.includes("Access is by invitation while Wish Labs is in early access."));
  check("login shows the saved wish", loginText.includes("Your wish is saved") && loginText.includes("cloud"));
  check("login logo says Wish Labs", (await page.textContent(".login-card .logo")) === "Wish Labs" || loginText.includes("Wish Labs"));
  await page.screenshot({ path: path.join(OUT, "2-login.png") });
  await page.fill("#key", "dev");
  await page.click(".login-card button");
  await page.waitForSelector(".home-composer textarea");
  await page.waitForTimeout(600);
  const value = await page.inputValue(".home-composer textarea");
  check("composer pre-filled with the wish", value === WISH, JSON.stringify(value));
  check("wish removed from the address bar", !page.url().includes("wish="), page.url());
  check("home is /studio", ["/studio", "/studio/"].includes(new URL(page.url()).pathname), page.url());
  const projects = await page.evaluate(() => fetch("/api/projects").then((r) => r.json()));
  check("nothing auto-submitted", projects.projects.length === 0, `${projects.projects.length} projects`);
  check("tab title", (await page.title()) === "Wish Labs", await page.title());
  check("home logo", (await page.textContent(".home-top .logo")).includes("Wish Labs"));
  await page.screenshot({ path: path.join(OUT, "3-home-prefilled.png") });

  // 2. a 600-character wish is capped at 500
  await page.goto(`${ORIGIN}/studio/?wish=${encodeURIComponent("x".repeat(600))}`);
  await page.waitForSelector(".home-composer textarea");
  await page.waitForTimeout(500);
  check("wish capped at 500", (await page.inputValue(".home-composer textarea")).length === 500);

  // 3. start a project from the composer: in-app navigation stays under /studio
  await page.fill(".home-composer textarea", "A small desk lamp that glows when I get an email.");
  await Promise.all([page.waitForURL(/\/studio\/p\/p_/, { timeout: 20000 }), page.click(".send-btn")]);
  const pid = new URL(page.url()).pathname.split("/")[3];
  check("new project opens at /studio/p/<id>", /^p_/.test(pid), page.url());
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, "4-project.png") });
  const share = await page.evaluate((id) => fetch(`/api/projects/${id}/share`, { method: "POST" }).then((r) => r.json()), pid);
  check("share URL is /studio/s/<token>", share.url === `/studio/s/${share.token}`, share.url);
  await page.click('a[aria-label="Back to home"]');
  await page.waitForSelector(".home-composer");
  check("back to home stays under /studio", ["/studio", "/studio/"].includes(new URL(page.url()).pathname), page.url());
  await page.reload();
  await page.waitForSelector(".home-composer");
  check("reload of /studio is the home page", ["/studio", "/studio/"].includes(new URL(page.url()).pathname), page.url());
  await page.click(".proj-link");
  await page.waitForURL(/\/studio\/p\/p_/);
  check("project card link", new URL(page.url()).pathname === `/studio/p/${pid}`, page.url());

  // 4. a share link opens signed out
  const anon = await browser.newContext({ viewport: { width: 1360, height: 860 } });
  const ap = await anon.newPage();
  watch(ap);
  await ap.goto(`${ORIGIN}${share.url}`);
  await ap.waitForSelector(".ws-header", { timeout: 20000 });
  check("share link renders signed out", new URL(ap.url()).pathname === share.url, ap.url());
  await ap.screenshot({ path: path.join(OUT, "5-share.png") });

  // 5. the empty-box button sends the example being typed
  await ap.goto(`${ORIGIN}/`, { waitUntil: "domcontentloaded" });
  await ap.waitForTimeout(1500);
  await Promise.all([ap.waitForURL(/\/studio\/login/, { timeout: 20000 }), ap.click(".wish-go")]);
  await ap.fill("#key", "dev");
  await ap.click(".login-card button");
  await ap.waitForSelector(".home-composer textarea");
  await ap.waitForTimeout(600);
  const ex = await ap.inputValue(".home-composer textarea");
  check("empty box sends the typed example", ex.startsWith("I wish for "), JSON.stringify(ex));

  // 6. old run.app routes redirect into /studio (straight to the backend, as on the run.app URL)
  for (const [from, to] of [["/", "/studio/"], ["/login", "/studio/login"], [`/p/${pid}`, `/studio/p/${pid}`], [`/s/${share.token}`, share.url]]) {
    const r = await fetch(`http://127.0.0.1:${BACK}${from}`, { redirect: "manual" });
    check(`backend ${from} -> ${to}`, r.status === 307 && r.headers.get("location") === to, `${r.status} ${r.headers.get("location")}`);
  }
  const health = await fetch(`${ORIGIN}/api/health`).then((r) => r.json());
  check("/api/health through the LB stand-in", health.ok === true);
  check("no failed /studio/assets requests or page errors", bad.length === 0, bad.join(" | "));

  await browser.close();
  lb.close();
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
