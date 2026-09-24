#!/usr/bin/env bash
# NSFW Mask（加固版）— Linux 安装脚本
#
# 与原版 install_linux.sh 的区别：
#   1. 不再把 API Key 明文追加到 ~/.bashrc（原版会执行
#      `echo "export NSFW_API_KEY=..." >> ~/.bashrc`，等于长期留痕）。
#      改为生成 data/api_key 文件，权限 0600。
#   2. systemd 单元改用 EnvironmentFile 指向受限权限的文件，而不是把
#      密钥写在 unit 文件的 Environment= 里（unit 文件默认 644，谁都能读）。
#   3. 服务默认只监听 127.0.0.1，不再直接 0.0.0.0。
#   4. 安装前先检查依赖是否可用，失败即退出，不做"半成品安装"。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "------------------------------------------------"
echo " NSFW Mask（加固版）— Linux 安装程序"
echo "------------------------------------------------"

# ---------- 1. 系统依赖 ----------
echo "[1/5] 检查系统依赖..."
MISSING=()
command -v python3 >/dev/null 2>&1 || MISSING+=("python3")
python3 -c 'import venv' 2>/dev/null || MISSING+=("python3-venv")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ">> 缺少：${MISSING[*]}"
    echo ">> 尝试安装（需要 sudo）..."
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip libgl1 libglib2.0-0
else
    echo ">> 已满足。"
fi

PY_OK=$(python3 -c 'import sys;print(1 if sys.version_info[:2] >= (3,10) else 0)')
if [ "${PY_OK}" != "1" ]; then
    echo "!! 需要 Python 3.10 及以上，当前为 $(python3 -V)" >&2
    exit 1
fi

# ---------- 2. 虚拟环境 ----------
echo "[2/5] 创建虚拟环境..."
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip

# ---------- 3. Python 依赖 ----------
if command -v nvidia-smi >/dev/null 2>&1; then
    echo ">> 检测到 NVIDIA GPU，安装 GPU 版本依赖。"
    pip install -r requirements-gpu.txt
else
    echo ">> 未检测到 GPU，安装 CPU 版本依赖。"
    pip install -r requirements.txt
fi

# ---------- 4. 生成密钥 ----------
echo "[3/5] 生成 API Key..."
python - <<'PY'
from backend import config
from backend.security import SecretStore
config.ensure_dirs()
store = SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE)
store.api_key          # 触发生成
store.signing_key
PY

chmod 600 data/api_key data/signing_key 2>/dev/null || true
chmod 700 data 2>/dev/null || true
echo ">> 密钥已写入 ${PROJECT_ROOT}/data/api_key（权限 600）"
echo ">> 查看方式：cat ${PROJECT_ROOT}/data/api_key"

# 供 systemd 使用的受限环境文件（只含一项，权限 600）
ENV_FILE="${PROJECT_ROOT}/data/nsfw_guard.env"
umask 077
{
    printf 'NSFW_API_KEY=%s\n' "$(cat data/api_key)"
    printf 'NSFW_HOST=127.0.0.1\n'
    printf 'NSFW_PORT=8000\n'
} > "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
echo ">> systemd 环境文件：${ENV_FILE}（权限 600）"

# ---------- 5. systemd 单元 ----------
echo "[4/5] 写入 systemd 单元 nsfw_guard.service ..."
SERVICE_USER="${SUDO_USER:-$USER}"
cat <<EOF | sudo tee /etc/systemd/system/nsfw_guard.service >/dev/null
[Unit]
Description=NSFW Mask (Hardened) API
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=${ENV_FILE}
Environment="PYTHONPATH=${PROJECT_ROOT}"
ExecStart=${PROJECT_ROOT}/venv/bin/python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

# --- 基础加固 ---
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${PROJECT_ROOT}/data
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

echo "[5/5] 完成。"
echo "------------------------------------------------"
cat <<EOF
启动服务：  sudo systemctl start nsfw_guard
开机自启：  sudo systemctl enable nsfw_guard
查看日志：  journalctl -u nsfw_guard -f

注意：
  * 服务默认只监听 127.0.0.1，外网无法直接访问 —— 这是有意为之。
  * 如需对外开放，请在 Nginx / Caddy 里做 TLS + 访问控制后反代到
    127.0.0.1:8000，并把请求头交给它处理，不要直接改成 0.0.0.0。
  * 若要改配置，编辑 ${ENV_FILE} 后 systemctl restart nsfw_guard。
EOF
echo "------------------------------------------------"
