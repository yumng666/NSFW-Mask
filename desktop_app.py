"""NSFW Mask - desktop window entry (pywebview shell, native titlebar).

Window opens instantly with a loading page; the FastAPI backend (heavy
imports) is brought up in a background thread and the window switches to the
app once it is ready. Uses the native system titlebar.

Closing the window terminates the process immediately (os._exit) so the
single-instance lock is released at once and the app can be relaunched
right away.

Launched via venv pythonw (no console): see NSFW-Mask.exe bootstrap.
"""
from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# 系统代理可能拦截 127.0.0.1 导致窗口白屏 —— WebView2 强制直连本地服务
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-proxy-server")

# 检测模型自适应：存在 640m.onnx（按 640 训练）时自动用 640 分辨率推理，
# 没有则保持默认 320（此时 640 输入会让 320n 漏检特写，绝不能盲设）。
if os.path.exists(os.path.join(ROOT, "models", "640m.onnx")):
    os.environ.setdefault("NSFW_DETECTOR_RESOLUTION", "640")

# 男性检出增强：候选下限与男性门槛降到检测下限，救回极低分检出。
# 代价是误检增多 —— 界面灵敏度档位与手动涂抹可补救；已用
# setdefault，用户显式设置的同名变量优先。
os.environ.setdefault("NSFW_DETECTOR_SCORE_FLOOR", "0.05")
os.environ.setdefault("NSFW_MG_THRESHOLD", "0.05")


# pythonw（无控制台）下 sys.stdout/stderr 是 None，或从 GUI 启动器继承到
# 无效句柄 —— 后端导入期的 print 会让进程静默崩溃。
# 探测可写性，不可写一律重定向到日志文件。
# 注意：替换物必须是"普通文件对象"——pythonnet 会把 .NET Console 桥接到
# Python 流，自定义包装类可能引发 WebView2 宿主崩溃（实测）。
def _safe_streams() -> None:
    log_dir = os.path.join(ROOT, "data")
    os.makedirs(log_dir, exist_ok=True)
    log_stream = open(os.path.join(log_dir, "desktop.log"), "a", encoding="utf-8", buffering=1)

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        usable = False
        try:
            if stream is not None:
                stream.write("")
                stream.flush()
                usable = True
        except Exception:
            usable = False
        if not usable:
            setattr(sys, name, log_stream)


_safe_streams()


def _activate_existing_window() -> bool:
    """找到已运行实例的主窗口并置前（已最小化则还原）。

    第二次双击启动时调用：代替旧版的"已在运行"弹窗 —— 直接把用户
    带回已经打开的那个窗口。窗口标题由 create_window 固定为 "NSFW Mask"。

    Windows 有前台锁：后台进程的 SetForegroundWindow 可能被拒绝。
    按 常规 → SwitchToThisWindow → AttachThreadInput 三级尝试，
    仍失败则闪任务栏提示（FlashWindowEx）。
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.startswith("NSFW Mask"):
                found.append(hwnd)
        return True

    try:
        user32.EnumWindows(_enum_cb, 0)
    except Exception:
        return False
    if not found:
        return False
    hwnd = found[0]

    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.ShowWindow(hwnd, 5)      # SW_SHOW
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)

        # 一级：未抢到前台 → SwitchToThisWindow（无视前台锁的常用 API）
        if user32.GetForegroundWindow() != hwnd:
            try:
                user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass

        # 二级：仍未抢到 → 附加到当前前台线程再置前
        if user32.GetForegroundWindow() != hwnd:
            fg = user32.GetForegroundWindow()
            if fg:
                tid_self = kernel32.GetCurrentThreadId()
                tid_fg = user32.GetWindowThreadProcessId(fg, None)
                if tid_fg and tid_fg != tid_self:
                    user32.AttachThreadInput(tid_self, tid_fg, True)
                    try:
                        user32.SetForegroundWindow(hwnd)
                    finally:
                        user32.AttachThreadInput(tid_self, tid_fg, False)

        # 兜底：闪任务栏（至少让用户注意到窗口在哪）
        if user32.GetForegroundWindow() != hwnd:
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint),
                ]

            fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3, 3, 0)  # FLASHW_ALL
            user32.FlashWindowEx(ctypes.byref(fi))
        return True
    except Exception:
        return False


def _single_instance() -> None:
    """Windows 命名互斥体：已有实例在跑时，把它的窗口调到前台并退出本实例。"""
    if sys.platform != "win32":
        return
    ctypes.windll.kernel32.CreateMutexW(None, False, "NSFW-Mask-Desktop-SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if not _activate_existing_window():
            # 窗口还没出现（对方仍在加载中）或找不到：简短提示后退出
            ctypes.windll.user32.MessageBoxW(
                0, "NSFW Mask 已经在运行（请查看任务栏窗口）。", "NSFW Mask", 0x40
            )
        sys.exit(0)


_single_instance()

LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body { margin:0; height:100vh; display:flex; flex-direction:column;
         align-items:center; justify-content:center; gap:18px;
         background:#0f172a; color:#e2e8f0;
         font-family:"Segoe UI", "Microsoft YaHei", sans-serif; }
  .logo { display:flex; gap:6px; }
  .logo i { width:18px; height:18px; border-radius:3px; animation:pulse 1.2s infinite; }
  .logo i:nth-child(1){ background:#3b82f6; animation-delay:0s }
  .logo i:nth-child(2){ background:#8b5cf6; animation-delay:.15s }
  .logo i:nth-child(3){ background:#ec4899; animation-delay:.3s }
  @keyframes pulse { 0%,100%{opacity:.25} 50%{opacity:1} }
  h1 { font-size:22px; font-weight:500; margin:0; }
  h1 span { color:#94a3b8; font-size:14px; margin-left:8px; }
  p { color:#94a3b8; font-size:13px; margin:0; }
</style></head><body>
  <div class="logo"><i></i><i></i><i></i></div>
  <h1>NSFW Mask<span>by yumng</span></h1>
  <p>正在启动，首次运行约需 1 分钟，请稍候…</p>
</body></html>"""


