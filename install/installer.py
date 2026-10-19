"""NSFW Mask - single-file graphical setup wizard.

All program files (backend/frontend/desktop_app.py/requirements) are embedded
in this exe (PyInstaller --add-data). On install it extracts them to the
chosen path, creates a venv with the system Python, installs dependencies
(Tsinghua mirror), generates the API key, optionally creates a desktop
shortcut, and can launch the app right away.

Unattended self-test mode:
    NSFW-Mask-Setup.exe --auto gpu|cpu --root "D:\\target\\dir" [--no-shortcut]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser

import webview

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PYTHON_DOWNLOAD = "https://www.python.org/downloads/"

# 无控制台 exe 的子进程：显式空 stdin + 禁止新建控制台窗口
# （继承无效 stdio 句柄会让控制台子进程 DLL 初始化失败 0xC0000142）
SUBPROCESS_KWARGS = {
    "stdin": subprocess.DEVNULL,
    "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
}

# 内嵌资源根（onefile 运行时为解压目录；源码运行时为项目根）
if getattr(sys, "frozen", False):
    RESOURCE_BASE = sys._MEIPASS
else:
    RESOURCE_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EMBED_ITEMS = [
    "backend",
    "frontend",
    "desktop_app.py",
    "requirements.txt",
    "requirements-gpu.txt",
    "app.ico",
    "NSFW-Mask.exe",
]

TARGET_DEFAULT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NSFW-Mask"
)


def get_target() -> str:
    if "--root" in sys.argv:
        return os.path.abspath(sys.argv[sys.argv.index("--root") + 1])
    return TARGET_DEFAULT


TARGET = get_target()
AUTO = "--auto" in sys.argv


def log_ui(line: str, kind: str = "info") -> None:
    if AUTO:
        print(line)
    try:
        payload = json.dumps(f"[{kind}] {line}")
        for w in webview.windows:
            w.evaluate_js(f"appendLog({payload}, {json.dumps(kind)})")
    except Exception:
        pass


def phase_ui(phase: str, text: str = "") -> None:
    try:
        for w in webview.windows:
            w.evaluate_js(f"setPhase({json.dumps(phase)}, {json.dumps(text)})")
    except Exception:
        pass


def find_python() -> tuple[str | None, str]:
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        exe = cmd[0]
        if not shutil.which(exe):
            continue
        try:
            out = subprocess.run(
                cmd + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                capture_output=True, text=True, timeout=20,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ver = (out.stdout or "").strip()
            major, _, minor = ver.partition(".")
            if ver and (int(major), int(minor or 0)) >= (3, 10):
                return " ".join(cmd), ver
        except Exception:
            continue
    return None, ""


def python_too_new(ver: str) -> bool:
    """3.13+ 部分依赖（torch 等）可能尚无预编译包，安装易失败。"""
    try:
        major, minor = (int(x) for x in ver.split(".")[:2])
        return (major, minor) > (3, 12)
    except Exception:
        return False


def detect_gpu() -> bool:
    return bool(shutil.which("nvidia-smi"))


def extract_program_files(target: str, include_640m: bool = False) -> None:
    log_ui(f"释放程序文件到 {target}")
    items = list(EMBED_ITEMS)
    if include_640m:
        # 640m 大模型内嵌在安装器里（选增强版才释放，体积 +99MB）
        items.append(os.path.join("models", "640m.onnx"))
    for item in items:
        src = os.path.join(RESOURCE_BASE, item)
        dst = os.path.join(target, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    log_ui(f"程序文件释放完成（{len(items)} 项）")


def create_shortcut(target: str) -> None:
    """桌面快捷方式：直接指向 NSFW-Mask.exe 启动器，图标 app.ico。"""
    try:
        desktop = os.path.join(
            os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop"
        )
        lnk = os.path.join(desktop, "NSFW Mask.lnk")
        launcher = os.path.join(target, "NSFW-Mask.exe")
        ico = os.path.join(target, "app.ico")
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            f"$s.TargetPath='{launcher}';"
            f"$s.WorkingDirectory='{target}';"
            f"$s.IconLocation='{ico}';"
            "$s.Save()"
        )
        shell_cmd = ["powershell", "-NoProfile", "-Command", ps]
        subprocess.run(shell_cmd, capture_output=True, timeout=30,
                       stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log_ui("已创建桌面快捷方式", "ok")
    except Exception as exc:
        log_ui(f"快捷方式创建失败（不影响使用）：{exc}", "error")


def launch_app(target: str) -> None:
    launcher = os.path.join(target, "NSFW-Mask.exe")
    if os.path.exists(launcher):
        subprocess.Popen([launcher], cwd=target, close_fds=True)
        log_ui("NSFW Mask 已启动", "ok")
        return
    pythonw = os.path.join(target, "venv", "Scripts", "pythonw.exe")
    app_py = os.path.join(target, "desktop_app.py")
    if os.path.exists(pythonw) and os.path.exists(app_py):
        subprocess.Popen([pythonw, app_py], cwd=target, close_fds=True)
        log_ui("NSFW Mask 已启动", "ok")


class Api:
    def choose_dir(self) -> str:
        """弹系统文件夹选择框（PowerShell 子进程，稳定可靠）。"""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$f.Description = '选择 NSFW Mask 安装位置';"
            "$f.ShowNewFolderButton = $true;"
            "$f.ShowDialog() | Out-Null; $f.SelectedPath"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True, text=True, timeout=120,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            path = (out.stdout or "").strip()
            return path
        except Exception as exc:
            log_ui(f"打开文件夹选择框失败：{exc}", "error")
            return ""

    def env_check(self) -> str:
        py, ver = find_python()
        return json.dumps({
            "python_found": py is not None,
            "python_ver": ver,
            "python_warn": python_too_new(ver) if py else False,
            "gpu": detect_gpu(),
            "default_path": TARGET,
        })

    def open_python_download(self) -> None:
        webbrowser.open(PYTHON_DOWNLOAD)

    def start_install(self, gpu: bool = False, path: str = "", shortcut: bool = True,
                      run_after: bool = True, model: str = "320n") -> str:
        threading.Thread(
            target=_run_install,
            args=(bool(gpu), path or TARGET, bool(shortcut), bool(run_after),
                  model if model in ("320n", "640m") else "320n"),
            daemon=True,
        ).start()
        return "started"

    def run_app(self, path: str = "") -> str:
        launch_app(path or TARGET)
        return "ok"


def _run_install(gpu: bool, target: str, shortcut: bool, run_after: bool,
                 model: str = "320n") -> None:
    try:
        _install(gpu, model, target, shortcut, run_after)
    except Exception as exc:
        log_ui(f"安装失败：{exc}", "error")
        phase_ui("error", str(exc))


def _run_step(title: str, cmd: list[str], cwd: str | None = None) -> tuple[int, list[str]]:
    """执行子命令，实时推送日志。返回 (退出码, 最后几行输出)。"""
    log_ui("$ " + " ".join(cmd), "cmd")
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        text = line.rstrip()
        tail.append(text)
        if len(tail) > 8:
            tail.pop(0)
        log_ui(text)
    code = proc.wait()
    return code, tail


def _download_640m(target: str) -> bool:
    """下载 640m.onnx（约 100 MB）到 <target>/models/。失败返回 False（降级 320n）。

    多源轮询：GitHub 直连在部分网络下会被重定向到登录页（49KB HTML），
    依次尝试官方源与加速镜像，用体积校验拦截坏响应。
    """
    import tempfile
    import urllib.request

    base = "https://github.com/notAI-tech/NudeNet/releases/download/v3.4-weights/640m.onnx"
    url_list = [
        base,
        "https://ghproxy.net/" + base,
        "https://gh-proxy.com/" + base,
    ]
    dest = os.path.join(target, "models", "640m.onnx")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 10 * 1024 * 1024:
        log_ui("640m 模型已存在，跳过下载", "ok")
        return True

    log_ui("$ 下载 640m.onnx（约 100 MB，取决于网络可能较慢）", "cmd")
    total = 0
    ok = False
    final_tmp = None
    for idx, url in enumerate(url_list):
        tmp_path = None
        try:
            log_ui(f"  尝试下载源：{url.split('/')[2]}")
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NSFW-Mask"}
            )
            tmp_path = os.path.join(target, "models", f".640m.part{idx}")
            total = 0
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if total % (10 * 1024 * 1024) < 512 * 1024:
                        log_ui(f"  已下载 {total / 1024 / 1024:.0f} MB")
            if total < 10 * 1024 * 1024:
                log_ui(f"  该源返回异常内容（{total} 字节），换下一个源…", "error")
                os.unlink(tmp_path)
                tmp_path = None
                continue
            ok = True
            final_tmp = tmp_path
            break
        except Exception as exc:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            log_ui(f"  该源失败：{exc}", "error")
            continue
    if not ok:
        log_ui("640m 模型下载失败：所有下载源均不可用", "error")
        log_ui("安装将继续使用 320n 轻量模型（功能完整，检出稍弱）。", "error")
        log_ui("稍后可重新运行本安装器选择 640m，或手动下载 640m.onnx 放入 models\\ 目录。", "error")
        return False
    os.replace(final_tmp, dest)
    log_ui(f"640m 模型下载完成（{total / 1024 / 1024:.0f} MB）", "ok")
    return True


def _install(gpu: bool, model: str, target: str, shortcut: bool, run_after: bool) -> None:
    os.makedirs(target, exist_ok=True)

    phase_ui("extract", "释放程序文件…")
    extract_program_files(target, include_640m=(model == "640m"))

    py, ver = find_python()
    if not py:
        phase_ui("error", "未找到 Python 3.10+")
        log_ui("未检测到 Python 3.10+，请先安装 Python 并勾选 Add to PATH。", "error")
        return
    log_ui(f"使用 Python {ver}（{py}）")

    phase_ui("venv", "创建虚拟环境…")
    venv_dir = os.path.join(target, "venv")
    py_parts = py.split(" ")
    _run_step("创建 venv", py_parts + ["-m", "venv", venv_dir], cwd=target)
    vpy = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(vpy):
        raise RuntimeError("venv 创建异常：未找到 venv\\Scripts\\python.exe")

    phase_ui("pip", "安装依赖（几分钟，取决于网络）…")
    req = "requirements-gpu.txt" if gpu else "requirements.txt"
    code, tail = _run_step("安装依赖",
                           [vpy, "-m", "pip", "install", "-r", req, "-i", PIP_MIRROR],
                           cwd=target)
    if code != 0:
        log_ui("镜像源安装失败，改用官方源重试…", "error")
        code, tail = _run_step("安装依赖（官方源）",
                               [vpy, "-m", "pip", "install", "-r", req],
                               cwd=target)
        if code != 0:
            raise RuntimeError("依赖安装失败，pip 输出末尾：\n" + "\n".join(tail))

    if model == "640m":
        builtin = os.path.join(target, "models", "640m.onnx")
        if os.path.exists(builtin) and os.path.getsize(builtin) > 10 * 1024 * 1024:
            phase_ui("model", "检测模型：640m 增强版（安装器内置，无需下载）")
            log_ui("已内置 640m 检测模型（约 99 MB），离线即用。", "ok")
        else:
            # 旧版安装器未内嵌时走网络下载兜底
            phase_ui("model", "下载 640m 检测模型（约 100 MB，检出更强）…")
            if not _download_640m(target):
                log_ui("已降级为 320n 轻量模型（安装继续，不影响使用）。", "error")

    phase_ui("key", "生成 API 密钥…")
    key_script = (
        "from backend import config; from backend.security import SecretStore; "
        "config.ensure_dirs(); "
        "SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE).api_key"
    )
    _run_step("生成密钥", [vpy, "-c", key_script], cwd=target)

    if shortcut:
        phase_ui("shortcut", "创建桌面快捷方式…")
        create_shortcut(target)

    phase_ui("done", "安装完成")
    log_ui(f"全部完成！安装位置：{target}", "ok")
    if run_after:
        log_ui("正在启动 NSFW Mask…", "ok")
        launch_app(target)


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing:border-box; }
  body { margin:0; height:100vh; display:flex; flex-direction:column;
         background:#0f172a; color:#e2e8f0;
         font-family:"Segoe UI","Microsoft YaHei",sans-serif; }
  header { padding:18px 24px 10px; flex:none; }
  h1 { font-size:20px; font-weight:500; margin:0 0 2px; }
  h1 span { color:#94a3b8; font-size:13px; margin-left:8px; }
  header p { margin:0; color:#94a3b8; font-size:12px; }
  main { flex:1; display:flex; flex-direction:column; padding:0 24px 16px;
         gap:12px; min-height:0; overflow-y:auto; }
  .card { background:#111c33; border:1px solid #1e2d4d; border-radius:10px;
          padding:14px 16px; }
  .row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .tag { font-size:12px; padding:2px 10px; border-radius:20px;
         background:#1e293b; color:#94a3b8; }
  .tag.ok { background:#052e1c; color:#34d399; }
  .tag.bad { background:#3b0d12; color:#f87171; }
  .tag.warn { background:#3b2f0d; color:#fbbf24; }
  .btn { border:1px solid #2b426b; background:#16233f; color:#e2e8f0;
         padding:8px 18px; border-radius:8px; font-size:14px; cursor:pointer; }
  .btn:hover { background:#1d2f55; }
  .btn.primary { background:#2563eb; border-color:#2563eb; }
  .btn.primary:hover { background:#1d4ed8; }
  .btn:disabled { opacity:.45; cursor:not-allowed; }
  .btn.small { padding:6px 12px; font-size:13px; }
  .phase { font-size:14px; color:#93c5fd; min-height:20px; }
  #log { height:150px; flex:none; background:#0a1120; border:1px solid #1e2d4d;
         border-radius:10px; padding:10px 12px; overflow-y:auto;
         font:12px/1.5 Consolas,monospace; color:#9fb3c8; white-space:pre-wrap;
         word-break:break-all; }
  #log .error { color:#f87171; }
  #log .ok { color:#34d399; }
  #log .cmd { color:#7dd3fc; }
  input[type=text] { flex:1; min-width:280px; background:#0a1120;
                     border:1px solid #2b426b; border-radius:8px;
                     color:#e2e8f0; padding:8px 10px; font-size:13px; }
  label.chk { display:flex; align-items:center; gap:6px; font-size:13px;
              color:#cbd5e1; cursor:pointer; }
  label.chk input { accent-color:#2563eb; }
  .note { color:#64748b; font-size:12px; }
  .hidden { display:none; }
  a { color:#60a5fa; }
</style></head><body>
<header>
  <h1>NSFW Mask 安装向导<span>by yumng</span></h1>
  <p>单文件安装器：释放程序、创建运行环境、安装依赖（首次安装约需 2-5 GB 磁盘空间）</p>
</header>
<main>
  <div class="card">
    <div class="row" id="envRow"><span class="tag">正在检测环境…</span></div>
  </div>
  <div class="card" id="pathCard">
    <div class="row">
      <span style="font-size:14px;flex:none">安装位置：</span>
      <input type="text" id="installPath">
      <button class="btn small" onclick="pickDir()">浏览…</button>
    </div>
  </div>
  <div class="card" id="optCard">
    <div class="row">
      <span style="font-size:14px;flex:none">显卡模式：</span>
      <button class="btn" id="btnGpu">NVIDIA GPU 加速</button>
      <button class="btn" id="btnCpu">仅 CPU</button>
      <span class="note" id="gpuNote"></span>
    </div>
    <div class="row" style="margin-top:10px">
      <span style="font-size:14px;flex:none">检测模型：</span>
      <button class="btn" id="btn320">320n 轻量版</button>
      <button class="btn" id="btn640">640m 增强版</button>
    </div>
    <div class="note" style="margin-top:8px" id="modelDesc"></div>
    <div class="row" style="margin-top:10px">
      <label class="chk"><input type="checkbox" id="chkShortcut" checked>创建桌面快捷方式</label>
      <label class="chk"><input type="checkbox" id="chkRun" checked>安装完成后立即运行</label>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn primary" id="btnInstall" disabled>开始安装</button>
      <span class="note" id="installHint">请先确认显卡模式</span>
    </div>
  </div>
  <div class="phase" id="phase"></div>
  <div id="log" class="hidden"></div>
  <div class="row hidden" id="dlRow">
    <span class="note">未检测到 Python 3.10+：</span>
    <button class="btn" onclick="pywebview.api.open_python_download()">打开 Python 下载页</button>
    <span class="note">安装时勾选 “Add python.exe to PATH”，装好后重新运行本向导</span>
  </div>
</main>
<script>
let chosenGpu = null;
function appendLog(line, kind) {
  const box = document.getElementById('log');
  box.classList.remove('hidden');
  const div = document.createElement('div');
  if (kind) div.className = kind;
  div.textContent = line;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
function setPhase(phase, text) {
  const el = document.getElementById('phase');
  if (phase === 'done') { el.textContent = '✔ 安装完成'; el.style.color = '#34d399'; }
  else if (phase === 'error') { el.textContent = '✖ ' + text; el.style.color = '#f87171'; }
  else { el.textContent = text || phase; el.style.color = '#93c5fd'; }
}
function renderEnv(info) {
  const row = document.getElementById('envRow');
  row.innerHTML = '';
  const mk = (t, ok) => { const s = document.createElement('span');
    s.className = 'tag ' + (ok ? 'ok' : 'bad'); s.textContent = t; row.appendChild(s); };
  mk(info.python_found ? 'Python ' + info.python_ver + ' ✓' : 'Python 3.10+ 未找到',
     info.python_found);
  if (info.python_warn) mk('版本较新，部分依赖可能安装失败', false);
  mk(info.gpu ? '检测到 NVIDIA GPU（推荐 GPU 模式）' : '未检测到 NVIDIA GPU（用 CPU 模式）',
     info.gpu);
  if (!info.python_found) {
    document.getElementById('dlRow').classList.remove('hidden');
    document.getElementById('btnInstall').disabled = true;
    document.getElementById('installHint').textContent = '缺少 Python，无法安装';
  } else {
    chosenGpu = info.gpu;  // 默认跟随检测结果，用户可改
    highlight();
    document.getElementById('btnInstall').disabled = false;
    document.getElementById('installHint').textContent = '';
  }
}
function highlight() {
  document.getElementById('btnGpu').style.borderColor =
    chosenGpu === true ? '#7c3aed' : '#2b426b';
  document.getElementById('btnGpu').style.background =
    chosenGpu === true ? '#241a4d' : '#16233f';
  document.getElementById('btnCpu').style.borderColor =
    chosenGpu === false ? '#2b426b' : '#2b426b';
  document.getElementById('btnCpu').style.background =
    chosenGpu === false ? '#16233f' : '#16233f';
}
let chosenModel = '320n';
const MODEL_DESC = {
  '320n': '320n 轻量版：即装即用、推理速度快；特写姿势（如口交）可能漏检，后续可随时升级。',
  '640m': '640m 增强版：检出明显更强（特写姿势显著改善）；需额外下载约 100 MB 模型文件' +
          '（GitHub 直连，网络不佳会自动降级为 320n），推理速度慢 2-3 倍。'
};
function modelHighlight() {
  document.getElementById('btn320').style.borderColor =
    chosenModel === '320n' ? '#2563eb' : '#2b426b';
  document.getElementById('btn320').style.background =
    chosenModel === '320n' ? '#16233f' : '#16233f';
  document.getElementById('btn640').style.borderColor =
    chosenModel === '640m' ? '#2563eb' : '#2b426b';
  document.getElementById('btn640').style.background =
    chosenModel === '640m' ? '#16233f' : '#16233f';
  document.getElementById('modelDesc').textContent = MODEL_DESC[chosenModel] || '';
}
function pickDir() {
  pywebview.api.choose_dir().then((p) => {
    if (p) document.getElementById('installPath').value = p;
  });
}
function beginInstall() {
  const path = document.getElementById('installPath').value.trim();
  if (!path) { alert('请选择安装位置'); return; }
  if (chosenGpu === null) { alert('请选择显卡模式'); return; }
  document.getElementById('btnInstall').disabled = true;
  document.getElementById('btnGpu').disabled = true;
  document.getElementById('btnCpu').disabled = true;
  document.getElementById('btn320').disabled = true;
  document.getElementById('btn640').disabled = true;
  const shortcut = document.getElementById('chkShortcut').checked;
  const runAfter = document.getElementById('chkRun').checked;
  pywebview.api.start_install(chosenGpu, path, shortcut, runAfter, chosenModel);
}
window.addEventListener('pywebviewready', async () => {
  const info = JSON.parse(await pywebview.api.env_check());
  document.getElementById('installPath').value = info.default_path;
  renderEnv(info);
  modelHighlight();
  document.getElementById('btnGpu').onclick = () => { chosenGpu = true; highlight(); };
  document.getElementById('btnCpu').onclick = () => { chosenGpu = false; highlight(); };
  document.getElementById('btn320').onclick = () => { chosenModel = '320n'; modelHighlight(); };
  document.getElementById('btn640').onclick = () => { chosenModel = '640m'; modelHighlight(); };
  document.getElementById('btnInstall').onclick = beginInstall;
});
</script></body></html>"""


def main() -> None:
    webview.create_window(
        "NSFW Mask 安装向导",
        html=PAGE,
        js_api=Api(),
        width=780,
        height=680,
        min_size=(660, 560),
        background_color="#0f172a",
    )
    webview.start(private_mode=False)


if __name__ == "__main__":
    if AUTO:  # 无人值守自测：--auto gpu|cpu --root DIR [--model 640m|320n] [--no-shortcut] [--no-run]
        gpu = "gpu" in sys.argv
        shortcut = "--no-shortcut" not in sys.argv
        run_after = "--no-run" not in sys.argv
        model = "640m" if "--model" in sys.argv and "640m" in sys.argv else "320n"
        print(f"[auto] target={TARGET} gpu={gpu} model={model}")
        _run_install(gpu, TARGET, shortcut, run_after, model)
        sys.exit(0)
    main()
