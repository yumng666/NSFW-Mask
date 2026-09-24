"""密钥管理、常量时间比较、下载令牌签名、文件名与路径校验。

设计原则：
1. **不存在硬编码密钥**。密钥要么来自环境变量，要么首次启动时随机生成并
   以 0600 权限落盘。原版把同一个默认 Key 同时写在后端、API 文档和前端
   JS 三个地方，等于没有鉴权。
2. **所有比较走常量时间**，避免时序侧信道（`==` 会短路）。
3. **文件名按白名单校验，且二次用 realpath 做容器校验**。原版只检查
   `".."` 和 `/`，在 Windows 上被反斜杠绕过。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import stat
import time
from pathlib import Path

# 服务端自己生成的文件名格式，除此之外一律拒绝。
#
# 文件名全部由服务端生成，字符集收紧到 [A-Za-z0-9_-]（不含点、不含任何
# 路径分隔符），因此从构造上就不可能表达 ".." 或绝对路径。已有的三种前缀：
#   processed_<uuid>.<ext>  兼容旧结果（保留可读）
#   original_<uuid>.<ext>   用户上传的原图副本（供手动改遮罩时重算）
#   mask_<uuid>.png         自动遮罩位图（前端涂抹编辑的初始状态）
#   masked_<时间戳>_<原名>.<ext>  最终结果，存放在 output/ 供用户直接取用
#
# 第二道防线是 resolve_within() 的 realpath 包含性检查。
# \w 在 Python re 里默认含 Unicode 字母（中文等）。字符类里刻意
# 不含点号与任何分隔符 —— ".." 与路径穿越在构造上就不可能；
# 第二道防线是 resolve_within() 的 realpath 包含性检查。
SAFE_FILENAME_RE = re.compile(
    r"^[\w-]{1,80}\.(?:jpg|jpeg|png|webp)$"
)

# --------------------------------------------------------------------------
# 密钥文件读写
# --------------------------------------------------------------------------
_signing_key_cache: str | None = None


def _restrict_permissions(path: Path) -> None:
    """尽力把文件权限收成 0600。Windows 上 chmod 语义有限，忽略失败。"""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _read_or_create_secret(path: Path, prefix: str = "") -> str:
    """读取密钥；不存在则生成随机值并以 0600 落盘。"""
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                _restrict_permissions(path)
                return existing
    except OSError:
        pass

    value = prefix + secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先用最严权限创建，再写入，避免出现短暂的宽松权限窗口
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
    finally:
        _restrict_permissions(path)
    return value


class SecretStore:
    """集中持有运行期密钥。所有值都惰性加载并缓存。"""

    def __init__(self, api_key_file: Path, signing_key_file: Path) -> None:
        self._api_key_file = api_key_file
        self._signing_key_file = signing_key_file
        self._api_key: str | None = None
        self._signing_key: str | None = None
        # 是否使用了自动生成的密钥（用于启动提示）
        self.api_key_was_generated = False

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            env_value = (os.getenv("NSFW_API_KEY") or "").strip()
            if env_value:
                if len(env_value) < 16:
                    raise RuntimeError(
                        "NSFW_API_KEY 太短（至少 16 个字符），拒绝以弱密钥启动。"
                    )
                self._api_key = env_value
                self.api_key_was_generated = False
            else:
                self._api_key = _read_or_create_secret(self._api_key_file)
                self.api_key_was_generated = True
        return self._api_key

    @property
    def signing_key(self) -> str:
        if self._signing_key is None:
            self._signing_key = _read_or_create_secret(
                self._signing_key_file, prefix="sig_"
            )
        return self._signing_key


def constant_time_equals(candidate: str, expected: str) -> bool:
    """常量时间字符串比较。两个空串视为不相等，避免"未配置即放行"。"""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(
        candidate.encode("utf-8", "ignore"), expected.encode("utf-8", "ignore")
    )


def key_hint(secret_value: str) -> str:
    """生成可安全写入日志的密钥指纹（只透露长度与短前缀，便于人工核对）。"""
    if not secret_value:
        return "<empty>"
    return f"len={len(secret_value)} sha256={hashlib.sha256(secret_value.encode()).hexdigest()[:8]}"


# --------------------------------------------------------------------------
# 文件名与路径安全
# --------------------------------------------------------------------------
def is_safe_filename(filename: str) -> bool:
    """文件名必须完全匹配服务端自身的命名格式。

    这是白名单而非黑名单：任何路径分隔符（`/`、`\\`）、盘符、`..`
    都不可能出现在 `[0-9a-f-]` 与固定字面量之外，因此无法构造穿越。
    """
    if not filename or len(filename) > 128:
        return False
    return bool(SAFE_FILENAME_RE.match(filename))


def resolve_within(base_dir: Path, filename: str) -> Path | None:
    """在 base_dir 内解析文件名，越界则返回 None。

    第二道防线：即使白名单被绕过，realpath 之后的父目录也必须等于 base_dir，
    这能挡住符号链接、Windows 8.3 短名、以及各种编码技巧。
    """
    if not is_safe_filename(filename):
        return None
    base_real = base_dir.resolve()
    candidate = (base_real / filename).resolve()
    if candidate.parent != base_real:
        return None
    if not candidate.is_file():
        return None
    return candidate


# 文件夹分段（不含扩展名，因此不能用 is_safe_filename 校验）
# 文件夹段：Unicode 字母数字（\w 含中文等）+ 空格 + 连字符。
# 刻意不含点号与任何分隔符 —— ".." 与穿越在构造上就不可能。
_SAFE_SEGMENT = re.compile(r"^[\w][\w -]{0,79}$")


def resolve_within_subfolder(base_dir: Path, relative: str) -> Path | None:
    """同 resolve_within，但允许"子文件夹/文件名"形式的相对路径。

    用于文件夹批量处理：结果会输出到 output/<文件夹名>/ 之下。
    防护与单层版本等价 —— 每一段都必须通过白名单（文件夹段只允许
    [A-Za-z0-9_-]，文件段走完整白名单），不许出现 ".."、分隔符之外
    的任何特殊字符；realpath 之后仍必须位于 base_dir 之内。
    """
    parts = [p for p in relative.replace("\\", "/").split("/") if p]
    if not parts or len(parts) > 2:
        return None
    for segment in parts[:-1]:
        if not _SAFE_SEGMENT.match(segment):
            return None
    if not is_safe_filename(parts[-1]):
        return None
    base_real = base_dir.resolve()
    candidate = base_real.joinpath(*parts).resolve()
    if base_real != candidate.parent and base_real not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------
# 下载令牌：让"取回图片"这件事也带上鉴权，同时不破坏 <img src> 的用法
# --------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def make_download_token(filename: str, signing_key: str, ttl: int) -> str:
    """为某个文件名签发下载令牌：`<过期时间戳>.<HMAC>`。"""
    expires = int(time.time()) + ttl
    message = f"{filename}\n{expires}".encode("utf-8")
    signature = hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).digest()
    return f"{expires}.{_b64url(signature)}"


def verify_download_token(
    filename: str, token: str, signing_key: str
) -> bool:
    """校验令牌：签名正确且未过期。"""
    if not token or "." not in token:
        return False
    expires_raw, _, signature_raw = token.partition(".")
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if expires < time.time():
        return False
    message = f"{filename}\n{expires}".encode("utf-8")
    expected = hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).digest()
    try:
        provided = _b64url_decode(signature_raw)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(expected, provided)
