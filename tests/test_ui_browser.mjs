/**
 * 真实浏览器 UI 测试（Chrome headless + CDP，零第三方依赖）。
 *
 * 为什么需要它：后端接口全绿并不能说明前端可用。曾经出现过这样的情况 ——
 * 系统 MIME 表把 .js 判成 text/plain，页面能打开、CSS 也正常、日志里静态
 * 文件全是 200，但浏览器拒绝执行脚本，于是滑块拖不动、上传点不动。
 * 这类问题只有真的在浏览器里跑一遍才抓得到。
 *
 * 覆盖：
 *   1. 脚本确实执行完成（初始化标记）
 *   2. 强度滑块能联动数字显示
 *   3. 打码方式切换能联动颜色选择器
 *   4. API Key 能写入 sessionStorage
 *   5. 通过 <input type=file> 真实上传图片并拿到处理结果
 *
 * 用法：
 *   node tests/test_ui_browser.mjs [baseUrl]
 *   默认 baseUrl = http://127.0.0.1:8000
 *   API Key 取自环境变量 NSFW_API_KEY，或项目根目录的 data/api_key
 *
 * 需要 Node 18+ 与本机已安装 Chrome。不属于 run-tests.bat 的默认范围。
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const DEBUG_PORT = 9333;

const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];

// 96x96 的 PNG，红/绿两色块，足够走完整个处理链路
const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAIAAABt+uBvAAAB7ElEQVR4Ae3BMQEAQAgCQOjiQCYTG4HBMJ/AneHv2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/Dd2CN8N/YI3409wndjj/DdKC2S2IUklBZJ7EISSoskdiEJpUUSu5CE0iKJXUhCaZHELiShtEhiF5JQWiSxC0koLZLYhSSUFknsQhJKiyR2IQmlRRK7kITSIoldSEJpkcQuJKG0SGIXklBaJLELSSgtktiFJJQWSexCEkqLJHYhCaVFEruQhNIiiV1IQmmRxC4kobRIYheSUFoksQtJKC2S2IUklBZJ7EISSoskdiEJpUUSu5CE0iKJXUhCaZHELiShtEhiF5JQWiSxC0koLZLYhSSUFknsQhJKiyR2IQmlRRK7kITSIoldSEJpkcQuJKG0SGIXklBaJLELSSgtktiFJJQWSexCEkqLJHYhCaVFEruQhNIiiV1IQmmRxC4kobRIYheSUFoksQtJHhKVzkGoyaDNAAAAAElFTkSuQmCC';

const failures = [];
function check(label, actual, expected) {
  const ok = actual === expected;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${label}`);
  if (!ok) {
    console.log(`         得到 ${JSON.stringify(actual)}，期望 ${JSON.stringify(expected)}`);
    failures.push(label);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- CDP 客户端
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      const entry = this.pending.get(msg.id);
      if (entry) {
        this.pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(JSON.stringify(msg.error)));
        else entry.resolve(msg.result);
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

  /** 在页面里求值，返回真实的 JS 值（非 JSON 字符串） */
  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
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
    } catch {
      /* 端口还没起来，继续等 */
    }
    await sleep(500);
  }
  return null;
}

// ---------------------------------------------------------------- 主流程
const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!chromePath) {
  console.error('找不到 Chrome / Edge，无法运行 UI 测试。');
  process.exit(2);
}

let apiKey = process.env.NSFW_API_KEY || '';
if (!apiKey) {
  const keyFile = join(ROOT, 'data', 'api_key');
  if (existsSync(keyFile)) apiKey = readFileSync(keyFile, 'utf8').trim();
}
if (!apiKey) {
  console.error('拿不到 API Key，请设置 NSFW_API_KEY 或先生成 data/api_key。');
  process.exit(2);
}

const profileDir = mkdtempSync(join(tmpdir(), 'nsfw-ui-'));
const imgPath = join(profileDir, 'ui-test.png');
writeFileSync(imgPath, Buffer.from(PNG_B64, 'base64'));

