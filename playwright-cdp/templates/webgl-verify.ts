import { chromium } from 'playwright';
import { spawn } from 'child_process';

function minimizeChrome() {
  const ps = spawn('powershell.exe', ['-NoProfile', '-File', '/mnt/c/tmp/minimize-chrome.ps1'], {
    detached: true, stdio: 'ignore',
  });
  ps.unref();
}

const HARD = 60_000;
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

    lastStep = 'webgl-check';
    const glInfo = await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl');
      if (!gl) return { ok: false };
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return {
        ok: true,
        version: gl.getParameter(gl.VERSION),
        renderer: gl.getParameter(gl.RENDERER),
        unmasked: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
      };
    });

    console.log(JSON.stringify(glInfo, null, 2));
  } finally {
    await page.close();
    minimizeChrome();
  }

  clearTimeout(hardTimer);
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
