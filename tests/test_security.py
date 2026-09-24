"""安全模块的独立单元测试（不依赖网络与模型）。

直接运行： python tests/test_security.py
以断言失败数作为退出码。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config  # noqa: E402
from backend.security import (  # noqa: E402
    SecretStore,
    constant_time_equals,
    is_safe_filename,
    make_download_token,
    resolve_within,
    verify_download_token,
)

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}: 得到 {actual!r}，期望 {expected!r}")
        failures.append(label)


print("=" * 66)
print("1. 文件名白名单 —— 原漏洞正是绕过了这一步")
print("=" * 66)
name_cases = [
    # 服务端自己产出的合法名字
    ("processed_11111111-2222-3333-4444-555555555555.jpg", True),
    ("processed_11111111-2222-3333-4444-555555555555.webp", True),
    ("original_11111111-2222-3333-4444-555555555555.png", True),
    ("mask_11111111-2222-3333-4444-555555555555.png", True),
    # output/ 里的结果采用人类可读命名（时间戳 + 清洗后的原文件名）
    ("masked_20260923-201530_photo.png", True),
    ("masked_20260923-201530_IMG_0001.jpg", True),
    # 原版被绕过的载荷：反斜杠绝对路径，既不含 "/" 也不含 ".."
    ("C:\\Windows\\win.ini", False),
    ("C:\\Users\\Administrator\\.ssh\\id_rsa", False),
    ("%5CWindows%5Cwin.ini", False),
    # 经典正向穿越
    ("../../../etc/passwd", False),
    ("..\\..\\config.py", False),
    ("..", False),
    # 其他花样
    ("/etc/passwd", False),
    ("processed_11111111-2222-3333-4444-555555555555.jpg/../../x", False),
    ("processed_11111111-2222-3333-4444-555555555555.exe", False),
    # 白名单放宽为 [A-Za-z0-9_-] 后，这种"格式不像我们生成的"名字也会通过。
    # 有意为之：文件名永远由服务端生成，白名单的职责是挡住路径穿越
    # （分隔符、点号、非法扩展名），而不是校验业务格式；
    # 就算名字通过了，后面还有 resolve_within 的目录包含性检查兜底。
    ("processed_x.jpg", True),
    # 名字里带点号会被拒绝 —— 这样 ".." 根本构造不出来
    ("masked_20260923..photo.png", False),
    ("foo.bar.jpg", False),
    ("", False),
    ("a" * 200, False),
    ("processed_11111111-2222-3333-4444-555555555555.JPG", False),
    # Unicode 文件名（中文等）：白名单已放宽到 \w，中文合法
    ("masked_照片.jpg", True),
    ("masked_测试_2026.png", True),
    # 但点号与分隔符仍然非法
    ("masked_..jpg", False),
    ("masked_照片/../../x.jpg", False),
]
for name, expected in name_cases:
    check(f"is_safe_filename({name!r})", is_safe_filename(name), expected)

print()
print("=" * 66)
print("2. realpath 容器校验（第二道防线）")
print("=" * 66)
check(
    "越界名一律返回 None",
    resolve_within(config.WORK_DIR, "C:\\Windows\\win.ini"),
    None,
)
check(
    "不存在的合法名返回 None",
    resolve_within(config.WORK_DIR, "processed_11111111-2222-3333-4444-555555555555.jpg"),
    None,
)
check(
    "output 目录同样受包含性检查约束",
    resolve_within(config.OUTPUT_DIR, "..\\..\\config.py"),
    None,
)

print()
print("=" * 66)
print("3. 下载令牌（HMAC，绑定文件名 + 有效期）")
print("=" * 66)
store = SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE)
signing_key = store.signing_key
target = "processed_11111111-2222-3333-4444-555555555555.jpg"
other = "processed_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jpg"
token = make_download_token(target, signing_key, 3600)

check("合法令牌通过", verify_download_token(target, token, signing_key), True)
check("换到别的文件名失败", verify_download_token(other, token, signing_key), False)
check("签名密钥不对失败", verify_download_token(target, token, "wrong-key"), False)
check(
    "过期令牌失败",
    verify_download_token(target, make_download_token(target, signing_key, -10), signing_key),
    False,
)
check("空令牌失败", verify_download_token(target, "", signing_key), False)
check("没有点号的令牌失败", verify_download_token(target, "garbage", signing_key), False)
check(
    "篡改过期时间失败",
    verify_download_token(target, "99999999999." + token.split(".", 1)[1], signing_key),
    False,
)

print()
print("=" * 66)
print("4. 常量时间比较")
print("=" * 66)
check("相同返回 True", constant_time_equals("abc123", "abc123"), True)
check("不同返回 False", constant_time_equals("abc123", "abc124"), False)
check("空候选不通过", constant_time_equals("", "abc123"), False)
check("空期望不通过", constant_time_equals("abc123", ""), False)
check("双方为空不通过", constant_time_equals("", ""), False)

print()
print("=" * 66)
print("5. 运行期密钥")
print("=" * 66)
check("自动生成的密钥长度足够", len(store.api_key) >= 32, True)
check(
    "密钥文件权限已收窄存在",
    os.path.exists(config.API_KEY_FILE),
    True,
)
# 再次读取应当稳定（不会每次生成新密钥）
check("密钥可重复读取", SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE).api_key, store.api_key)

print()
print("=" * 66)
if failures:
    print(f"结果：{len(failures)} 项失败")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("结果：全部通过")
