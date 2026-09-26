#!/usr/bin/env node
// Screenshot a page with headless Chrome over the DevTools Protocol (no npm deps; Node >= 22).
//   node scripts/dev/snap.mjs <url> <out.png> [--click "Agent steps"]... [--width 1440] [--height 900] [--wait 4000]
//                             [--full] [--crop WxH+X+Y]
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const [url, out] = args;
const opt = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : dflt; };
const clicks = args.flatMap((a, i) => (a === '--click' ? [args[i + 1]] : []));
const width = Number(opt('width', 1440));
const height = Number(opt('height', 900));
const wait = Number(opt('wait', 4000));
const full = args.includes('--full');
const cropArg = opt('crop', null); // "WxH+X+Y"
if (!url || !out) { console.error('usage: snap.mjs <url> <out.png> [--click text] [--full]'); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const port = 9300 + Math.floor(Math.random() * 600);
const profile = mkdtempSync(join(tmpdir(), 'snap-'));
const chrome = spawn(process.env.CHROME || 'google-chrome', [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars', '--no-first-run',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, `--window-size=${width},${height}`, 'about:blank',
], { stdio: 'ignore' });

try {
  let target;
  for (let i = 0; i < 60 && !target; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((t) => t.type === 'page');
    } catch { /* chrome still starting */ }
    if (!target) await sleep(250);
  }
  if (!target) throw new Error('chrome did not expose a page target');

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });

  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  await sleep(wait);
  for (const text of clicks) {
    const expr = `(() => {
      const want = ${JSON.stringify(text)}.toLowerCase();
      const els = [...document.querySelectorAll('button, summary, [role=button], a, div, span')]
        .filter((e) => (e.innerText || '').trim().toLowerCase().startsWith(want) && e.offsetParent !== null);
      els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
      const el = els[0]; if (!el) return false;
      (el.closest('button, summary, [role=button]') || el).click(); return true;
    })()`;
    let clicked = false;
    for (let attempt = 0; attempt < 10 && !clicked; attempt++) {  // SPA content may still be rendering
      const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
      clicked = Boolean(r.result?.result?.value);
      if (!clicked) await sleep(1000);
    }
    if (!clicked) console.error(`snap: nothing to click for "${text}"`);
    await sleep(1200);
  }
  let clip;
  if (full) {
    const m = await send('Page.getLayoutMetrics');
    const size = m.result.cssContentSize || m.result.contentSize;
    clip = { x: 0, y: 0, width, height: Math.min(Math.ceil(size.height), 6000), scale: 1 };
  }
  if (cropArg) {
    const [, w, h, x, y] = cropArg.match(/^(\d+)x(\d+)\+(\d+)\+(\d+)$/).map(Number);
    clip = { x, y, width: w, height: h, scale: 1 };
  }
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full, ...(clip ? { clip } : {}) });
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log(`snap: ${out}`);
  ws.close();
} finally {
  chrome.kill('SIGKILL');
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
