---
name: playwright-cdp
description: "Use when a task requires Playwright for browser automation, screenshots, visual testing, or WebGL verification. Use ONLY for real browser tasks."
---

# Playwright CDP

Browser automation via Playwright connected to local Chrome CDP (port 18800). Chrome on Windows with GPU; WSL connects through mirrored networking.

## When to Use / Not Use

**Use:** navigate/click/fill forms, screenshot, verify WebGL/CSP/DOM, mobile emulation, mouse/keyboard/touch interactions, any real browser engine task.

**Not use:** static HTML parsing (→ Read/Grep), HTTP requests (→ webfetch/curl), headless scraping without visuals, image analysis (→ vision-analyzer skill).

## 铁律

这些不做，脚本必崩。不管场景多简单，缺一条就停。

- **`process.exit(0)`** — 脚本末尾必须有。Node 在 CDP WebSocket 不断开时不会自动退出。
- **`try/finally { page.close() }`** — 漏关的 tab 累积到 Chrome 无 tab 时进程会死。
- **永远不用 `browser.close()`** — CDP 模式下会杀掉 Chrome 本身。用 `process.exit(0)` 替代。
- **硬超时 + `lastStep`** — `setTimeout` 必须在脚本最顶部设，`lastStep` 每次操作前更新。
- **`chromium.connectOverCDP()`** — 不用 `chromium.launch()`（本机无浏览器二进制）。
- **`browser.contexts()[0] ?? browser.newContext()`** — 必须处理无已有 context 的情况。

### Red Flags

写了这些说明你在走捷径，停手：

- "就一个简单操作，不用 `process.exit` 吧？"
- "我手动测过没问题，不写 `try/finally` 了"
- "这次不用硬超时"

### 最小脚本

```ts
import { chromium } from 'playwright';
const HARD = 90_000; let lastStep = 'init'; const hardTimer = setTimeout(() => { console.error(`HARD TIMEOUT at ${lastStep}`); process.exit(2); }, HARD);
async function main() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:18800');
  const ctx = browser.contexts()[0] ?? await browser.newContext();
  const page = await ctx.newPage();
  try { lastStep = 'goto'; await page.goto('URL', { timeout: 15000 }); /* your code */ }
  finally { await page.close(); }
  clearTimeout(hardTimer); process.exit(0);
}
main().catch(e => { console.error(e); process.exit(1); });
```

需 Playwright ≥ 1.49。运行：`NODE_PATH=$(npm root -g) npx tsx script.ts`

## Quick Reference

### Connection & Navigation

| Task | Pattern |
|------|---------|
| Connect | `chromium.connectOverCDP('http://127.0.0.1:18800')` |
| Context | `browser.contexts()[0] ?? browser.newContext()` |
| Viewport | CDP 模式下默认为 `null`（=窗口实际大小），如需固定用 `page.setViewportSize({ width: 1280, height: 720 })` |
| Navigate | `page.goto(url, { timeout: 15000 })` |
| Wait element | `page.waitForSelector(sel, { timeout: 10000 })` |
| Screenshot | `page.screenshot({ path, fullPage: true })` |
| Mobile | CDP `Emulation.setDeviceMetricsOverride` (not `newContext`) |
| Screenshot analysis | Screenshot → use vision-analyzer skill |
| Chrome status | `curl -sf http://127.0.0.1:18800/json/version` |
| Start Chrome | `chrome-agent start` |
| Cleanup tabs | `chrome-agent cleanup` |
| Pre-flight | `import { ensureCDP } from './health-check.js'; await ensureCDP()` — 连接前检查+自动恢复 |

### Click & Scroll (高频)

