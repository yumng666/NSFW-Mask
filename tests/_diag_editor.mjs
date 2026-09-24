/**
 * 诊断：用接近真实的大图走一遍手动涂抹流程，并抓取页面控制台输出。
 * 仅供排查，不属于常规测试套件。
 *
 * 运行前提：服务已在 127.0.0.1:8000 运行
 *   node tests/_diag_editor.mjs
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const IMG_PATH = process.argv[3] || '';
const DEBUG_PORT = 9444;
const API_KEY = process.env.NSFW_API_KEY || '';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.console = [];
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      const entry = this.pending.get(msg.id);
      if (entry) {
        this.pending.delete(msg.id);
        msg.error ? entry.reject(new Error(JSON.stringify(msg.error))) : entry.resolve(msg.result);
        return;
      }
      if (msg.method === 'Runtime.consoleAPICalled') {
        const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ');
        this.console.push(`[console.${msg.params.type}] ${text}`);
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails;
        this.console.push(`[EXCEPTION] ${d.text} ${d.exception?.description || ''}`);
      }
      if (msg.method === 'Log.entryAdded') {
        this.console.push(`[${msg.params.entry.level}] ${msg.params.entry.text}`);
      }
    });
  }
  send(method, params = {}, timeoutMs = 60000) {
    const id = (this.nextId += 1);
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP 超时: ${method}`));
      }, timeoutMs);
    });
  }
  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (res.exceptionDetails) {
      throw new Error('页面内异常: ' + JSON.stringify(res.exceptionDetails.exception));
    }
    return res.result.value;
  }
}

async function findTarget() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 端口未就绪 */ }
    await sleep(500);
  }
  return null;
}

// ---- 主流程 ----
const dir = mkdtempSync(join(tmpdir(), 'diag-editor-'));

