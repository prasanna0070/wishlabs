// Screenshots of the parent page against mock_device.py, at phone width, light and dark.
// node shoot.js <out_dir>   (needs the mock on 8771 = ready and 8772 = setup; PLAYWRIGHT_BROWSERS_PATH set)
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const out = process.argv[2] || ".";
(async () => {
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: scheme });
    const page = await ctx.newPage();
    await page.goto("http://127.0.0.1:8772/");
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${out}/parent-setup-${scheme}.png`, fullPage: true });
    await page.goto("http://127.0.0.1:8771/");
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${out}/parent-login-${scheme}.png` });
    await page.fill("#pin", "2468");
    await page.click("#go");
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${out}/parent-main-${scheme}.png`, fullPage: true });
    await ctx.close();
  }
  await browser.close();
})();
