# Troubleshooting Reference

## Chrome not responding on CDP

```bash
chrome-agent status              # is it running?
chrome-agent restart             # kill + start fresh
```

Chrome may take 10-15 seconds to become CDP-ready on first launch. Poll:

```bash
for i in $(seq 1 10); do
  curl -sf --max-time 3 http://127.0.0.1:18800/json/version && break
  sleep 2
done
```

### Stale CDP (HTTP works, Playwright hangs)

Symptom: `curl http://127.0.0.1:18800/json/version` responds but `chromium.connectOverCDP()` times out after WebSocket connects. The Chrome process is dead but the HTTP endpoint responds with cached/stale data (common in WSL2 mirrored networking).

Detection:
```bash
# HTTP alive but process dead = stale
chrome-agent status
# "Chrome running" but (Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Measure-Object).Count returns 0
```

Recovery:
```bash
chrome-agent restart   # now detects stale state and force-clears before restart
```

Pre-flight in scripts:
```typescript
import { ensureCDP } from './health-check.js';
await ensureCDP();  // checks + recovers if needed
```

## `Cannot find module 'playwright'`

```bash
npm install -g playwright
node -e "require('playwright')"
```

Run scripts with: `NODE_PATH=$(npm root -g) npx tsx script.ts`

## Playwright script hangs

1. **Missing timeout on wait functions** — always pass `{ timeout: N }`
2. **Waiting for `networkidle`** — switch to `domcontentloaded`
3. **Tab in background with rAF loop** — bring Chrome to foreground
4. **`page.evaluate` with infinite promise** — add `setTimeout` fallback inside evaluate
5. **`waitForFunction` hangs in CDP mode** — replace with manual polling:

```typescript
const deadline = Date.now() + 10000;
while (Date.now() < deadline) {
  const ready = await page.evaluate(() =>
    (document.querySelector('#root') as HTMLElement)?.childElementCount > 0
  );
  if (ready) break;
  await new Promise(r => setTimeout(r, 500));
}
```

## Tab leak detection

```bash
# Count tabs
curl -sf http://127.0.0.1:18800/json/list | python3 -c "import sys,json; print(len([t for t in json.load(sys.stdin) if t.get('type')=='page']))"

# Clean up
chrome-agent cleanup
```

## Chrome exits unexpectedly

Chrome exits when the last tab closes. If `page.close()` closes the only tab, Chrome dies. Ensure at least one tab (newtab) remains, or use `chrome-agent cleanup`.

Never call `browser.close()` in CDP mode — it kills Chrome. Use `process.exit(0)`.

## Node process won't exit

Node hangs after `page.close()` because the CDP WebSocket keeps the event loop alive. Always call `process.exit(0)`.

## CDP not responding after script exit

Normal — Chrome's CDP may be unresponsive for 5-10s after Playwright disconnects. Wait and retry:

```bash
sleep 5 && curl -sf --max-time 10 http://127.0.0.1:18800/json/version
```

Verify Chrome is alive before assuming crash:

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "(Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count"
```

## File access (WSL ↔ Windows)

`file://` URLs must use Windows paths:

```typescript
// Correct
await page.goto('file:///C:/Users/NMASTER/test.html');

// Wrong — Windows Chrome cannot access WSL path
await page.goto('file:///tmp/test.html');
```

Bridge pattern:

```typescript
import { writeFileSync } from 'fs';
const winPath = '/mnt/c/Users/NMASTER/tmp-test.html';
const fileUrl = 'file:///C:/Users/NMASTER/tmp-test.html';
writeFileSync(winPath, htmlContent);
await page.goto(fileUrl);
```

## Security

- Single-user dev machines only — CDP has no authentication
- Any process reaching `localhost:18800` can fully control Chrome
- Ensure Windows firewall blocks external access to port 18800
- Avoid loading sensitive files (`.env`, credentials, SSH keys) into browser context
- Chrome uses dedicated profile (`chrome-agent-profile`) — never point at daily browsing profile
