"""下载 SAM（Segment Anything）分割模型，用于「贴合部位轮廓」。

用法（在项目根目录）：
    python tools/fetch_sam.py

模型约 358MB，下载到 models/sam-vit-base/。国内网络会自动走
HF_ENDPOINT（默认 https://hf-mirror.com）。已存在则跳过。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "models" / "sam-vit-base"


def main() -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 镜像不支持 Xet 传输协议，禁用后走普通 HTTP
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    marker = TARGET / "model.safetensors"
    if marker.is_file() and marker.stat().st_size > 300 * 1024 * 1024:
        print(f"模型已存在: {marker}")
        return 0

    print(f"下载 facebook/sam-vit-base -> {TARGET}")
    print("（约 358MB，取决于网速需要几分钟）")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少 huggingface_hub：pip install huggingface_hub")
        return 1

    try:
        snapshot_download(
            "facebook/sam-vit-base",
            local_dir=str(TARGET),
            allow_patterns=[
                "config.json",
                "preprocessor_config.json",
                "model.safetensors",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"下载失败: {exc!r}")
        print("可设置代理后重试：set HTTPS_PROXY=http://127.0.0.1:端口")
        return 1

    if marker.is_file() and marker.stat().st_size > 300 * 1024 * 1024:
        print(f"完成: {marker} ({marker.stat().st_size / 1024 / 1024:.0f} MB)")
        return 0
    print("下载结果异常（文件缺失或过小）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
