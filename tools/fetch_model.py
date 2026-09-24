"""下载 NudeNet 的大模型（640m），显著提升检出率。

用法：
    python tools/fetch_model.py            # 下载 640m（推荐，约 170 MB）
    python tools/fetch_model.py 480m       # 下载 480m（更快，召回略低）

下载到 <项目根>/models/<名字>.onnx，服务启动时会自动启用（无需配置）。

为什么需要它：口交、生殖器贴近面部这类姿势，默认的 320n 小模型经常
检不出来 —— 这是模型能力的上限，调参数解决不了。640m 的召回明显更好。

网络受限时本脚本支持代理：
    set HTTPS_PROXY=http://127.0.0.1:7890
    python tools/fetch_model.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RELEASE = "https://github.com/notAI-tech/NudeNet/releases/download/v3.4-weights"
MIN_BYTES = 10 * 1024 * 1024          # 正常模型不会小于 10 MB
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NSFW-Mask"


def fetch(name: str) -> int:
    url = f"{RELEASE}/{name}.onnx"
    dest = BASE_DIR / "models" / f"{name}.onnx"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > MIN_BYTES:
        print(f"[跳过] 已存在: {dest}")
        return 0

    print(f"[下载] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, tmp:
            total = 0
            while True:
                chunk = resp.read(1024 * 512)
                if not chunk:
                    break
                tmp.write(chunk)
                total += len(chunk)
                if total % (10 * 1024 * 1024) < 512 * 1024:
                    print(f"  已下载 {total / 1024 / 1024:.0f} MB")
    except Exception as exc:
        tmp.close()
        os.unlink(tmp.name)
        print(f"[失败] {exc}")
        print("       若网络受限，请先设置代理再重试：")
        print("       set HTTPS_PROXY=http://127.0.0.1:7890")
        return 1

    if total < MIN_BYTES:
        print(f"[失败] 下载内容只有 {total} 字节，不像模型文件（可能被重定向到登录页）")
        os.unlink(tmp.name)
        return 1

    os.replace(tmp.name, dest)
    print(f"[完成] {dest}  ({total / 1024 / 1024:.0f} MB)")
    print("       重启服务即可自动启用，无需其它配置。")
    return 0


def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "640m").strip().lower()
    if name not in {"640m", "480m"}:
        print(f"不支持的模型名: {name}（可选 640m / 480m）")
        return 2
    return fetch(name)


if __name__ == "__main__":
    sys.exit(main())
