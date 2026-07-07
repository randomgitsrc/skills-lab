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

  const violations: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error' && msg.text().includes('Content Security Policy')) {
      violations.push(msg.text());
    }
  });

  try {
    lastStep = 'goto';
    await page.goto(process.argv[2] || 'https://example.com', { timeout: 15000 });

    lastStep = 'evaluate';
    await page.waitForLoadState('domcontentloaded', { timeout: 10000 });

    console.log(`CSP violations: ${violations.length}`);
    violations.forEach((v, i) => console.log(`  ${i + 1}. ${v}`));
  } finally {
    await page.close();
    minimizeChrome();
  }

  clearTimeout(hardTimer);
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
