// Usage: NODE_PATH=$(npm root -g) node screenshots.js output/viewer.html output name=az=210&el=20&d=2.6 ...
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const [file, outdir, ...views] = process.argv.slice(2);
  const b = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const p = await b.newPage({ viewport: { width: 1400, height: 850 } });
  p.on('console', m => console.log('console:', m.text()));
  for (const v of views) {
    const i = v.indexOf('='); const name = v.slice(0, i), hash = v.slice(i + 1);
    await p.goto('about:blank');
    await p.goto('file://' + path.resolve(file) + '#' + hash);
    await p.waitForFunction(() => window.ready === true, null, { timeout: 60000 });
    await p.waitForTimeout(800);
    await p.evaluate(() => document.querySelector('.card').style.display = 'none');
    await p.screenshot({ path: path.join(outdir, name + '.png') });
    console.log('shot', name);
  }
  await b.close();
})();
