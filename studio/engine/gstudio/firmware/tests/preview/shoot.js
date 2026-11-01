// Screenshots of the parent page against mock_device.py, at phone width, light and dark.
// node shoot.js <out_dir> <port> <ready|setup>   (PLAYWRIGHT_BROWSERS_PATH set; mock running on that port)
const { chromium } = require("/opt/gs/pw/node_modules/playwright");
const [out, port, mode] = [process.argv[2] || ".", process.argv[3] || "8771", process.argv[4] || "ready"];
(async () => {
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: scheme });
    const page = await ctx.newPage();
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.waitForTimeout(600);
    if (mode === "setup") {
      await page.screenshot({ path: `${out}/parent-setup-${scheme}.png`, fullPage: true });
    } else {
      await page.fill("#pin", "246813");
      await page.click("#go");
      await page.waitForTimeout(700);
      await page.screenshot({ path: `${out}/parent-main-${scheme}.png`, fullPage: true });
    }
    await ctx.close();
  }
  await browser.close();
})();
