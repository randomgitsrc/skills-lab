import { chromium } from 'playwright';
import { spawn } from 'child_process';

function minimizeChrome() {
  const ps = spawn('powershell.exe', ['-NoProfile', '-File', '/mnt/c/tmp/minimize-chrome.ps1'], {
    detached: true, stdio: 'ignore',
  });
  ps.unref();
}

const HARD = 90_000;
let lastStep = 'init';

const hardTimer = setTimeout(() => {
  console.error(`HARD TIMEOUT at: ${lastStep}`);
  process.exit(2);
}, HARD);

async function main() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:18800');
  const context = browser.contexts()[0] ?? await browser.newContext();
  const page = await context.newPage();

  try {
    lastStep = 'goto';
    await page.goto(process.argv[2] || 'https://example.com', { timeout: 15000 });

    lastStep = 'scroll';
    await page.mouse.wheel(0, 500);
    await page.waitForTimeout(500);

    lastStep = 'right-click';
    await page.mouse.click(200, 200, { button: 'right' });

    lastStep = 'draw-rectangle';
    await page.mouse.move(100, 100);
    await page.mouse.down();
    await page.mouse.move(300, 100, { steps: 10 });
    await page.mouse.move(300, 300, { steps: 10 });
    await page.mouse.move(100, 300, { steps: 10 });
    await page.mouse.move(100, 100, { steps: 10 });
    await page.mouse.up();

    lastStep = 'drag-and-drop';
    const src = page.locator('#draggable').first();
    const tgt = page.locator('#dropzone').first();
    if (await src.count() > 0 && await tgt.count() > 0) {
      await src.dragTo(tgt, { timeout: 5000 });
    }

    lastStep = 'screenshot';
    await page.screenshot({ path: '/tmp/interaction-result.png', fullPage: true });
    console.log('Done. Screenshot: /tmp/interaction-result.png');
  } finally {
    await page.close();
    minimizeChrome();
  }

  clearTimeout(hardTimer);
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