| Task | Pattern |
|------|---------|
| Left click | `locator.click()` |
| Right click | `locator.click({ button: 'right' })` |
| Double click | `locator.dblclick()` |
| Hover | `locator.hover()` |
| Click at offset | `locator.click({ position: { x: 10, y: 20 } })` |
| Scroll | `page.mouse.wheel(0, 300)` or `page.evaluate(() => window.scrollBy(0, 300))` |
| Scroll into view | `locator.scrollIntoViewIfNeeded()` |
| Scroll to bottom | `page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))` |
| Coordinate click | `page.mouse.click(x, y)` |

### Keyboard & Form (高频)

| Task | Pattern |
|------|---------|
| Type into field | `locator.fill('text')` (preferred, clears first) |
| Type char-by-char | `locator.pressSequentially('Hello', { delay: 100 })` |
| Press key combo | `locator.press('Control+S')` or `page.keyboard.press('Control+Shift+T')` |
| IME/CJK input | `page.keyboard.insertText('中文')` |
| Clear field | `locator.clear()` |
| Select dropdown | `locator.selectOption('blue')` |
| Check/uncheck | `locator.check()` / `locator.uncheck()` |
| Upload file | `locator.setInputFiles('path/to/file')` |

### 滚动不工作？

执行 scroll 后 `page.evaluate(() => window.scrollY)` 返回 0？页面可能用了自定义滚动容器。

1. **找 scrollable 元素**——挑出 `scrollHeight > clientHeight` 的：
   ```ts
   page.evaluate(() => Array.from(document.querySelectorAll('*'))
     .filter(e => e.scrollHeight > e.clientHeight)
     .map(e => e.tagName + (e.className ? '.' + e.className.slice(0,50) : '')))
   ```
2. **操作它**——对容器设 `scrollTop`：
   ```ts
   page.evaluate(y => document.querySelector('.body-wrapper').scrollTop = y, 720)
   ```
3. **检查 overflow**——`getComputedStyle(el).overflowY` 是否为 `hidden` / `auto` / `scroll`

### Low-frequency (少用)

<details>
<summary>Mouse: Middle/Shift+click, Freeform draw</summary>

| Task | Pattern |
|------|---------|
| Middle click | `locator.click({ button: 'middle' })` |
| Shift/Ctrl+click | `locator.click({ modifiers: ['Shift'] })` |
| Freeform draw | `page.mouse.down()` → `page.mouse.move(x, y, { steps })` → `page.mouse.up()` |

</details>

<details>
<summary>Drag & Drop</summary>

| Task | Pattern |
|------|---------|
| Element to element | `sourceLocator.dragTo(targetLocator)` |
| Page-level | `page.dragAndDrop('#src', '#tgt')` |
| Drop files (v1.60) | `dropLocator.drop({ files: { name, mimeType, buffer } })` |
| Manual drag | `srcLocator.hover()` → `page.mouse.down()` → `page.mouse.move()` → `page.mouse.up()` |

</details>

<details>
<summary>Keyboard: Hold/Release, Focus/Blur</summary>

| Task | Pattern |
|------|---------|
| Hold & release | `page.keyboard.down('Shift')` ... `page.keyboard.up('Shift')` |
| Focus/blur | `locator.focus()` / `locator.blur()` |

</details>

<details>
<summary>Touch (requires hasTouch or CDP mobile emulation)</summary>

| Task | Pattern |
|------|---------|
| Tap | `page.touchscreen.tap(x, y)` or `locator.tap()` |
| Multi-touch/pinch | Manual: `dispatchEvent('touchstart', { touches: [t1, t2] })` → `touchmove` → `touchend` |
| Swipe | `touchstart` → `touchmove` (updated coords) → `touchend` |

</details>

<details>
<summary>Form & File: Upload buffer, File chooser</summary>

| Task | Pattern |
|------|---------|
| Upload buffer | `locator.setInputFiles({ name: 'f.txt', mimeType: 'text/plain', buffer: Buffer.from('hi') })` |
| File chooser | `const fc = await page.waitForEvent('filechooser'); await fc.setFiles('file.pdf')` |

</details>

## Timeouts

