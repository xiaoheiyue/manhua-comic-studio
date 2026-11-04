const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Users/Mukuro/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', headless: true });
  const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 } });
  await ctx.request.post('http://127.0.0.1:7870/api/project/load', { data: { file: 'E:/manhua/data/projects/558f643800.json' } });
  const p = await ctx.newPage();
  const errors=[]; p.on('pageerror', e=>errors.push(e.message));
  await p.goto('http://127.0.0.1:7870/', { waitUntil:'domcontentloaded' });
  await p.waitForTimeout(7500);
  const s = await p.evaluate(() => ({
    m: document.querySelector('script[src*="app.js"]').src,
    id: S.project?.id,
    overview: !!document.getElementById('project-overview')?.innerText,
    anchors: document.querySelectorAll('#refine-anchor-cards .refine-anchor-card').length,
    cells: document.querySelectorAll('#refine-cell-grid .refine-cell-card').length,
    gridFallback: document.querySelectorAll('#grid25-box .grid25-cell').length,
    pose: document.querySelectorAll('#pose-grid .shot-card').length,
  }));
  console.log(s, 'errors', errors);
  await p.click('.nav-item[data-stage="projects"]');
  await p.waitForTimeout(900);
  console.log('stage cells', await p.evaluate(() => document.querySelectorAll('#project-list tbody tr').length));
  await b.close();
})();