async function main() {
  if (!API_KEY) {
    console.error('需要 NSFW_API_KEY 环境变量');
    process.exit(2);
  }
  if (!IMG_PATH) {
    console.error('用法: node tests/_diag_editor.mjs [baseUrl] <图片路径>');
    process.exit(2);
  }

  // 上传大图
  const { readFileSync } = await import('node:fs');
  const form = new FormData();
  form.append('file', new Blob([readFileSync(IMG_PATH)]), 'big.png');
  form.append('mode', 'blur');
  form.append('intensity', '51');
  const up = await fetch(`${BASE}/api/process`, {
    method: 'POST', headers: { 'X-API-KEY': API_KEY }, body: form,
  });
  const payload = await up.json();
  console.log('上传结果:', up.status);
  console.log('  id          :', payload.id);
  console.log('  original_url:', payload.original_url);
  console.log('  mask_url    :', payload.mask_url);
  console.log('  blur_count  :', payload.blur_count);

  // 起浏览器
  const chrome = spawn(
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    [
      '--headless=new', `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${join(dir, 'profile')}`, '--no-first-run',
      '--no-default-browser-check', '--disable-extensions', '--disable-gpu',
      '--enable-logging=stderr', '--v=0',
      '--window-size=1400,900', 'about:blank',
    ],
    { stdio: ['ignore', 'ignore', 'pipe'] }
  );
  const chromeErr = [];
  chrome.stderr.on('data', (d) => chromeErr.push(String(d)));
  chrome.on('exit', (code, sig) => {
    console.log(`\n[Chrome 进程退出] code=${code} sig=${sig}`);
  });

  let ws = null;
  try {
    const wsUrl = await findTarget();
    ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', rej, { once: true });
    });
    const cdp = new CDP(ws);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');
    await cdp.send('DOM.enable');

    await cdp.send('Page.navigate', { url: BASE });
    for (let i = 0; i < 240; i += 1) {
      try {
        if (await cdp.eval("document.documentElement.dataset.appReady === 'true'")) break;
      } catch { /* 导航中 */ }
      await sleep(250);
    }
    console.log('\n页面已加载');
    console.log('---- 控制台（加载阶段）----');
    for (const line of cdp.console) console.log('  ' + line);
    console.log('------------------------');

    // 填密钥
    console.log('[step] 填入密钥');
    await cdp.eval(`
      (() => {
        const k = document.getElementById('apiKeyInput');
        k.value = ${JSON.stringify(API_KEY)};
        k.dispatchEvent(new Event('change', { bubbles: true }));
        return 'ok';
      })()
    `);

    // 通过文件输入框上传同一张大图
    console.log('[step] 通过文件输入框上传');
    const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fileInput' });
    await cdp.send('DOM.setFileInputFiles', { files: [IMG_PATH], nodeId });

    // 等处理完成 + 编辑器就绪
    console.log('[step] 等待处理与编辑器就绪…');
    let status = '';
    for (let i = 0; i < 400; i += 1) {
      try {
        status = await cdp.eval("document.getElementById('editorStatus').textContent");
      } catch (err) {
        console.log(`\n[!] 第 ${i} 次轮询时连接异常: ${err.message}`);
        console.log('    WebSocket readyState:', ws.readyState, '(3=已断开)');
        break;
      }
      if (!status.includes('正在载入') && !status.includes('上传图片后即可')) break;
      if (i % 10 === 0) console.log(`   等待中(${i}): ${JSON.stringify(status)}`);
      await sleep(500);
    }
    console.log('编辑器状态文本:', JSON.stringify(status));
    console.log('---- 控制台（上传阶段）----');
    for (const line of cdp.console) console.log('  ' + line);
    console.log('------------------------');

    const info = JSON.parse(await cdp.eval(`
      (() => JSON.stringify({
        bitmap: [editorCanvasWidth(), editorCanvasHeight()],
        rect: (r => [Math.round(r.width), Math.round(r.height)])(document.getElementById('editorCanvas').getBoundingClientRect()),
        resultsHidden: document.getElementById('results').classList.contains('hidden'),
        editorVisible: !document.getElementById('editorBlock').closest('.hidden'),
        origLoaded: !!document.querySelector('#originalImg')?.getAttribute('src'),
      }))()
    `.replace(/editorCanvasWidth\(\)/g, "document.getElementById('editorCanvas').width")
       .replace(/editorCanvasHeight\(\)/g, "document.getElementById('editorCanvas').height")));
    console.log('画布位图尺寸:', info.bitmap, ' 显示尺寸:', info.rect);
    console.log('结果面板隐藏:', info.resultsHidden, ' 编辑器可见:', info.editorVisible);

    // 涂抹。注意两点：
    // 1. CDP 的鼠标事件用的是视口坐标，坐标必须在窗口内，否则事件到不了画布；
    // 2. displayResult 里的 scrollIntoView 是 smooth 滚动，会持续几百毫秒，
    //    所以这里改成即时滚动并等待稳定，避免拿到滚动中途的坐标。
    await cdp.eval(`
      (() => {
        document.getElementById('editorCanvas').scrollIntoView({ block: 'center', behavior: 'instant' });
      })()
    `);
    await sleep(700);
    const rect = JSON.parse(await cdp.eval(`
      (() => { const r = document.getElementById('editorCanvas').getBoundingClientRect();
               return JSON.stringify({ x: r.left, y: r.top, w: r.width, h: r.height,
                                       vh: window.innerHeight }); })()
    `));
    console.log('画布视口坐标:', JSON.stringify(rect), ' 窗口高:', rect.vh);

    // 涂抹区间取"画布与视口的可见交集"的中段，保证事件落在画布上
    const visTop = Math.max(rect.y, 0);
    const visBottom = Math.min(rect.y + rect.h, rect.vh);
    if (visBottom - visTop < 40) {
      throw new Error(`画布几乎不可见 (可视高度 ${Math.round(visBottom - visTop)}px)，无法涂抹`);
    }
    console.log(`  可视区间 y: ${Math.round(visTop)} ~ ${Math.round(visBottom)}`);

    const from = { x: rect.x + rect.w * 0.3, y: visTop + (visBottom - visTop) * 0.35 };
    const to = { x: rect.x + rect.w * 0.55, y: visTop + (visBottom - visTop) * 0.6 };
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: from.x, y: from.y, button: 'left', clickCount: 1, buttons: 1 });
    for (let s = 1; s <= 8; s += 1) {
      await cdp.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: from.x + ((to.x - from.x) * s) / 8,
        y: from.y + ((to.y - from.y) * s) / 8,
        button: 'left', buttons: 1,
      });
      await sleep(20);
    }
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: to.x, y: to.y, button: 'left', clickCount: 1, buttons: 0 });
    await sleep(150);

    // 提交重新打码
    await cdp.eval("document.getElementById('editorApply').click()");
    let applyStatus = '';
    for (let i = 0; i < 200; i += 1) {
      applyStatus = await cdp.eval("document.getElementById('editorStatus').textContent");
      if (applyStatus.includes('已按你的遮罩') || applyStatus.includes('失败')) break;
      await sleep(300);
    }
    console.log('\n重新打码结果:', JSON.stringify(applyStatus));

    console.log('\n---- 页面控制台 ----');
    for (const line of cdp.console) console.log('  ' + line);
  } catch (err) {
    console.log('诊断中断:', err.message);
  } finally {
    console.log('\n---- Chrome stderr（最后 25 行）----');
    for (const line of chromeErr.slice(-25)) console.log('  ' + line.trim());
    try { if (ws) ws.close(); } catch { /* ignore */ }
    chrome.kill();
    await sleep(800);
  }
}

main();
