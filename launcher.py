"""NSFW-Mask.exe bootstrap (built with PyInstaller, console=False).

Double-click entry for the desktop app. Locates the project venv next to the
exe and hands off to desktop_app.py under pythonw (no console window).
Failures are logged to launcher.log next to the exe so silent deaths can be
diagnosed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback


def log(msg: str) -> None:
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "launcher.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def fail(msg: str) -> None:
    log("FAIL: " + msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "NSFW Mask", 0x10)
    except Exception:
        pass
    sys.exit(1)


def main() -> None:
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    log("launcher start, root=" + root)
    pythonw = os.path.join(root, "venv", "Scripts", "pythonw.exe")
    python = os.path.join(root, "venv", "Scripts", "python.exe")
    app = os.path.join(root, "desktop_app.py")

    if not os.path.exists(pythonw) or not os.path.exists(app):
        fail("未找到运行环境。\n\n请先运行 install\\install_windows.bat 完成安装，再双击本程序。")
        return

    # venv 里需要已安装 pywebview（安装脚本会装）；缺它时 pythonw 会静默失败，
    # 这里预检给用户明确提示。注意必须用 pythonw（GUI 子系统）—— 用 python.exe
    # 会在无控制台的父进程下闪出一个终端窗口。
    try:
        check = subprocess.run(
            [pythonw, "-c", "import webview"],
            cwd=root, capture_output=True, timeout=60,
        )
        if check.returncode != 0:
            fail("缺少桌面窗口组件 (pywebview)。\n\n请重新运行 install\\install_windows.bat 修复。")
            return
    except Exception as exc:
        fail("环境检查失败：%s" % exc)

    try:
        # CREATE_NO_WINDOW：pythonw 本就无窗口，显式标志防止任何控制台闪现
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([pythonw, app], cwd=root, close_fds=True, creationflags=flags)
        log("handoff ok")
    except Exception as exc:
        log("Popen error:\n" + traceback.format_exc())
        fail("启动失败：%s" % exc)


if __name__ == "__main__":
    main()
