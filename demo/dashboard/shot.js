// Screenshot the dashboard with the pre-installed Playwright/Chromium.
//   node demo/dashboard/shot.js    (run `python demo/dashboard/generate.py` first)
const { chromium } = require('playwright');
const path = require('path');
const DIR = __dirname;
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1324, height: 1200 }, deviceScaleFactor: 2 });
  await page.goto('file://' + path.join(DIR, 'index.html'), { waitUntil: 'load' });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(DIR, 'dashboard_full.png'), fullPage: true });
  const s = await page.$$('.section');
  await s[0].screenshot({ path: path.join(DIR, 'board.png') });
  await s[1].screenshot({ path: path.join(DIR, 'inbox.png') });
  await s[2].screenshot({ path: path.join(DIR, 'analytics.png') });
  await browser.close();
  console.log('screenshots written to', DIR);
})().catch(e => { console.error(String(e)); process.exit(1); });
