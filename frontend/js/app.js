'use strict';

/**
 * NSFW Guard Pro — 前端逻辑。
 *
 * 与原版的主要差异：
 * - 不再硬编码默认密钥，也不再预填。密钥只存 sessionStorage（关掉标签页即失效），
 *   而不是 localStorage 长期驻留。
 * - 所有插入 DOM 的文本一律走 textContent，不用 innerHTML 拼接，
 *   避免文件名里的特殊字符被当成标记解析。
 * - 图片地址使用服务端返回的带签名令牌 URL，无需额外请求头。
 */

(function () {
  const $ = (id) => document.getElementById(id);

  const dropzone = $('dropzone');
  const fileInput = $('fileInput');
  const results = $('results');
  const intensity = $('intensity');
  const intensityVal = $('intensityVal');
  const status = $('status');
  const processedImg = $('processedImg');
  const scoresContainer = $('scoresContainer');
  const resetBtn = $('resetBtn');
  const downloadBtn = $('downloadBtn');
  const downloadAllBtn = $('downloadAllBtn');
  const fileNameDisplay = $('fileNameDisplay');
  const summaryInfo = $('summaryInfo');
  const blurLabel = $('blurLabel');
  const skeleton = $('skeleton');
  const blurOverlay = $('blurOverlay');
  const batchPanel = $('batchPanel');
  const queueCount = $('queueCount');
  const queueList = $('queueList');
  const apiKeyInput = $('apiKeyInput');
  const keySaveBtn = $('keySaveBtn');
  const rememberKey = $('rememberKey');
  const colorPickerContainer = $('colorPickerContainer');
  const solidColor = $('solidColor');
  const sensitivity = $('sensitivity');
  const maskInset = $('maskInset');
  const maskInsetVal = $('maskInsetVal');
  const ruleset = $('ruleset');
  const padding = $('padding');
  const paddingVal = $('paddingVal');
  const shapeMask = $('shapeMask');
  const feather = $('feather');
  const featherVal = $('featherVal');
  const decisionsContainer = $('decisionsContainer');
  const decisionsEmpty = $('decisionsEmpty');
  const resetSettings = $('resetSettings');
  const langSelect = $('langSelect');
  const keyFillBtn = $('keyFillBtn');
  const editorUndo = $('editorUndo');
  const editorRedo = $('editorRedo');
  const editorPanel = $('editorPanel');
  const brushCursor = $('brushCursor');

  // 手动遮罩编辑器
  const editorCanvas = $('editorCanvas');
  const editorStatus = $('editorStatus');
  const toolPaint = $('toolPaint');
  const toolErase = $('toolErase');
  const brushSize = $('brushSize');
  const brushSizeVal = $('brushSizeVal');
  const editorResetAuto = $('editorResetAuto');
  const editorClear = $('editorClear');
  const editorApply = $('editorApply');
  const editorBlock = $('editorBlock');

  // 密钥存储分两层：
  //   session  —— 只要填过就存，关掉标签页即失效（默认行为）
  //   persist  —— 只有勾了「记住」才写入 localStorage，跨会话保留
  // 默认不勾选，避免"默认就把密钥长期留在浏览器里"。
  const KEY_SESSION = 'nsfw_api_key_session';
  const KEY_PERSIST = 'nsfw_api_key_persist';
  const KEY_REMEMBER_FLAG = 'nsfw_remember_key';
  const MAX_FILES_PER_RUN = 200;   // 文件夹批处理场景下 20 张太少

  let allResults = [];


  // ---------------------------------------------------------------- 密钥
  // 不存在任何内置默认密钥。
  let remember = localStorage.getItem(KEY_REMEMBER_FLAG) === '1';
  rememberKey.checked = remember;

  const storedKey =
    (remember ? localStorage.getItem(KEY_PERSIST) : null) ||
    sessionStorage.getItem(KEY_SESSION) ||
    '';
  if (storedKey) apiKeyInput.value = storedKey;

  function syncStoredKey(value) {
    if (value) {
      sessionStorage.setItem(KEY_SESSION, value);
    } else {
      sessionStorage.removeItem(KEY_SESSION);
    }
    if (remember && value) {
      localStorage.setItem(KEY_PERSIST, value);
    } else {
      localStorage.removeItem(KEY_PERSIST);
    }
    localStorage.setItem(KEY_REMEMBER_FLAG, remember ? '1' : '0');
  }

  function currentKey() {
    return apiKeyInput.value.trim();
  }

  keySaveBtn.addEventListener('click', () => {
    const value = currentKey();
    syncStoredKey(value);
    if (!value) {
      setStatus(t('key_cleared'), '');
    } else if (remember) {
      setStatus(t('key_saved_next'), 'ok');
    } else {
      setStatus(t('key_saved_session'), 'ok');
    }
  });

  rememberKey.addEventListener('change', () => {
    remember = rememberKey.checked;
    syncStoredKey(currentKey());
    if (remember && currentKey()) {
      setStatus(t('key_remembered'), 'ok');
    } else if (!remember) {
      setStatus(t('key_unremembered'), '');
    }
  });

  apiKeyInput.addEventListener('change', () => {
    syncStoredKey(currentKey());
  });

  // 一键填写：从服务端读本机密钥。该端点只响应 127.0.0.1 的请求，
  // 且服务默认只监听本机回环 —— 一旦改成对外监听，请在反代上封掉它。
  keyFillBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch('/api/local-key');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (!data.api_key) throw new Error('服务端没有密钥');
      apiKeyInput.value = data.api_key;
      syncStoredKey(currentKey());
      setStatus(t('key_filled'), 'ok');
    } catch (err) {
      setStatus(t('key_fill_fail') + err.message, 'error');
    }
  });


  // ---------------------------------------------------------------- 参数记忆
  // 所有可调项都存进 localStorage，下次打开保持一致。
  // 与密钥分开存 —— 密钥是否持久化由「记住」开关单独控制，两者互不影响。
  const SETTINGS_KEY = 'nsfw_settings_v1';

  const DEFAULTS = {
    mode: 'blur',
    intensity: '51',
    color: '#000000',
    ruleset: 'pixiv',
    sensitivity: 'max',
    maskInset: '15',
    padding: '3',
    shapeMask: 'shape',
    feather: '2',
  };

  function collectSettings() {
    const checked = document.querySelector('input[name="mode"]:checked');
    return {
      mode: checked ? checked.value : DEFAULTS.mode,
      intensity: intensity.value,
      color: solidColor.value,
      ruleset: ruleset.value,
      sensitivity: sensitivity.value,
      maskInset: maskInset.value,
      padding: padding.value,
      shapeMask: shapeMask.value,
      feather: feather.value,
    };
  }

  function applySettings(values) {
    const s = values || {};
    const mode = s.mode || DEFAULTS.mode;
    const radio = document.querySelector('input[name="mode"][value="' + mode + '"]');
    if (radio) {
      radio.checked = true;
      radio.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (s.intensity) intensity.value = s.intensity;
    intensityVal.textContent = intensity.value;
    if (s.color) solidColor.value = s.color;
    if (s.ruleset) ruleset.value = s.ruleset;
    if (s.sensitivity) sensitivity.value = s.sensitivity;
    if (s.maskInset) maskInset.value = s.maskInset;
    maskInsetVal.textContent = maskInset.value;
    if (s.padding) padding.value = s.padding;
    paddingVal.textContent = padding.value;
    if (s.shapeMask) shapeMask.value = s.shapeMask;
    if (s.feather) feather.value = s.feather;
    featherVal.textContent = feather.value;
  }

  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(collectSettings()));
    } catch (err) {
      /* 隐私模式下 localStorage 可能不可用，忽略即可 */
    }
  }

  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
    } catch (err) {
      return null;
    }
  }

  // 先恢复上次的设置，再挂监听，避免恢复过程本身触发一次保存
  applySettings(loadSettings());

  // range 控件在松手时才触发 change，正好合适做保存时机
  document
    .querySelectorAll(
      'input[name="mode"], #intensity, #sensitivity, #ruleset, #maskInset, #padding, #shapeMask, #feather, #solidColor'
    )
    .forEach((el) => el.addEventListener('change', saveSettings));

  // 语言切换：词条即时生效并记住选择
  langSelect.addEventListener('change', () => setLang(langSelect.value));
  applyLang();

  resetSettings.addEventListener('click', () => {
    applySettings(DEFAULTS);
    saveSettings();
    setStatus(t('settings_reset_ok'), 'ok');
  });


  // ------------------------------------------------------- 手动遮罩编辑器
  // 为什么要做这个：自动分割（GrabCut）靠颜色分布区分前景背景，
  // 当部位与周围皮肤颜色接近时它找不到边界，会返回整个检测框 ——
  // 用户看到的就是"还是矩形、还遮到周围皮肤"。
  // 所以给一条完全可控的路径：用户涂哪里就只打哪里。
  const EDITOR_MAX_SIDE = 1600;   // 编辑画布上限，兼顾精度与流畅度
  const ectx = editorCanvas.getContext('2d');
  let baseImage = null;
  let maskCanvas = null;
  let maskCtx = null;
  let overlayCanvas = null;
  let overlayCtx = null;
  let currentTaskId = null;
  let currentMaskUrl = null;
  let currentOutputName = null;
  let brushMode = 'paint';
  let painting = false;
  let lastPoint = null;
  let editorReady = false;

  // ---- 撤回 / 恢复 ----
  // 存 ImageData 快照而不是 PNG：恢复是同步的，笔画中间不会闪。
  // 1600x1200 一份约 7.7MB，上限 12 步约 90MB，桌面场景可接受。
  const HISTORY_LIMIT = 12;
  const undoStack = [];
  const redoStack = [];

  function currentMaskSnapshot() {
    return maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
  }

  function snapshotForUndo() {
    undoStack.push(currentMaskSnapshot());
    if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack.length = 0;
    updateHistoryButtons();
  }

  function applySnapshot(data) {
    maskCtx.putImageData(data, 0, 0);
    renderEditor();
  }

  function updateHistoryButtons() {
    editorUndo.disabled = undoStack.length === 0;
    editorRedo.disabled = redoStack.length === 0;
  }

  function undoMask() {
    if (!undoStack.length || !editorReady) return;
    redoStack.push(currentMaskSnapshot());
    applySnapshot(undoStack.pop());
    updateHistoryButtons();
  }

  function redoMask() {
    if (!redoStack.length || !editorReady) return;
    undoStack.push(currentMaskSnapshot());
    applySnapshot(redoStack.pop());
    updateHistoryButtons();
  }

  editorUndo.addEventListener('click', undoMask);
  editorRedo.addEventListener('click', redoMask);
  // Ctrl+Z / Ctrl+Y，和画图软件的习惯一致
  document.addEventListener('keydown', (event) => {
    if (!editorReady) return;
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      undoMask();
    } else if ((event.ctrlKey || event.metaKey) &&
               (event.key.toLowerCase() === 'y' ||
                (event.shiftKey && event.key.toLowerCase() === 'z'))) {
      event.preventDefault();
      redoMask();
    }
  });

  function setEditorStatus(text, ok) {
    editorStatus.textContent = text;
    editorStatus.style.color = ok === false ? '#fca5a5' : '';
  }

  function loadImage(src) {
    console.debug('[editor] loadImage 发起请求:', String(src).slice(0, 60));
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => {
        console.debug('[editor] loadImage onload', im.naturalWidth, 'x', im.naturalHeight);
        resolve(im);
      };
      im.onerror = () => {
        console.debug('[editor] loadImage onerror');
        reject(new Error('图片载入失败'));
      };
      im.src = src;
    });
  }

  /** 把画布内容重绘：底图 + 半透明红色遮罩 */
  function renderEditor() {
    if (!baseImage) return;
    const w = editorCanvas.width;
    const h = editorCanvas.height;
    ectx.clearRect(0, 0, w, h);
    ectx.drawImage(baseImage, 0, 0, w, h);

    // 用一份离屏画布把白色遮罩染成半透明红，再叠上去
    overlayCtx.clearRect(0, 0, w, h);
    overlayCtx.drawImage(maskCanvas, 0, 0, w, h);
    overlayCtx.globalCompositeOperation = 'source-in';
    overlayCtx.fillStyle = 'rgba(239, 68, 68, 0.45)';
    overlayCtx.fillRect(0, 0, w, h);
    overlayCtx.globalCompositeOperation = 'source-over';

    ectx.drawImage(overlayCanvas, 0, 0, w, h);
  }

  /** 把服务端的灰度掩膜（白=打码）转成 alpha 掩膜。
   *
   * 遮罩画布必须用 alpha 表示（涂过的地方不透明、其余全透明）：
   * 叠加红色提示层用的是 source-in，它按 alpha 混合 —— 如果画布是
   * 黑白不透明图，整张画布都会被染红，用户根本看不出涂了哪里。
   * 灰度图的亮度直接映射成 alpha，羽化的半透明边缘也能保留。
   */
  function importLuminanceMask(img) {
    const w = maskCanvas.width;
    const h = maskCanvas.height;
    maskCtx.clearRect(0, 0, w, h);
    maskCtx.drawImage(img, 0, 0, w, h);
    const frame = maskCtx.getImageData(0, 0, w, h);
    const px = frame.data;
    for (let i = 0; i < px.length; i += 4) {
      const lum = px[i];                     // 灰度图 R=G=B
      px[i] = 255;
      px[i + 1] = 255;
      px[i + 2] = 255;
      px[i + 3] = lum;                       // 亮度 → alpha
    }
    maskCtx.putImageData(frame, 0, 0);
  }

  function resetMaskFromImage(img, keepHistory) {
    if (!keepHistory && maskCanvas) snapshotForUndo();
    maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
    if (img) importLuminanceMask(img);
    renderEditor();
  }

  /** 导出成服务端认的格式：黑底 + 白色选区（灰度 PNG）。 */
  function exportMask() {
    const out = document.createElement('canvas');
    out.width = maskCanvas.width;
    out.height = maskCanvas.height;
    const octx = out.getContext('2d');
    octx.fillStyle = '#000';
    octx.fillRect(0, 0, out.width, out.height);
    octx.drawImage(maskCanvas, 0, 0);
    return out.toDataURL('image/png');
  }

  async function resetMaskToAuto() {
    if (!currentMaskUrl) {
      resetMaskFromImage(null);
      return;
    }
    try {
      const auto = await loadImage(currentMaskUrl);
      resetMaskFromImage(auto);
      setEditorStatus(t('editor_after_auto'));
    } catch (err) {
      resetMaskFromImage(null);
      setEditorStatus(t('editor_mask_fail'), false);
    }
  }

  async function initEditor(data) {
    editorReady = false;
    currentTaskId = data.id || null;
    currentMaskUrl = data.mask_url || null;
    // 取相对路径（可能带子文件夹），重新打码时据此覆盖同一份文件
    currentOutputName = (data.processed_url || '').split('/api/files/').pop().split('?')[0] || null;

    // 这些 console.debug 在平时不可见，打开 devtools 才能看到。
    // 留着它们：如果编辑器在大图上出问题，最后一条日志就是故障点。
    console.debug('[editor] 任务数据就绪', data.id);

    if (!data.original_url) {
      setEditorStatus(t('editor_no_original'), false);
      return;
    }

    setEditorStatus(t('editor_loading'));
    try {
      const im = await loadImage(data.original_url);
      console.debug('[editor] 原图已载入', im.naturalWidth, 'x', im.naturalHeight);
      baseImage = im;

      const scale = Math.min(1, EDITOR_MAX_SIDE / Math.max(im.naturalWidth, im.naturalHeight));
      const w = Math.max(1, Math.round(im.naturalWidth * scale));
      const h = Math.max(1, Math.round(im.naturalHeight * scale));

      editorCanvas.width = w;
      editorCanvas.height = h;
      maskCanvas = document.createElement('canvas');
      maskCanvas.width = w;
      maskCanvas.height = h;
      maskCtx = maskCanvas.getContext('2d');
      overlayCanvas = document.createElement('canvas');
      overlayCanvas.width = w;
      overlayCanvas.height = h;
      overlayCtx = overlayCanvas.getContext('2d');
      console.debug('[editor] 画布就绪', w, 'x', h);

      resetMaskFromImage(null);
      console.debug('[editor] 掩膜已清空');
      editorReady = true;
      setEditorStatus(t('editor_ready'));
      await resetMaskToAuto();
      console.debug('[editor] 初始化全部完成');
    } catch (err) {
      console.debug('[editor] 初始化失败', err && err.message);
      setEditorStatus('Load error: ' + err.message, false);
    }
  }

  function pointerPos(event) {
    const rect = editorCanvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: (event.clientX - rect.left) * (editorCanvas.width / rect.width),
      y: (event.clientY - rect.top) * (editorCanvas.height / rect.height),
    };
  }

  function strokeTo(point) {
    if (!lastPoint) {
      lastPoint = point;
    }
    const size = Number(brushSize.value) || 40;
    // 擦除用 destination-out 直接抠掉 alpha；涂抹则画不透明的白
    maskCtx.globalCompositeOperation =
      brushMode === 'erase' ? 'destination-out' : 'source-over';
    maskCtx.strokeStyle = '#fff';
    maskCtx.fillStyle = '#fff';
    maskCtx.lineWidth = size;
    maskCtx.lineCap = 'round';
    maskCtx.lineJoin = 'round';

    maskCtx.beginPath();
    maskCtx.moveTo(lastPoint.x, lastPoint.y);
    maskCtx.lineTo(point.x, point.y);
    maskCtx.stroke();
    // 单点点击时 lineTo 到同一点不会留下痕迹，补一个圆
    if (lastPoint.x === point.x && lastPoint.y === point.y) {
      maskCtx.beginPath();
      maskCtx.arc(point.x, point.y, size / 2, 0, Math.PI * 2);
      maskCtx.fill();
    }
    maskCtx.globalCompositeOperation = 'source-over';
    lastPoint = point;
    renderEditor();
  }

  editorCanvas.addEventListener('pointerdown', (event) => {
    if (!editorReady) return;
    event.preventDefault();
    // 合成事件（自动化测试）下 pointerId 可能不是活跃指针，捕获会抛异常。
    // 捕获只是为了拖出画布时仍能收到事件，失败不影响涂抹本身。
    try {
      editorCanvas.setPointerCapture(event.pointerId);
    } catch (err) {
      /* 忽略 */
    }
    painting = true;
    lastPoint = null;
    const p = pointerPos(event);
    if (p) {
      snapshotForUndo();
      strokeTo(p);
    }
  });

  editorCanvas.addEventListener('pointermove', (event) => {
    if (!painting) return;
    const p = pointerPos(event);
    if (p) strokeTo(p);
  });

  const endStroke = () => {
    painting = false;
    lastPoint = null;
  };
  editorCanvas.addEventListener('pointerup', endStroke);
  editorCanvas.addEventListener('pointercancel', endStroke);
  editorCanvas.addEventListener('pointerleave', () => {
    if (painting) endStroke();
  });

  brushSize.addEventListener('input', () => {
    brushSizeVal.textContent = brushSize.value;
    positionBrushCursor(lastBrushEvent);
  });

  // 笔刷光标：圆圈直径 = 笔刷大小，但要按画布"位图→屏幕"的缩放比例换算
  let lastBrushEvent = null;
  function positionBrushCursor(event) {
    if (!event) return;
    // 光标元素的定位基准是 .editor-stage（position:relative 的父级），
    // 不是画布本身 —— 画布在 stage 里是居中的，两者有偏移，
    // 之前按画布算导致圆圈总在真实指针左侧。
    const stage = editorCanvas.parentElement.getBoundingClientRect();
    const rect = editorCanvas.getBoundingClientRect();
    if (!stage.width || !rect.width) return;
    const scale = rect.width / editorCanvas.width;
    const size = Math.max(6, Number(brushSize.value) * scale);
    brushCursor.style.width = size + 'px';
    brushCursor.style.height = size + 'px';
    brushCursor.style.left = (event.clientX - stage.left) + 'px';
    brushCursor.style.top = (event.clientY - stage.top) + 'px';
  }

  editorCanvas.addEventListener('pointermove', (event) => {
    lastBrushEvent = event;
    if (!editorReady) return;
    positionBrushCursor(event);
    brushCursor.classList.remove('hidden');
  });
  editorCanvas.addEventListener('pointerleave', () => {
    brushCursor.classList.add('hidden');
  });
  editorCanvas.addEventListener('pointerdown', (event) => {
    lastBrushEvent = event;
  });

  function selectTool(mode) {
    brushMode = mode;
    toolPaint.classList.toggle('active', mode === 'paint');
    toolErase.classList.toggle('active', mode === 'erase');
  }
  toolPaint.addEventListener('click', () => selectTool('paint'));
  toolErase.addEventListener('click', () => selectTool('erase'));

  editorResetAuto.addEventListener('click', resetMaskToAuto);
  editorClear.addEventListener('click', () => {
    resetMaskFromImage(null);
    setEditorStatus(t('editor_cleared'));
  });

  editorApply.addEventListener('click', async () => {
    if (!editorReady || !currentTaskId) return;
    const key = currentKey();
    if (!key) {
      setStatus(t('need_key'), 'bad');
      return;
    }

    editorBlock.classList.add('editor-busy');
    setEditorStatus(t('editor_applying'));
    try {
      const resp = await fetch('/api/remask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-KEY': key },
        body: JSON.stringify({
          id: currentTaskId,
          mask: exportMask(),
          output_name: currentOutputName,
          mode: document.querySelector('input[name="mode"]:checked').value,
          intensity: Number(intensity.value),
          color: solidColor.value,
          feather: Number(feather.value),
        }),
      });

      if (!resp.ok) {
        let detail = 'HTTP ' + resp.status;
        try {
          const body = await resp.json();
          if (body && body.detail) detail = body.detail;
        } catch (err) { /* 忽略解析失败 */ }
        throw new Error(detail);
      }

      const data = await resp.json();
      // remask 覆盖同一输出文件，加时间戳防止 WebView/浏览器用缓存旧图
      const bust = data.processed_url + (data.processed_url.includes('?') ? '&' : '?') + 'r=' + Date.now();
      processedImg.src = bust;
      downloadBtn.href = bust;
      setEditorStatus(
        '已按你的遮罩重新打码，覆盖 ' + Number(data.masked_pixels).toLocaleString() + ' 个像素。'
      );
      setStatus(t('remask_ok'), 'ok');
    } catch (err) {
      setEditorStatus(t('remask_fail_with') + err.message, false);
      setStatus(t('remask_fail'), 'bad');
    } finally {
      editorBlock.classList.remove('editor-busy');
    }
  });


  // ------------------------------------------------------------ 状态提示
  function setStatus(text, kind) {
    status.textContent = '';
    if (kind === 'busy') {
      const spin = document.createElement('span');
      spin.className = 'spinner';
      status.appendChild(spin);
    }
    status.appendChild(document.createTextNode(text));
    status.className = 'status' + (kind ? ' ' + kind : '');
  }


  // -------------------------------------------------------------- 交互
  document.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener('change', (event) => {
      colorPickerContainer.classList.toggle('hidden', event.target.value !== 'solid');
    });
  });

  intensity.addEventListener('input', (event) => {
    intensityVal.textContent = event.target.value;
  });

  padding.addEventListener('input', (event) => {
    paddingVal.textContent = event.target.value;
  });

  maskInset.addEventListener('input', (event) => {
    maskInsetVal.textContent = event.target.value;
  });

  feather.addEventListener('input', (event) => {
    featherVal.textContent = event.target.value;
  });

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });

  // ---- 拖拽智能识别：文件或文件夹（递归遍历子目录）----
  function entryToFile(entry) {
    return new Promise((resolve, reject) => {
      entry.file(resolve, reject);
    });
  }

  function readAllEntries(reader) {
    return new Promise((resolve, reject) => {
      const out = [];
      const step = () => {
        reader.readEntries((batch) => {
          if (batch.length === 0) { resolve(out); return; }
          out.push(...batch);
          step();                       // 必须反复调用直到返回空，否则只拿到前几个
        }, reject);
      };
      step();
    });
  }

  async function walkDirectory(dirEntry, prefix, out) {
    for (const child of await readAllEntries(dirEntry.createReader())) {
      if (child.isFile) {
        try {
          out.push({ file: await entryToFile(child), folder: prefix });
        } catch (err) { /* 单个文件读不出就跳过 */ }
      } else if (child.isDirectory) {
        await walkDirectory(child, prefix || child.name, out);
      }
    }
  }

  async function collectFromDataTransfer(dataTransfer) {
    const picked = [];
    let folderName = null;
    const rawItems = Array.from(dataTransfer.items || []);
    const entries = [];
    for (const item of rawItems) {
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry) entries.push(entry);
      else {
        const f = item.getAsFile();
        if (f) picked.push({ file: f, folder: null });
      }
    }
    for (const entry of entries) {
      if (entry.isFile) {
        try { picked.push({ file: await entryToFile(entry), folder: null }); }
        catch (err) { /* 忽略 */ }
      } else if (entry.isDirectory) {
        if (!folderName) folderName = entry.name;
        await walkDirectory(entry, entry.name, picked);
      }
    }
    return { picked: picked.filter((p) => /\.(jpe?g|png|webp)$/i.test(p.file.name)), folderName };
  }

  ['dragenter', 'dragover'].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add('dragging');
    });
  });
  ['dragleave', 'dragend'].forEach((name) => {
    dropzone.addEventListener(name, () => dropzone.classList.remove('dragging'));
  });
  dropzone.addEventListener('drop', async (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragging');
    const { picked } = await collectFromDataTransfer(event.dataTransfer);
    if (picked.length === 0) {
      setStatus(t('drop_no_images'), 'error');
      return;
    }
    // 每个文件带着自己所属的顶层文件夹名 —— 同时拖多个文件夹时
    // 各归各的 output/<文件夹名>/，不再全部挤进第一个的名字里
    handleUpload(picked);
  });

  fileInput.addEventListener('change', (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length > 0) handleUpload(files.map((f) => ({ file: f, folder: null })));
  });

  // ------------------------------------------------ 批量下载全部打码结果
  function relFromUrl(url) {
    return (url || '').split('/api/files/').pop().split('?')[0];
  }

  function triggerDownload(url, name) {
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  downloadAllBtn.addEventListener('click', () => {
    const items = allResults.filter((r) => r.data && r.data.processed_url);
    if (!items.length) {
      // 还没有任何成功结果：直接给出提示
      setStatus(t('download_all_none'), 'bad');
      flashButton(t('download_all_none'), true);
      if (window.AndroidSaver && window.AndroidSaver.notify) window.AndroidSaver.notify(t('download_all_none'));
      return;
    }

    if (window.AndroidSaver && typeof window.AndroidSaver.save === 'function') {
      // 手机 App：走 JS 桥逐张同步保存到相册，精确统计成败
      let ok = 0;
      const fails = [];
      items.forEach(({ data, file }) => {
        try {
          const rel = relFromUrl(data.processed_url);
          const name = 'censored_' + (data.filename || (file && file.name) || 'image.png');
          const r = window.AndroidSaver.save(rel, name);
          if (r === 'OK') ok += 1;
          else fails.push((data.filename || '图片') + '：' + r);
        } catch (err) {
          fails.push((data.filename || '图片') + '：' + (err && err.message ? err.message : '保存异常'));
        }
      });
      let msg;
      if (fails.length) {
        msg = t('download_all_partial', { ok: ok, fail: fails.length }) + '  ' + fails.slice(0, 2).join('；');
        setStatus(msg, 'bad');
      } else {
        msg = t('download_all_done', { n: ok });
        setStatus(msg, 'ok');
      }
      // 反馈给到点击处：按钮文字临时变为结果 + 系统 Toast 全局弹出
      flashButton(msg, fails.length > 0);
      if (window.AndroidSaver.notify) window.AndroidSaver.notify(msg);
    } else {
      // 桌面浏览器：逐个触发浏览器下载，文件名带原名区分
      items.forEach(({ data, file }, i) => {
        const name = 'censored_' + (data.filename || (file && file.name) || 'image.png');
        setTimeout(() => triggerDownload(data.processed_url, name), i * 200);
      });
      const msg = t('download_all_started', { n: items.length });
      setStatus(msg, 'ok');
      flashButton(msg, false);
    }
  });

  // 按钮内联反馈：文字临时换成结果，2.5 秒后复原（失败态变红）
  let flashTimer = null;
  function flashButton(msg, isError) {
    if (flashTimer) clearTimeout(flashTimer);
    downloadAllBtn.textContent = msg.length > 24 ? msg.slice(0, 24) + '…' : msg;
    downloadAllBtn.classList.toggle('flash-error', isError);
    flashTimer = setTimeout(() => {
      downloadAllBtn.textContent = t('download_all');
      downloadAllBtn.classList.remove('flash-error');
      flashTimer = null;
    }, 2500);
  }

  resetBtn.addEventListener('click', () => {
    results.classList.add('hidden');
    batchPanel.classList.add('hidden');
    fileInput.value = '';
    processedImg.removeAttribute('src');
    fileNameDisplay.textContent = '';
    summaryInfo.textContent = '';
    scoresContainer.textContent = '';
    decisionsContainer.textContent = '';
    decisionsEmpty.classList.remove('hidden');
    decisionsEmpty.textContent =
      '模型检出的每一个部位及其处置结果。若某个位置该遮没遮，这里会写明原因（比如「分数低于门槛」），据此调整上方灵敏度即可。';
    queueList.textContent = '';

    // 清掉编辑器状态，避免把上一张图的遮罩用在新图上
    editorReady = false;
    currentTaskId = null;
    currentMaskUrl = null;
    currentOutputName = null;
    editorPanel.classList.add('hidden');
    undoStack.length = 0;
    redoStack.length = 0;
    updateHistoryButtons();
    baseImage = null;
    if (ectx && editorCanvas.width) {
      ectx.clearRect(0, 0, editorCanvas.width, editorCanvas.height);
    }
    setEditorStatus(t('editor_idle'));
    allResults = [];
    setStatus(t('status_ready'), '');
  });


  // ---------------------------------------------------------- 上传与处理
  async function handleUpload(entries) {
    // entries: [{ file, folder }]，folder 为顶层文件夹名（点击多选时为 null）
    if (!entries || entries.length === 0) return;

    if (!currentKey()) {
      setStatus(t('need_key'), 'error');
      apiKeyInput.focus();
      return;
    }

    let files = Array.from(entries);
    if (files.length > MAX_FILES_PER_RUN) {
      files = files.slice(0, MAX_FILES_PER_RUN);
      setStatus(t('too_many_files', { n: MAX_FILES_PER_RUN }), 'error');
    }

    allResults = [];
    queueList.textContent = '';
    batchPanel.classList.remove('hidden');
    results.classList.add('hidden');
    queueCount.textContent = files.length + ' ' + t('files_unit');

    const items = files.map((file, index) => {
      const el = document.createElement('div');
      el.className = 'queue-item';
      el.id = 'queue-item-' + index;

      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = t('pending');
      el.appendChild(tag);

      queueList.appendChild(el);
      return { el: el, tag: tag };
    });

    const mode = document.querySelector('input[name="mode"]:checked').value;
    const color = solidColor.value;
    const sensValue = sensitivity.value;
    const paddingValue = padding.value;
    const shapeValue = shapeMask.value;
    const featherValue = feather.value;

    for (let i = 0; i < files.length; i += 1) {
      const { file, folder } = files[i];
      const slot = items[i];

      slot.tag.textContent = t('processing');
      setStatus(t('processing_n', { i: i + 1, total: files.length, name: file.name }), 'busy');

      const formData = new FormData();
      formData.append('file', file);
      formData.append('mode', mode);
      formData.append('intensity', intensity.value);
      formData.append('color', color);
      formData.append('sensitivity', sensValue);
      formData.append('mask_inset', maskInset.value);
      formData.append('padding', paddingValue);
      formData.append('shape_mask', shapeValue === 'shape' ? '1' : '0');
      formData.append('feather', featherValue);
      if (folder) formData.append('subfolder', folder);
      formData.append('ruleset', ruleset.value);

      try {
        let response;
        // 文件夹批处理是逐张发请求，偶发 429 就退避重试，
        // 别让第 31 张因为限流直接标成失败。
        for (let attempt = 0; ; attempt += 1) {
          response = await fetch('/api/process', {
            method: 'POST',
            headers: { 'X-API-KEY': currentKey() },
            body: formData
          });
          if (response.status !== 429 || attempt >= 3) break;
          const wait = 4000 * (attempt + 1);
          setStatus(t('rate_limit_retry', { sec: Math.round(wait / 1000), attempt: attempt + 1 }), 'busy');
          await new Promise((r) => setTimeout(r, wait));
        }

        if (!response.ok) {
          throw new Error(await describeError(response));
        }

        const data = await response.json();
        allResults.push({ data: data, file: file });

        slot.el.classList.add('ready');
        slot.el.textContent = '';
        const thumb = document.createElement('img');
        thumb.src = data.processed_url;
        thumb.alt = '';
        slot.el.appendChild(thumb);

        const tag = document.createElement('span');
        tag.className = 'tag ok';
        tag.textContent = data.blur_count > 0 ? t('tag_censored') : t('tag_clean');
        slot.el.appendChild(tag);

        slot.el.addEventListener('click', () => displayResult(data, file));

        if (i === 0) displayResult(data, file);
      } catch (error) {
        slot.el.textContent = '';
        const tag = document.createElement('span');
        tag.className = 'tag bad';
        const reason = (error && error.message) ? String(error.message) : '';
        // 手机端没有控制台：失败原因直接写在卡片上，截个图就能反馈
        tag.textContent = reason ? ('失败: ' + reason.slice(0, 80)) : t('tag_failed');
        tag.title = reason;
        slot.el.appendChild(tag);
        setStatus(reason || t('tag_failed'), 'bad');
        console.error('process failed', error);
      }
    }

    setStatus(t('batch_done', { ok: allResults.length, total: files.length }), 'ok');
  }

  async function describeError(response) {
    let detail = '';
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      }
    } catch (error) {
      detail = '';
    }
    if (response.status === 403) return t('error_403');
    if (response.status === 413) return detail || t('error_413');
    if (response.status === 429) return t('error_429');
    return (response.status + ' ' + (detail || response.statusText)).trim();
  }


  // ------------------------------------------------------------ 结果展示
  function displayResult(data, originalFile) {
    results.classList.remove('hidden');
    results.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    fileNameDisplay.textContent = originalFile.name;
    setStatus(t('analysis_done'), 'ok');

    // 原图预览：直接用服务端签发的地址，而不是把整个文件转成 data URL。
    // data URL 会把文件体积放大 1.37 倍并在内存里同时持有字符串与位图，
    // 大图时与编辑器自己的图片加载叠加，曾把 headless 渲染进程压崩。

    skeleton.classList.remove('hidden');
    processedImg.style.opacity = '0';
    blurOverlay.style.opacity = '0';

    const probe = new Image();
    probe.onload = () => {
      processedImg.src = probe.src;
      processedImg.style.opacity = '1';
      skeleton.classList.add('hidden');
      blurOverlay.style.opacity = data.blur_count > 0 ? '1' : '0';
    };
    probe.onerror = () => {
      skeleton.classList.add('hidden');
      setStatus(t('result_img_fail'), 'error');
    };
    probe.src = data.processed_url;
    downloadBtn.href = data.processed_url;
    downloadBtn.setAttribute('download', 'censored_' + originalFile.name);

    renderScores(data.scores);
    renderDecisions(data.decisions);

    const censored = data.blur_count > 0;
    blurLabel.textContent = censored ? t('tag_censored') : t('result_none');
    blurLabel.className = 'pill ' + (censored ? 'bad' : 'ok');

    const decisions = data.decisions || [];
    // 检出了"规则范围内"的部位、但按当前策略判定为无需遮挡 —— 这类最可能是
    // 用户眼里的"漏检"，必须显式提示，否则用户只会觉得"没识别到"。
    // in_scope 由后端按当前打码规则给出，规则外的部位（如 pixiv 规则下的臀部）
    // 不算漏检，不该提示。
    const skippedSensitive = decisions.filter(
      (d) => !d.censored && d.in_scope === true
    );

    if (censored) {
      summaryInfo.textContent = t('summary_detected', {
        d: data.detections.length,
        b: data.blur_count,
      });
    } else {
      summaryInfo.textContent = t('summary_none');
    }
    if (skippedSensitive.length > 0) {
      summaryInfo.textContent += t('summary_skipped', { n: skippedSensitive.length });
    }

    // 轮廓贴合生效时，把"省下来多少面积"明确告诉用户
    const contoured = decisions.filter((d) => d.censored && d.shape === 'contour');
    if (contoured.length > 0) {
      const avg =
        contoured.reduce((sum, d) => sum + (Number(d.mask_ratio) || 0), 0) /
        contoured.length;
      summaryInfo.textContent += t('summary_contour', { pct: Math.round(avg * 100) });
    }

    // 载入手动编辑所需的原图与自动遮罩
    editorPanel.classList.remove('hidden');
    initEditor(data);
  }

  // 逐个检测框的处置结果。这一块是专门为"我明明看到有，为什么没遮住"
  // 这类排查需求加的：既显示已打码的，也显示被策略跳过的以及跳过原因。
  function renderDecisions(decisions) {
    decisionsContainer.textContent = '';
    const list = decisions || [];

    if (list.length === 0) {
      decisionsEmpty.classList.remove('hidden');
      decisionsEmpty.textContent = t('decisions_none');
      return;
    }

    decisionsEmpty.classList.add('hidden');

    list.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'decision ' + (item.censored ? 'censored' : 'skipped');

      const label = document.createElement('span');
      label.className = 'dlabel';
      label.textContent = tLabel(item.label);

      const score = document.createElement('span');
      score.className = 'dscore';
      score.textContent = (Number(item.score) * 100).toFixed(0) + '%';

      const reason = document.createElement('span');
      reason.className = 'dreason';
      reason.textContent = explainReason(item.reason);

      row.appendChild(label);
      row.appendChild(score);

      if (item.censored) {
        const shape = document.createElement('span');
        shape.className = 'dshape';
        const pct =
          typeof item.mask_ratio === 'number'
            ? Math.round(item.mask_ratio * 100) + '%'
            : '';
        shape.textContent =
          (item.shape === 'contour' ? t('shape_contour_short') : t('shape_rect_short')) + ' ' + pct;
        row.appendChild(shape);
      }

      row.appendChild(reason);
      decisionsContainer.appendChild(row);
    });
  }

  function renderScores(scores) {
    scoresContainer.textContent = '';
    if (!scores || scores.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'hint';
      empty.textContent = t('scores_empty');
      scoresContainer.appendChild(empty);
      return;
    }

    scores.slice().sort((a, b) => b.score - a.score).forEach((entry) => {
      const pct = (entry.score * 100).toFixed(1);

      const row = document.createElement('div');
      row.className = 'score-row';

      const head = document.createElement('div');
      head.className = 'score-head';

      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = tLabel(entry.label);

      const value = document.createElement('span');
      value.textContent = pct + '%';

      head.appendChild(name);
      head.appendChild(value);

      const bar = document.createElement('div');
      bar.className = 'score-bar';

      const fill = document.createElement('div');
      fill.className = 'score-fill ' + scoreClass(entry.label);
      fill.style.width = Math.max(1, Math.min(100, Number(pct))) + '%';

      bar.appendChild(fill);
      row.appendChild(head);
      row.appendChild(bar);
      scoresContainer.appendChild(row);
    });
  }

  function scoreClass(label) {
    const name = String(label).toLowerCase();
    if (name === 'normal' || name === 'neutral' || name === 'drawings') return 'f-safe';
    if (name === 'sexy') return 'f-warn';
    if (name === 'porn' || name === 'hentai' || name === 'nsfw' || name === 'unsafe') return 'f-bad';
    return 'f-other';
  }

  // 初始化完成标记。写在最后一行，意味着上面所有元素查找与事件绑定都成功了。
  // 用途：在浏览器控制台执行 document.documentElement.dataset.appReady
  // 可快速判断"页面能打开但点不动"到底是脚本没执行，还是脚本执行了但逻辑有问题。
  document.documentElement.dataset.appReady = 'true';
})();