每个 wait 必须加 `{ timeout: N }`，脚本顶部设硬超时 90s + `lastStep` 追踪。

| Operation | Timeout |
|-----------|---------|
| `page.goto()` | 15000ms |
| `waitForSelector` | 10000ms |
| `page.evaluate()` | 5000ms |
| `page.screenshot()` viewport | 10000ms |
| `page.screenshot()` fullPage | 30000ms |

## Window State

| State | rAF FPS | Works For |
|-------|---------|-----------|
| Normal/Maximized | ~165 | Everything |
| Minimized/Background | ~1 | One-shot checks only (DOM, CSP) |

Anti-throttling flags don't help in Chrome 149+. Bring Chrome to foreground for FPS/animation/WebGL tests.

## Troubleshooting

### 脚本挂了 / 不退出

| 现象 | 原因 | 解决 |
|------|------|------|
| 脚本挂死不退出 | 没有 `process.exit(0)` | 末尾加 |
| CDP 无响应 | WebSocket 重连延迟 | 等 5-10s |
| 没超时但无限等 | CDP wait 默认 30s 太长 | 所有 wait 加 `{ timeout: N }` |
| 需要排查执行位置 | 不知道脚本跑到哪了 | 加 `await page.waitForTimeout(1000)` 辅助观察 |

### 导航 / 连接

| 现象 | 原因 | 解决 |
|------|------|------|
| `localhost` 连不上 | WSL2 下 `localhost` 有时不通 | 用 `127.0.0.1` |
| `Cannot find module 'playwright'` | 全局包找不到 | `NODE_PATH=$(npm root -g)` |
| goto 永远 pending | `networkidle` 可能永不触发 | 用 `domcontentloaded` |
| 页面 403 或乱码 | `file://` 用了 WSL 路径 | 用 `file:///C:/Users/...` Windows 路径 |
| 弹窗 alert/confirm 卡住页面 | 未处理 dialog 事件 | `page.on('dialog', d => d.accept())` |

### 滚动

| 现象 | 原因 | 解决 |
|------|------|------|
| scrollY=0 after scroll | 页面用自定义 scroll container | 找 scrollable 元素 → 设 `scrollTop`（见"滚动不工作？"）|
| 误判"页面只有一屏" | 没验证 scrollY 就下结论 | 先执行 scroll 再查 scrollY，变了说明能滚 |

### 窗口

| 现象 | 原因 | 解决 |
|------|------|------|
| Chrome 最小化后截图/动画慢 | rAF 被限到 ~1 FPS | 不需要前台的任务可忽略；需要 FPS 的把窗口还原 |

### 超时

| 现象 | 原因 | 解决 |
|------|------|------|
| 硬超时误杀 | 操作确实慢 | 增加对应 timeout + HARD 值 |
| >1MB HTML 加载慢 | CDP 传输+解析 | goto: 60s, HARD: 180s |
| Three.js 首帧慢 | WebGL init + assets | waitForSelector: 30s |
| fullPage 截图慢 | stitch viewports | screenshot: 30s |
| `file:///` >2MB | Windows filesystem bridge | goto: 60s |
| `waitForFunction` 挂死 | CDP 模式下不稳定 | 用手动 polling loop 替代（见 templates） |

## Templates & Reference

- `templates/basic.ts` — minimal script with hard timeout + lastStep
- `templates/screenshot.ts` — screenshot save + optional vision-analyzer integration
- `templates/interactions.ts` — mouse interactions: scroll, right-click, draw, drag-and-drop
- `templates/touch-gestures.ts` — touch: tap, swipe, pinch-zoom (CDP mobile emulation)
- `templates/mobile-emulate.ts` — CDP mobile device emulation
- `templates/csp-capture.ts` — CSP violation capture
- `templates/webgl-verify.ts` — WebGL context verification
- `reference/troubleshooting.md` — detailed diagnostics (Chrome not responding, tab leaks, file access, security)
