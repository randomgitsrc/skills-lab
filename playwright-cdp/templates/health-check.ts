import { chromium } from 'playwright';
import { execSync } from 'child_process';

/**
 * Fast CDP health check with race-timeout.
 * Avoids hanging when CDP WS connects but init protocol stalls (stale state).
 */
export async function checkCDPHealth(): Promise<boolean> {
  try {
    await Promise.race([
      (async () => {
        const browser = await chromium.connectOverCDP('http://127.0.0.1:18800');
        browser.contexts(); // verify browser object functional
        return true;
      })(),
      new Promise<boolean>((_, reject) =>
        setTimeout(() => reject(new Error('health check timeout')), 5000),
      ),
    ]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Restart chrome-agent service and poll until healthy.
 */
export async function recoverCDP(): Promise<void> {
  execSync('systemctl --user restart chrome-agent.service', {
    timeout: 60000,
    stdio: 'pipe',
  });

  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 2000));
    if (await checkCDPHealth()) return;
  }
  throw new Error('CDP recovery failed after 30s');
}

/**
 * Verify CDP is healthy, recovering if necessary.
 * Call at the start of any Playwright script before connectOverCDP.
 */
export async function ensureCDP(): Promise<void> {
  if (await checkCDPHealth()) return;
  console.log('[health-check] CDP unhealthy, recovering...');
  await recoverCDP();
  if (!(await checkCDPHealth())) {
    throw new Error('CDP health check failed');
  }
  console.log('[health-check] CDP healthy');
}