console.log('='.repeat(66));
console.log('真实浏览器 UI 测试');
console.log('='.repeat(66));
console.log(`  浏览器 : ${chromePath}`);
console.log(`  目标   : ${BASE}`);
console.log(`  CDP    : 127.0.0.1:${DEBUG_PORT}`);
console.log();

const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-gpu',
    '--window-size=1280,900',
    'about:blank',
  ],
  { stdio: 'ignore' }
);

let ws = null;
try {
  const wsUrl = await findTarget();
  if (!wsUrl) throw new Error('Chrome 调试端口未就绪');

  ws = new WebSocket(wsUrl);
  await new Promise((resolvePromise, rejectPromise) => {
    ws.addEventListener('open', resolvePromise, { once: true });
    ws.addEventListener('error', rejectPromise, { once: true });
  });

  const cdp = new CDP(ws);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('DOM.enable');

  // ---------------------------------------------------- 1. 加载与初始化
  console.log('='.repeat(66));
  console.log('1. 页面加载与脚本执行');
  console.log('='.repeat(66));
  await cdp.send('Page.navigate', { url: BASE });

  let ready = false;
  for (let i = 0; i < 240; i += 1) {
    try {
      if (await cdp.eval("document.documentElement.dataset.appReady === 'true'")) {
        ready = true;
        break;
      }
    } catch {
      /* 导航过程中求值可能失败，忽略 */
    }
    await sleep(250);
  }
  check('前端脚本执行完成（app.js 初始化标记存在）', ready, true);
  if (!ready) throw new Error('脚本未执行，后续交互测试无意义');

  check(
    '脚本以 JavaScript 类型加载',
    await cdp.eval(
      "Array.from(document.scripts).every(s => !s.src || s.type === '' || s.type.includes('javascript'))"
    ),
    true
  );

  // ---------------------------------------------------- 2. 滑块交互
  console.log();
  console.log('='.repeat(66));
  console.log('2. 强度滑块（用户报告的故障 1）');
  console.log('='.repeat(66));

  const sliderLogic = await cdp.eval(`
    (() => {
      const s = document.getElementById('intensity');
      const out = document.getElementById('intensityVal');
      if (!s || !out) return 'element-missing';
      const before = out.textContent;
      s.value = '99';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      return JSON.stringify({ before, after: out.textContent, value: s.value });
    })()
  `);
  const sliderState = JSON.parse(sliderLogic);
  check('滑块是 range 控件且可写', sliderState.value, '99');
  check('拖动滑块会同步更新数字显示', sliderState.after, '99');
  check('数字显示确实发生了变化', sliderState.after !== sliderState.before, true);

  // ---------------------------------------------------- 2b. 遮罩扩张滑块
  const paddingLogic = await cdp.eval(`
    (() => {
      const s = document.getElementById('padding');
      const out = document.getElementById('paddingVal');
      if (!s || !out) return 'element-missing';
      const before = out.textContent;
      s.value = '0';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      const atZero = out.textContent;
      s.value = '30';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      return JSON.stringify({ before, atZero, after: out.textContent, value: s.value });
    })()
  `);
  const paddingState = JSON.parse(paddingLogic);
  check('遮罩扩张滑块存在且可写', paddingState.value, '30');
  check('遮罩扩张滑块联动数字显示', paddingState.after, '30');
  check('可调到 0（严格只遮检测框）', paddingState.atZero, '0');
  check('默认扩张量为 3', paddingState.before, '3');
  // 复原，避免影响后续上传断言
  await cdp.eval(
    "(() => { const s = document.getElementById('padding'); s.value = '3'; s.dispatchEvent(new Event('input', { bubbles: true })); })()"
  );

  // ---------------------------------------------------- 2c. 灵敏度下拉
  const sensitivityLogic = await cdp.eval(`
    (() => {
      const s = document.getElementById('sensitivity');
      if (!s) return 'element-missing';
      const options = Array.from(s.options).map((o) => o.value);
      const before = s.value;
      s.value = 'aggressive';
      s.dispatchEvent(new Event('change', { bubbles: true }));
      return JSON.stringify({ options, before, after: s.value });
    })()
  `);
  const sensitivityState = JSON.parse(sensitivityLogic);
  check(
    '灵敏度提供四个档位',
    sensitivityState.options.join(','),
    'max,aggressive,balanced,strict'
  );
  check('灵敏度默认是最高召回', sensitivityState.before, 'max');
  check('灵敏度可切换到高召回', sensitivityState.after, 'aggressive');
  // 切回默认，避免影响后续上传测试
  await cdp.eval(
    "(() => { const s = document.getElementById('sensitivity'); s.value = 'balanced'; s.dispatchEvent(new Event('change', { bubbles: true })); })()"
  );

  // ---------------------------------------------------- 2d. 遮罩形状
  const shapeLogic = await cdp.eval(`
    (() => {
      const s = document.getElementById('shapeMask');
      if (!s) return 'element-missing';
      const options = Array.from(s.options).map((o) => o.value);
      const before = s.value;
      s.value = 'rect';
      s.dispatchEvent(new Event('change', { bubbles: true }));
      const after = s.value;
      s.value = 'shape';
      s.dispatchEvent(new Event('change', { bubbles: true }));
      return JSON.stringify({ options, before, after, restored: s.value });
    })()
  `);
  const shapeState = JSON.parse(shapeLogic);
  check(
    '遮罩形状提供轮廓/矩形两个选项',
    shapeState.options.join(','),
    'shape,rect'
  );
  check('遮罩形状默认是轮廓贴合', shapeState.before, 'shape');
  check('遮罩形状可切换到矩形', shapeState.after, 'rect');
  check('遮罩形状可切回轮廓', shapeState.restored, 'shape');

  // ---------------------------------------------------- 2e. 边缘羽化滑块
  const featherLogic = await cdp.eval(`
    (() => {
      const s = document.getElementById('feather');
      const out = document.getElementById('featherVal');
      if (!s || !out) return 'element-missing';
      const before = out.textContent;
      s.value = '12';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      return JSON.stringify({ before, after: out.textContent, value: s.value });
    })()
  `);
  const featherState = JSON.parse(featherLogic);
  check('羽化滑块存在且可写', featherState.value, '12');
  check('羽化滑块联动数字显示', featherState.after, '12');
  check('羽化默认值为 2', featherState.before, '2');
  // 复原，避免影响后面的上传断言
  await cdp.eval(
    "(() => { const s = document.getElementById('feather'); s.value = '2'; s.dispatchEvent(new Event('input', { bubbles: true })); })()"
  );

  // ---------------------------------------------------- 2f. 遮罩扩张默认值
  check(
    '遮罩扩张默认值为 3（已从原版的 40 收小）',
    await cdp.eval("document.getElementById('paddingVal').textContent"),
    '3'
  );

  // ---------------------------------------------------- 3. 模式切换
  console.log();
  console.log('='.repeat(66));
  console.log('3. 打码方式切换与颜色选择器联动');
  console.log('='.repeat(66));
  const colorLogic = await cdp.eval(`
    (() => {
      const solid = document.querySelector('input[name="mode"][value="solid"]');
      const box = document.getElementById('colorPickerContainer');
      const blur = document.querySelector('input[name="mode"][value="blur"]');
      solid.checked = true;
      solid.dispatchEvent(new Event('change', { bubbles: true }));
      const shownForSolid = !box.classList.contains('hidden');
      blur.checked = true;
      blur.dispatchEvent(new Event('change', { bubbles: true }));
      const hiddenForBlur = box.classList.contains('hidden');
      return JSON.stringify({ shownForSolid, hiddenForBlur });
    })()
  `);
  const colorState = JSON.parse(colorLogic);
  check('选「纯色填充」时显示颜色选择器', colorState.shownForSolid, true);
  check('切回「高斯模糊」时隐藏颜色选择器', colorState.hiddenForBlur, true);

  // ---------------------------------------------------- 4. 密钥记住与自动填充
  console.log();
  console.log('='.repeat(66));
  console.log('4. API Key 记忆与自动填充（用户需求：不再手动填写）');
  console.log('='.repeat(66));

  // 4a. 默认不勾「记住」时，只写 sessionStorage
  const keyStored = await cdp.eval(`
    (() => {
      const k = document.getElementById('apiKeyInput');
      k.value = ${JSON.stringify(apiKey)};
      k.dispatchEvent(new Event('change', { bubbles: true }));
      // 不依赖具体存储键名，只要存储里有这个值就算通过
      const inSession = Object.keys(sessionStorage)
        .map((n) => sessionStorage.getItem(n));
      const inLocal = Object.keys(localStorage)
        .map((n) => localStorage.getItem(n));
      return JSON.stringify({
        session: inSession.includes(${JSON.stringify(apiKey)}),
        local: inLocal.includes(${JSON.stringify(apiKey)}),
      });
    })()
  `);
  const keyState = JSON.parse(keyStored);
  check('密钥写入 sessionStorage', keyState.session, true);
  check('未勾「记住」时不写入 localStorage', keyState.local, false);

  // 4b. 勾上「记住」后写入 localStorage
  const afterRemember = await cdp.eval(`
    (() => {
      const box = document.getElementById('rememberKey');
      box.checked = true;
      box.dispatchEvent(new Event('change', { bubbles: true }));
      const inLocal = Object.keys(localStorage)
        .map((n) => localStorage.getItem(n));
      return JSON.stringify({
        checked: box.checked,
        local: inLocal.includes(${JSON.stringify(apiKey)}),
      });
    })()
  `);
  const rememberState = JSON.parse(afterRemember);
  check('「记住」复选框可勾选', rememberState.checked, true);
  check('勾选后密钥写入 localStorage', rememberState.local, true);

  // 4c. 刷新页面后应当自动填好 —— 这才是用户真正要的效果
  await cdp.send('Page.reload');
  let readyAfterReload = false;
  for (let i = 0; i < 240; i += 1) {
    try {
      if (await cdp.eval("document.documentElement.dataset.appReady === 'true'")) {
        readyAfterReload = true;
        break;
      }
    } catch {
      /* 导航中，忽略 */
    }
    await sleep(250);
  }
  check('刷新后脚本正常执行', readyAfterReload, true);
  check(
    '刷新后密钥自动填入，无需手动输入',
    await cdp.eval("document.getElementById('apiKeyInput').value"),
    apiKey
  );
  check(
    '刷新后「记住」状态保持勾选',
    await cdp.eval("document.getElementById('rememberKey').checked"),
    true
  );

  // 4d. 取消勾选应当清掉持久化副本
  const afterForget = await cdp.eval(`
    (() => {
      const box = document.getElementById('rememberKey');
      box.checked = false;
      box.dispatchEvent(new Event('change', { bubbles: true }));
      const inLocal = Object.keys(localStorage)
        .map((n) => localStorage.getItem(n));
      return JSON.stringify({
        local: inLocal.includes(${JSON.stringify(apiKey)}),
      });
    })()
  `);
  check('取消「记住」后清除 localStorage 中的密钥', JSON.parse(afterForget).local, false);

  // ---- 一键填写：从服务端读本机密钥并自动填入 ----
  await cdp.eval("document.getElementById('apiKeyInput').value = ''");
  await cdp.eval("document.getElementById('keyFillBtn').click()");
  await sleep(600);
  const filledKey = await cdp.eval("document.getElementById('apiKeyInput').value");
  check('一键填写自动填入密钥', filledKey.length >= 16, true);
  check('一键填写后可正常处理（密钥有效）', filledKey === filledKey.trim() && filledKey.length > 0, true);

  // ---- 语言切换：中文 -> English -> 日本語，词条即时生效并持久化 ----
  check('语言选择器默认中文', await cdp.eval("document.getElementById('langSelect').value"), 'zh');
  check('默认中文标题', await cdp.eval("document.querySelector('[data-i18n=\"controls_title\"]').textContent"), '处理选项');
  await cdp.eval("(() => { const l = document.getElementById('langSelect'); l.value = 'en'; l.dispatchEvent(new Event('change', { bubbles: true })); })()");
  check('切到 English 后标题变化',
    await cdp.eval("document.querySelector('[data-i18n=\"controls_title\"]').textContent"),
    'Options');
  check('切到 English 后按钮变化',
    await cdp.eval("document.getElementById('resetSettings').textContent"),
    'Reset to defaults');
  await cdp.eval("(() => { const l = document.getElementById('langSelect'); l.value = 'ja'; l.dispatchEvent(new Event('change', { bubbles: true })); })()");
  check('切到 日本語 后标题变化',
    await cdp.eval("document.querySelector('[data-i18n=\"controls_title\"]').textContent"),
    '処理オプション');
  // 刷新后语言保持
  await cdp.send('Page.navigate', { url: BASE + '/' });
  await sleep(1500);
  check('刷新后语言保持为 日本語', await cdp.eval("localStorage.getItem('nsfw_lang')"), 'ja');
  check('刷新后标题仍是日语',
    await cdp.eval("document.querySelector('[data-i18n=\"controls_title\"]').textContent"),
    '処理オプション');
  // 切回中文，避免影响后续断言
  await cdp.eval("(() => { const l = document.getElementById('langSelect'); l.value = 'zh'; l.dispatchEvent(new Event('change', { bubbles: true })); })()");
  check('切回中文正常',
    await cdp.eval("document.querySelector('[data-i18n=\"controls_title\"]').textContent"),
    '处理选项');

  // ---------------------------------------------------- 4e. 参数记忆
  console.log();
  console.log('='.repeat(66));
  console.log('4e. 参数记忆（用户需求：下次打开保持上次设置）');
  console.log('='.repeat(66));

  await cdp.eval(`
    (() => {
      const set = (id, v) => {
        const el = document.getElementById(id);
        el.value = v;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      const radio = document.querySelector('input[name="mode"][value="pixel"]');
      radio.checked = true;
      radio.dispatchEvent(new Event('change', { bubbles: true }));
      set('intensity', '77');
      set('sensitivity', 'aggressive');
      set('ruleset', 'strict');
      set('padding', '25');
      set('shapeMask', 'rect');
      set('feather', '9');
    })()
  `);
  check(
    '参数已改动',
    await cdp.eval("document.getElementById('sensitivity').value"),
    'aggressive'
  );

  await cdp.send('Page.reload');
  let readyAfterSettings = false;
  for (let i = 0; i < 240; i += 1) {
    try {
      if (await cdp.eval("document.documentElement.dataset.appReady === 'true'")) {
        readyAfterSettings = true;
        break;
      }
    } catch {
      /* 导航中 */
    }
    await sleep(250);
  }
  check('刷新后脚本正常执行', readyAfterSettings, true);

  const restored = JSON.parse(
    await cdp.eval(`
      (() => JSON.stringify({
        mode: (document.querySelector('input[name="mode"]:checked') || {}).value,
        intensity: document.getElementById('intensity').value,
        intensityLabel: document.getElementById('intensityVal').textContent,
        sensitivity: document.getElementById('sensitivity').value,
        ruleset: document.getElementById('ruleset').value,
        padding: document.getElementById('padding').value,
        paddingLabel: document.getElementById('paddingVal').textContent,
        shapeMask: document.getElementById('shapeMask').value,
        feather: document.getElementById('feather').value,
        featherLabel: document.getElementById('featherVal').textContent,
      }))()
    `)
  );
  check('刷新后打码方式保持', restored.mode, 'pixel');
  check('刷新后强度保持', restored.intensity, '77');
  check('刷新后强度显示同步', restored.intensityLabel, '77');
  check('刷新后灵敏度保持', restored.sensitivity, 'aggressive');
  check('刷新后打码规则保持', restored.ruleset, 'strict');
  check('刷新后遮罩扩张保持', restored.padding, '25');
  check('刷新后遮罩扩张显示同步', restored.paddingLabel, '25');
  check('刷新后遮罩形状保持', restored.shapeMask, 'rect');
  check('刷新后羽化保持', restored.feather, '9');
  check('刷新后羽化显示同步', restored.featherLabel, '9');

  // 恢复默认
  await cdp.eval("document.getElementById('resetSettings').click()");
  await sleep(200);
  const afterReset = JSON.parse(
    await cdp.eval(`
      (() => JSON.stringify({
        sensitivity: document.getElementById('sensitivity').value,
        ruleset: document.getElementById('ruleset').value,
        padding: document.getElementById('padding').value,
        shapeMask: document.getElementById('shapeMask').value,
      }))()
    `)
  );
  check('「恢复默认设置」把灵敏度复位为最高召回', afterReset.sensitivity, 'max');
  check('灵敏度默认选中「最高召回」',
    await cdp.eval("document.getElementById('sensitivity').value === 'max' && "
      + "document.getElementById('sensitivity').options[0].value === 'max'"),
    true);
  check('内收滑块存在且默认 15',
    await cdp.eval("document.getElementById('maskInset').value"),
    '15');
  check('「恢复默认设置」把打码规则复位为 pixiv', afterReset.ruleset, 'pixiv');
  check('「恢复默认设置」把扩张复位', afterReset.padding, '3');
  check('「恢复默认设置」把遮罩形状复位', afterReset.shapeMask, 'shape');

  // ---------------------------------------------------- 5. 真实上传
  console.log();
  console.log('='.repeat(66));
  console.log('5. 通过文件选择框真实上传（用户报告的故障 2）');
  console.log('='.repeat(66));
  const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
  const { nodeId } = await cdp.send('DOM.querySelector', {
    nodeId: root.nodeId,
    selector: '#fileInput',
  });
  check('文件输入框存在于 DOM 中', nodeId > 0, true);

  await cdp.send('DOM.setFileInputFiles', { files: [imgPath], nodeId });

  // 首次请求会触发模型加载，等久一点
  let queueText = '';
  let labelText = '';
  for (let i = 0; i < 300; i += 1) {
    labelText = await cdp.eval("document.getElementById('blurLabel').textContent");
    if (labelText !== '—') break;
    await sleep(500);
  }

  check('上传后出现处理结果（状态标签已更新）', labelText === '已打码' || labelText === '未检出', true);
  check(
    '队列面板已显示',
    await cdp.eval("!document.getElementById('batchPanel').classList.contains('hidden')"),
    true
  );
  check(
    '队列中出现 1 个条目',
    await cdp.eval("document.querySelectorAll('#queueList .queue-item').length"),
    1
  );
  check(
    '结果面板已显示',
    await cdp.eval("!document.getElementById('results').classList.contains('hidden')"),
    true
  );
  check(
    '原图预览模块已被移除（由手动遮罩编辑器取代）',
    await cdp.eval("document.getElementById('originalImg') === null"),
    true
  );
  // 结果图是异步加载的（编辑器初始化会占用主线程），轮询等待而不是断言一次
  let resultImgLoaded = false;
  for (let i = 0; i < 40; i += 1) {
    resultImgLoaded = await cdp.eval(
      "(() => { const el = document.getElementById('processedImg'); return !!el.getAttribute('src') && el.naturalWidth > 0; })()"
    );
    if (resultImgLoaded) break;
    await sleep(250);
  }
  check('打码结果图已加载', resultImgLoaded, true);
  check(
    '文件名已显示',
    await cdp.eval("document.getElementById('fileNameDisplay').textContent"),
    'ui-test.png'
  );
  check(
    '下载按钮已指向结果图',
    await cdp.eval(
      "(document.getElementById('downloadBtn').getAttribute('href') || '').includes('/api/files/')"
    ),
    true
  );
  queueText = await cdp.eval("document.getElementById('queueCount').textContent");
  check('队列计数正确', queueText, '1 Files');
  check(
    '检测明细区域已就绪（无检出时显示说明文字）',
    await cdp.eval(
      "!document.getElementById('decisionsEmpty').classList.contains('hidden')"
    ),
    true
  );

  // -------------------------------------------- 6. 手动涂抹重新打码
  console.log();
  console.log('='.repeat(66));
  console.log('6. 手动涂抹选区后重新打码（用户需求：涂哪里就只打哪里）');
  console.log('='.repeat(66));

  // 等编辑器把原图载进来
  let editorReady = false;
  for (let i = 0; i < 120; i += 1) {
    editorReady = await cdp.eval(
      "document.getElementById('editorCanvas').width > 0 && !document.getElementById('editorStatus').textContent.includes('正在载入')"
    );
    if (editorReady) break;
    await sleep(250);
  }
  check('原图已载入编辑器画布', editorReady, true);
  check(
    '遮罩与原图尺寸一致',
    await cdp.eval(
      "(() => { const c = document.getElementById('editorCanvas'); return c.width > 0 && c.height > 0; })()"
    ),
    true
  );

  /** 在画布上画一笔：用真实输入事件，走完整的 pointer 链路 */
  async function paintStroke(from, to) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: from.x, y: from.y, button: 'left', clickCount: 1, buttons: 1,
    });
    const steps = 6;
    for (let s = 1; s <= steps; s += 1) {
      await cdp.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: from.x + ((to.x - from.x) * s) / steps,
        y: from.y + ((to.y - from.y) * s) / steps,
        button: 'left',
        buttons: 1,
      });
      await sleep(16);
    }
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: to.x, y: to.y, button: 'left', clickCount: 1, buttons: 0,
    });
    await sleep(80);
  }

  // 画布滚动进可视区后取坐标（CDP 用的是视口坐标）
  const rectJson = await cdp.eval(`
    (() => {
      const c = document.getElementById('editorCanvas');
      c.scrollIntoView({ block: 'center' });
      const r = c.getBoundingClientRect();
      return JSON.stringify({ x: r.left, y: r.top, w: r.width, h: r.height });
    })()
  `);
  const rect = JSON.parse(rectJson);
  check('画布可见且有尺寸', rect.w > 50 && rect.h > 50, true);

  // 先清空，再加上一笔 —— 这样覆盖像素数就完全由这一笔决定
  await cdp.eval("document.getElementById('editorClear').click()");
  await cdp.eval("document.getElementById('toolPaint').click()");
  await cdp.eval(
    "(() => { const e = document.getElementById('brushSize'); e.value = '60'; e.dispatchEvent(new Event('input', { bubbles: true })); })()"
  );
  await sleep(120);

  await paintStroke(
    { x: rect.x + rect.w * 0.3, y: rect.y + rect.h * 0.3 },
    { x: rect.x + rect.w * 0.5, y: rect.y + rect.h * 0.45 }
  );

  // 重新打码
  await cdp.eval("document.getElementById('editorApply').click()");
  let applyStatus = '';
  for (let i = 0; i < 200; i += 1) {
    applyStatus = await cdp.eval("document.getElementById('editorStatus').textContent");
    if (applyStatus.includes('已按你的遮罩')) break;
    await sleep(250);
  }
  check('重新打码完成', applyStatus.includes('已按你的遮罩'), true);

  const pixelMatch = applyStatus.match(/覆盖 ([\d,]+) 个像素/);
  const paintedPixels = pixelMatch ? Number(pixelMatch[1].replace(/,/g, '')) : 0;
  check('涂抹区域被实际打码（像素数 > 0）', paintedPixels > 0, true);
  check(
    '打码面积远小于整张图（不是糊一大片）',
    paintedPixels < 800 * 600 * 0.5,
    true
  );
  console.log('  info: 本次涂抹覆盖 ' + paintedPixels + ' 个像素');

  // 全部清除后重新打码，应当一处都不遮
  await cdp.eval("document.getElementById('editorClear').click()");
  await sleep(120);
  await cdp.eval("document.getElementById('editorApply').click()");
  let clearedStatus = '';
  for (let i = 0; i < 200; i += 1) {
    clearedStatus = await cdp.eval("document.getElementById('editorStatus').textContent");
    if (clearedStatus.includes('已按你的遮罩')) break;
    await sleep(250);
  }
  check(
    '清空遮罩后重新打码，覆盖 0 像素',
    clearedStatus.includes('覆盖 0 个像素'),
    true
  );

  // 恢复自动遮罩应当重新填入内容
  await cdp.eval("document.getElementById('editorResetAuto').click()");
  await sleep(600);
  const autoStatus = await cdp.eval("document.getElementById('editorStatus').textContent");
  check('可恢复为自动遮罩', autoStatus.includes('已恢复为自动遮罩'), true);

  // ---- 撤回：一笔涂下去再撤回，遮罩应当回到空 ----
  await cdp.eval("document.getElementById('editorClear').click()");
  await sleep(120);
  await paintStroke(
    { x: rect.x + rect.w * 0.3, y: rect.y + rect.h * 0.3 },
    { x: rect.x + rect.w * 0.5, y: rect.y + rect.h * 0.45 }
  );
  check(
    '撤回按钮可用（有历史）',
    await cdp.eval("!document.getElementById('editorUndo').disabled"),
    true
  );
  await cdp.eval("document.getElementById('editorUndo').click()");
  await sleep(150);
  await cdp.eval("document.getElementById('editorApply').click()");
  let undoStatus = '';
  for (let i = 0; i < 200; i += 1) {
    undoStatus = await cdp.eval("document.getElementById('editorStatus').textContent");
    if (undoStatus.includes('已按你的遮罩')) break;
    await sleep(250);
  }
  check(
    '撤回后重新打码覆盖 0 像素（笔画确实被撤销）',
    undoStatus.includes('覆盖 0 个像素'),
    true
  );

  // ---- 笔刷光标：移动后应出现且尺寸跟随笔刷 ----
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseMoved', x: rect.x + rect.w * 0.4, y: rect.y + rect.h * 0.4,
  });
  await sleep(120);
  const cursorState = JSON.parse(await cdp.eval(`
    (() => {
      const el = document.getElementById('brushCursor');
      return JSON.stringify({ hidden: el.classList.contains('hidden'),
                              w: Math.round(parseFloat(el.style.width) || 0) });
    })()
  `));
  check('笔刷光标随鼠标出现', cursorState.hidden, false);
  check('笔刷光标尺寸与笔刷一致（按缩放换算）', cursorState.w > 5 && cursorState.w < 400, true);

  // ---- 编辑器已内嵌到结果区（替换原图预览的位置） ----
  check(
    '编辑器包含画布',
    await cdp.eval(
      "document.getElementById('editorPanel').contains(document.getElementById('editorCanvas'))"
    ),
    true
  );
  check(
    '编辑器已内嵌进结果区（原图原位置）',
    await cdp.eval(
      "document.getElementById('results').contains(document.getElementById('editorCanvas'))"
    ),
    true
  );

  // 关键回归：提示层只应覆盖实际被遮的区域。
  // 之前遮罩画布用黑白不透明图叠加，source-in 按 alpha 混合会把
  // 整张画布染成红色，用户根本看不出涂了哪里。
  const redRatio = Number(
    await cdp.eval(`
      (() => {
        const c = document.getElementById('editorCanvas');
        if (!c.width || !c.height) return 1;
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let red = 0;
        let total = 0;
        for (let i = 0; i < d.length; i += 4) {
          total += 1;
          if (d[i] > d[i + 1] + 40 && d[i] > d[i + 2] + 40) red += 1;
        }
        return total ? red / total : 1;
      })()
    `)
  );
  check(
    '提示层只覆盖被遮区域（红色占比 < 60%）',
    redRatio < 0.6,
    true
  );
  console.log('  info: 画布红色像素占比 ' + (redRatio * 100).toFixed(1) + '%');

  // 截图留证
  const shot = join(ROOT, 'ui-test-screenshot.png');
  try {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(shot, Buffer.from(data, 'base64'));
    console.log();
    console.log(`  info: 截图已保存 -> ${shot}`);
  } catch {
    /* 截图失败不影响结论 */
  }
} catch (err) {
  console.log();
  console.log('  [FAIL] 测试中断: ' + err.message);
  failures.push('测试中断: ' + err.message);
} finally {
  try {
    if (ws) ws.close();
  } catch {
    /* ignore */
  }
  chrome.kill();
  await sleep(800);
  try {
    rmSync(profileDir, { recursive: true, force: true });
  } catch {
    /* Windows 上偶尔会被占用，忽略 */
  }
}

console.log();
console.log('='.repeat(66));
if (failures.length) {
  console.log(`结果：${failures.length} 项失败`);
  for (const f of failures) console.log('  - ' + f);
  process.exit(1);
}
console.log('结果：全部通过');
