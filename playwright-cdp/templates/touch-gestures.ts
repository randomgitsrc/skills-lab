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

  const cdp = await context.newCDPSession(page);

  try {
    // iPhone 14
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 390, height: 844, deviceScaleFactor: 3,
      mobile: true, screenWidth: 390, screenHeight: 844,
    });
    await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true });

    lastStep = 'goto';
    await page.goto(process.argv[2] || 'https://example.com', { timeout: 15000 });

    lastStep = 'tap';
    await page.touchscreen.tap(195, 400);

    lastStep = 'swipe-up';
    const centerX = 195;
    const startY = 600;
    const endY = 200;
    await page.evaluate(
      ({ sx, sy, ex }) => {
        const el = document.elementFromPoint(sx, sy);
        if (!el) return;
        const touch1 = new Touch({ identifier: 1, target: el, clientX: sx, clientY: sy });
        el.dispatchEvent(new TouchEvent('touchstart', { touches: [touch1], changedTouches: [touch1], targetTouches: [touch1], bubbles: true }));
        const touch1m = new Touch({ identifier: 1, target: el, clientX: sx, clientY: ex });
        el.dispatchEvent(new TouchEvent('touchmove', { touches: [touch1m], changedTouches: [touch1m], targetTouches: [touch1m], bubbles: true }));
        el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [touch1m], targetTouches: [], bubbles: true }));
      },
      { sx: centerX, sy: startY, ex: endY }
    );

    lastStep = 'pinch-zoom';
    await page.evaluate(() => {
      const el = document.elementFromPoint(195, 400);
      if (!el) return;
      const t1 = new Touch({ identifier: 1, target: el, clientX: 150, clientY: 400 });
      const t2 = new Touch({ identifier: 2, target: el, clientX: 240, clientY: 400 });
      el.dispatchEvent(new TouchEvent('touchstart', { touches: [t1, t2], changedTouches: [t1, t2], targetTouches: [t1, t2], bubbles: true }));
      const t1m = new Touch({ identifier: 1, target: el, clientX: 120, clientY: 400 });
      const t2m = new Touch({ identifier: 2, target: el, clientX: 270, clientY: 400 });
      el.dispatchEvent(new TouchEvent('touchmove', { touches: [t1m, t2m], changedTouches: [t1m, t2m], targetTouches: [t1m, t2m], bubbles: true }));
      el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t1m, t2m], targetTouches: [], bubbles: true }));
    });

    lastStep = 'screenshot';
    await page.screenshot({ path: '/tmp/touch-result.png' });
    console.log('Done. Screenshot: /tmp/touch-result.png');

    await cdp.send('Emulation.clearDeviceMetricsOverride');
  } finally {
    await page.close();
    minimizeChrome();
  }

  clearTimeout(hardTimer);
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