def find_port(start: int = 8000, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def wait_ready(port: int, timeout: float = 300.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _set_window_icon(window) -> None:
    """把窗口/任务栏图标换成 app.ico。

    任务栏图标跟随窗口进程（pythonw 显示 Python 图标），pywebview 无直接 API。
    通过 pythonnet 设置 Form.Icon —— 必须经 form.Invoke 封送到 UI 线程执行
    （直接跨线程赋值会破坏 WebView2 稳定性，实测）。失败静默，不影响功能。
    """
    ico = os.path.join(ROOT, "app.ico")
    if not os.path.exists(ico):
        return

    def try_set(tries: int = 0) -> None:
        form = None
        try:
            form = getattr(window, "native", None)
        except Exception:
            form = None
        if form is None:
            if tries < 60:  # native Form 在窗口创建后才出现，轮询等待
                threading.Timer(0.5, try_set, args=(tries + 1,)).start()
            return

        def _apply():
            try:
                import clr

                clr.AddReference("System.Drawing")
                from System.Drawing import Icon

                form.Icon = Icon(ico)
                form.ShowIcon = True
                print("[desktop] window icon set from app.ico")
            except Exception as exc:
                print(f"[desktop] set icon error: {exc}")

        try:
            from System import Action

            form.Invoke(Action(_apply))  # 封送到 UI 线程执行
        except Exception as exc:
            print(f"[desktop] icon invoke failed: {exc}")

    threading.Thread(target=try_set, daemon=True).start()


def main() -> None:
    import webview  # 轻量导入；重型的 backend 留到后台线程

    storage_dir = os.path.join(ROOT, "data", "webview")
    os.makedirs(storage_dir, exist_ok=True)

    # 系统原生标题栏；窗口秒开（加载页），后端就绪后自动切换页面
    window = webview.create_window(
        "NSFW Mask by yumng",
        html=LOADING_HTML,
        width=1280,
        height=860,
        min_size=(960, 640),
        background_color="#0f172a",
    )

    def backend() -> None:
        try:
            import uvicorn

            from backend.app import app

            port = find_port()
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(config)
            threading.Thread(target=server.run, daemon=True).start()
            if wait_ready(port):
                # 带 cache-bust：绕过 WebView2 可能残留的旧页面缓存
                window.load_url(f"http://127.0.0.1:{port}/?v={int(time.time())}")
            else:
                window.load_html(
                    '<body style="background:#0f172a;color:#f87171;display:flex;'
                    'height:100vh;align-items:center;justify-content:center;'
                    'font-family:sans-serif">后端启动失败，请重启程序</body>'
                )
        except Exception:
            import traceback

            print(traceback.format_exc())

    threading.Thread(target=backend, daemon=True).start()
    _set_window_icon(window)

    # private_mode=False + 固定 storage 目录：localStorage（API Key 等）跨启动保留
    webview.start(private_mode=False, storage_path=storage_dir)

    # 窗口已关闭：立即终止进程（含所有后台线程），不再等待 uvicorn 优雅退出 ——
    # 否则单实例锁迟迟不释放，用户马上重开会被"已经在运行"挡住。
    print("[desktop] window closed - exit now")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
