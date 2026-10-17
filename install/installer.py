"""NSFW Mask - graphical setup wizard (PyInstaller onefile, no console).

Graphical replacement for install_windows.ps1: detects Python/GPU, creates
the venv, installs dependencies (Tsinghua mirror), generates the API key,
with live progress log in the window.

Auto (unattended) mode for testing:
    NSFW-Mask-Setup.exe --auto gpu --root "E:\\path\\to\\project"
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


def get_root() -> str:
    if "--root" in sys.argv:
        return os.path.abspath(sys.argv[sys.argv.index("--root") + 1])
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ROOT = get_root()


def log_ui(line: str, kind: str = "info") -> None:
    """把一行日志推送到向导页面（JSON 转义后 evaluate_js）；--auto 模式打印到控制台。"""
    if "--auto" in sys.argv:
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
    """探测系统 Python 3.10+，返回 (命令, 版本) 或 (None, '')。"""
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        exe = cmd[0]
        if not shutil.which(exe):
            continue
        try:
            out = subprocess.run(
                cmd + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                capture_output=True, text=True, timeout=20,
            )
            ver = (out.stdout or "").strip()
            major, _, minor = ver.partition(".")
            if ver and (int(major), int(minor or 0)) >= (3, 10):
                return " ".join(cmd), ver
        except Exception:
            continue
    return None, ""


def detect_gpu() -> bool:
    return bool(shutil.which("nvidia-smi"))


class Api:
    """JS 桥：环境检测 / 启动安装 / 打开下载页。"""

    def env_check(self) -> str:
        py, ver = find_python()
        info = {
            "python_found": py is not None,
            "python_ver": ver,
            "python_cmd": py or "",
            "gpu": detect_gpu(),
            "venv_exists": os.path.isdir(os.path.join(ROOT, "venv")),
        }
        return json.dumps(info)

    def open_python_download(self) -> None:
        webbrowser.open(PYTHON_DOWNLOAD)

    def start_install(self, gpu: bool = False) -> str:
        threading.Thread(target=_run_install, args=(bool(gpu),), daemon=True).start()
        return "started"


def _run_install(gpu: bool) -> None:
    try:
        _install(gpu)
    except Exception as exc:
        log_ui(f"安装失败：{exc}", "error")
        phase_ui("error", str(exc))


def _run_step(title: str, cmd: list[str], cwd: str | None = None) -> None:
    log_ui("$ " + " ".join(cmd), "cmd")
    proc = subprocess.Popen(
        cmd, cwd=cwd or ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log_ui(line.rstrip())
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{title} 失败（退出码 {code}）")


def _install(gpu: bool) -> None:
    py, ver = find_python()
    if not py:
        phase_ui("error", "未找到 Python 3.10+")
        log_ui("未检测到 Python 3.10+，请先安装 Python 并勾选 Add to PATH。", "error")
        return
    log_ui(f"使用 Python {ver}（{py}）")

    phase_ui("venv", "创建虚拟环境…")
    venv_dir = os.path.join(ROOT, "venv")
    py_parts = py.split(" ")
    _run_step("创建 venv", py_parts + ["-m", "venv", venv_dir])

    vpy = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(vpy):
        raise RuntimeError("venv 创建异常：未找到 venv\\Scripts\\python.exe")

    phase_ui("pip", "安装依赖（几分钟，取决于网络）…")
    req = "requirements-gpu.txt" if gpu else "requirements.txt"
    if not os.path.exists(os.path.join(ROOT, req)):
        raise RuntimeError(f"缺少 {req}")
    _run_step(
        "安装依赖",
        [vpy, "-m", "pip", "install", "-r", req, "-i", PIP_MIRROR],
    )

    phase_ui("key", "生成 API 密钥…")
    key_script = (
        "from backend import config; from backend.security import SecretStore; "
        "config.ensure_dirs(); "
        "SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE).api_key"
    )
    _run_step("生成密钥", [vpy, "-c", key_script])

    phase_ui("done", "安装完成")
    log_ui("全部完成！关闭本向导，双击 NSFW-Mask.exe 即可启动。", "ok")


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
         gap:14px; min-height:0; }
  .card { background:#111c33; border:1px solid #1e2d4d; border-radius:10px;
          padding:14px 16px; }
  .row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .tag { font-size:12px; padding:2px 10px; border-radius:20px;
         background:#1e293b; color:#94a3b8; }
  .tag.ok { background:#052e1c; color:#34d399; }
  .tag.bad { background:#3b0d12; color:#f87171; }
  .btn { border:1px solid #2b426b; background:#16233f; color:#e2e8f0;
         padding:10px 22px; border-radius:8px; font-size:14px; cursor:pointer; }
  .btn:hover { background:#1d2f55; }
  .btn.primary { background:#2563eb; border-color:#2563eb; }
  .btn.primary:hover { background:#1d4ed8; }
  .btn:disabled { opacity:.45; cursor:not-allowed; }
  .btn.gpu { border-color:#7c3aed; }
  .phase { font-size:14px; color:#93c5fd; min-height:20px; }
  #log { flex:1; min-height:120px; background:#0a1120; border:1px solid #1e2d4d;
         border-radius:10px; padding:10px 12px; overflow-y:auto;
         font:12px/1.5 Consolas,monospace; color:#9fb3c8; white-space:pre-wrap;
         word-break:break-all; }
  #log .error { color:#f87171; }
  #log .ok { color:#34d399; }
  #log .cmd { color:#7dd3fc; }
  .hidden { display:none; }
  .note { color:#64748b; font-size:12px; }
  a { color:#60a5fa; }
</style></head><body>
<header>
  <h1>NSFW Mask 安装向导<span>by yumng</span></h1>
  <p>将为本机创建运行环境并安装依赖（约 2-5 GB 磁盘空间，取决于显卡模式）</p>
</header>
<main>
  <div class="card">
    <div class="row" id="envRow"><span class="tag">正在检测环境…</span></div>
  </div>
  <div class="card" id="chooseCard">
    <div class="row">
      <span style="font-size:14px">显卡模式：</span>
      <button class="btn gpu" id="btnGpu">NVIDIA GPU 加速</button>
      <button class="btn" id="btnCpu">仅 CPU</button>
      <span class="note" id="gpuNote"></span>
    </div>
  </div>
  <div class="phase" id="phase"></div>
  <div id="log"></div>
  <div class="row hidden" id="doneRow">
    <span class="tag ok">安装完成</span>
    <span>关闭本向导，双击 <b>NSFW-Mask.exe</b> 启动程序。</span>
  </div>
  <div class="row hidden" id="dlRow">
    <span class="note">未检测到 Python 3.10+：</span>
    <button class="btn" onclick="pywebview.api.open_python_download()">打开 Python 下载页</button>
    <span class="note">安装时勾选 “Add python.exe to PATH”，装好后重新运行本向导</span>
  </div>
</main>
<script>
function appendLog(line, kind) {
  const box = document.getElementById('log');
  const div = document.createElement('div');
  if (kind) div.className = kind;
  div.textContent = line;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
function setPhase(phase, text) {
  const el = document.getElementById('phase');
  if (phase === 'done') { el.textContent = '✔ 安装完成'; el.style.color = '#34d399';
    document.getElementById('doneRow').classList.remove('hidden'); }
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
  mk(info.gpu ? '检测到 NVIDIA GPU' : '未检测到 NVIDIA GPU', info.gpu);
  mk(info.venv_exists ? '已有 venv（将复用）' : '将创建 venv', true);
  if (info.gpu) document.getElementById('gpuNote').textContent = '已检测到显卡，推荐 GPU 模式';
  if (!info.python_found) {
    document.getElementById('chooseCard').classList.add('hidden');
    document.getElementById('dlRow').classList.remove('hidden');
  }
  if (info.gpu) { /* 预选高亮由用户确认 */ }
}
function beginInstall(gpu) {
  document.getElementById('btnGpu').disabled = true;
  document.getElementById('btnCpu').disabled = true;
  pywebview.api.start_install(gpu);
}
window.addEventListener('pywebviewready', async () => {
  const info = JSON.parse(await pywebview.api.env_check());
  renderEnv(info);
  document.getElementById('btnGpu').onclick = () => beginInstall(true);
  document.getElementById('btnCpu').onclick = () => beginInstall(false);
});
</script></body></html>"""


def main() -> None:
    webview.create_window(
        "NSFW Mask 安装向导",
        html=PAGE,
        js_api=Api(),
        width=760,
        height=640,
        min_size=(640, 520),
        background_color="#0f172a",
    )
    webview.start(private_mode=False)


if __name__ == "__main__":
    if "--auto" in sys.argv:  # 无人值守自测：--auto gpu|cpu [--root DIR]
        gpu = "gpu" in sys.argv
        print(f"[auto] root={ROOT} gpu={gpu}")
        _run_install(gpu)
        sys.exit(0)
    main()
