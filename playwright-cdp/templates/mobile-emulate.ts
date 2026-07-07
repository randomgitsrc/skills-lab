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
    const cdp = await context.newCDPSession(page);

    // iPhone 14
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 3,
      mobile: true,
      screenWidth: 390,
      screenHeight: 844,
    });
    await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true });

    lastStep = 'goto';
    await page.goto('https://example.com', { timeout: 15000 });

    lastStep = 'screenshot';
    await page.screenshot({ path: '/tmp/mobile-screenshot.png' });

    // Restore desktop
    await cdp.send('Emulation.clearDeviceMetricsOverride');
  } finally {
    await page.close();
    minimizeChrome();
  }

  clearTimeout(hardTimer);
  // setDeviceMetricsOverride does NOT change User-Agent. Set via CDP Network.setUserAgentOverride if needed.
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
