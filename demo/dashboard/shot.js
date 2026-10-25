// Screenshot the dashboard with the pre-installed Playwright/Chromium.
//   python demo/dashboard/generate.py   # writes index.html
//   node   demo/dashboard/shot.js        # -> board.png, inbox.png, analytics.png
const { chromium } = require('playwright');
const path = require('path');
const DIR = __dirname;
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto('file://' + path.join(DIR, 'index.html'), { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  for (const v of ['board', 'inbox', 'analytics']) {
    await page.evaluate((view) => window.setView(view), v);
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(DIR, v + '.png'), fullPage: true });
  }
  await browser.close();
  console.log('screenshots written to', DIR);
})().catch(e => { console.error(String(e)); process.exit(1); });
