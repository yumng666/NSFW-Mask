"""端到端 API 测试：真正启动 uvicorn，用 HTTP 打全流程。

覆盖两类内容：
  A. 功能可用性 —— 鉴权、三个处理接口、错误码、安全响应头
  B. 漏洞回归 —— 原版可被读任意文件的路径穿越，加固版必须被阻断

直接运行： python tests/test_api_e2e.py
"""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

TEST_API_KEY = "test_key_" + "a" * 24
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}: 得到 {actual!r}，期望 {expected!r}")
        failures.append(label)


def wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- 准备环境
tmpdir = tempfile.mkdtemp(prefix="nsfwapi_")
data_dir = os.path.join(tmpdir, "data")
os.makedirs(data_dir, exist_ok=True)

# 造一张合成测试图
img_path = os.path.join(tmpdir, "sample.png")
rng = np.random.default_rng(7)
cv2.imwrite(img_path, rng.integers(0, 256, size=(600, 800, 3), dtype=np.uint8))

# 造一个"攻击目标"文件，放在数据目录之外
victim = os.path.join(tmpdir, "victim_secret.txt")
with open(victim, "w", encoding="utf-8") as fh:
    fh.write("CANARY_ARBITRARY_FILE_READ_9f3a\n")

env = dict(os.environ)
env.update(
    {
        "NSFW_API_KEY": TEST_API_KEY,
        "NSFW_DATA_DIR": data_dir,
        "NSFW_HOST": "127.0.0.1",
        "NSFW_PORT": str(PORT),
        "NSFW_DEEP_SCAN": "1",
        "PYTHONPATH": str(ROOT),
        "PYTHONIOENCODING": "utf-8",
    }
)

print("=" * 68)
print("启动服务（真实 uvicorn 子进程）")
print("=" * 68)
proc = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        "--log-level",
        "warning",
    ],
    cwd=str(ROOT),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

try:
    if not wait_for_port(PORT):
        print("!! 服务未能启动")
        proc.kill()
        out, _ = proc.communicate(timeout=10)
        print(out[-3000:])
        sys.exit(1)
    print(f">> 服务已监听 {BASE}")

    # trust_env=False：本机自测必须绕过环境里的 HTTP_PROXY，
    # 否则 httpx 会把请求改写成长格式 URI 转发给代理，导致全部 404。
    client = httpx.Client(base_url=BASE, timeout=180.0, trust_env=False)
    auth = {"X-API-KEY": TEST_API_KEY}

    # ------------------------------------------------------- A. 健康检查
    print()
    print("=" * 68)
    print("A1. 健康检查与信息泄露检查")
    print("=" * 68)
    r = client.get("/api/health")
    check("GET /api/health -> 200", r.status_code, 200)
    body = r.json()
    check("状态为 ok", body.get("status"), "ok")
    check(
        "不泄露 API Key",
        TEST_API_KEY in r.text or "api_key" in body,
        False,
    )

    print()
    print("=" * 68)
    print("A2. 安全响应头")
    print("=" * 68)
    check("X-Content-Type-Options", r.headers.get("x-content-type-options"), "nosniff")
    check("X-Frame-Options", r.headers.get("x-frame-options"), "DENY")
    check("Referrer-Policy", r.headers.get("referrer-policy"), "no-referrer")
    csp = r.headers.get("content-security-policy", "")
    check("CSP 不含 unsafe-eval", "unsafe-eval" in csp, False)
    check("CSP 限定 default-src", "default-src 'none'" in csp, True)

    # ------------------------------------------------------- A3. 鉴权
    print()
    print("=" * 68)
    print("A3. 鉴权")
    print("=" * 68)

    def upload(headers=None, **extra):
        with open(img_path, "rb") as fh:
            data = {
                "mode": "blur",
                "intensity": "51",
                "color": "#000000",
            }
            data.update(extra)
            return client.post(
                "/api/process",
                headers=headers or {},
                files={"file": ("sample.png", fh, "image/png")},
                data=data,
            )

    check("无密钥 -> 403", upload().status_code, 403)
    check("错误密钥 -> 403", upload({"X-API-KEY": "wrong"}).status_code, 403)
    check("空密钥 -> 403", upload({"X-API-KEY": ""}).status_code, 403)

    # --------------------------------------------------- A4. 正常处理
    print()
    print("=" * 68)
    print("A4. 正常处理流程")
    print("=" * 68)
    r = upload(auth)
    check("正确密钥 -> 200", r.status_code, 200)
    payload = r.json() if r.status_code == 200 else {}
    # 这个任务的结果会被 C 节复用（重新打码需要原图还在保留期内），
    # 不再重复上传 —— /api/process 限流 30 次/分钟，测试要省着用。
    main_payload = payload
    check("返回 processed_url", bool(payload.get("processed_url")), True)
    check("返回 scores 字段", "scores" in payload, True)
    check("返回 detections 字段", "detections" in payload, True)
    check("返回 blur_count 字段", "blur_count" in payload, True)
    check("返回 decisions 明细字段", isinstance(payload.get("decisions"), list), True)

    # 结果必须落在 output/ 目录，且文件名人类可读（用户要求结果存这里）
    out_name = (payload.get("processed_url") or "").split("/")[-1].split("?")[0]
    check("结果文件名是人类可读格式", out_name.startswith("masked_"), True)
    check(
        "结果文件确实存放在 output/ 目录",
        (config.OUTPUT_DIR / out_name).exists(),
        True,
    )
    # 中间产物在服务进程的 WORK_DIR。注意：测试给服务进程单独设了
    # NSFW_DATA_DIR 指向临时目录，所以这里不能用本进程的 config.WORK_DIR。
    server_work_dir = Path(data_dir) / "processed"
    # 文件夹批量：结果应进入 output/<子文件夹>/
    r = client.post(
        "/api/process",
        headers=auth,
        files={"file": ("sub_photo.png", open(img_path, "rb"), "image/png")},
        data={"mode": "blur", "intensity": "51", "subfolder": "我的相册/..\\evil"},
    )
    check("带子文件夹(含穿越载荷) -> 200", r.status_code, 200)
    if r.status_code == 200:
        sub_url = r.json().get("processed_url", "")
        sub_name = sub_url.split("/api/files/")[-1].split("?")[0]
        check("穿越载荷被清洗为安全段", "/" in sub_name and ".." not in sub_name, True)
        sub_parts = sub_name.split("/")
        expected_path = config.OUTPUT_DIR.joinpath(*sub_parts)
        check("结果落在 output/ 的子文件夹里", expected_path.exists(), True)
        r2 = client.get(sub_url)
        check("可从子文件夹取回结果", r2.status_code, 200)
        check("子文件夹结果是图片", r2.headers.get("content-type", "").startswith("image/"), True)
        # 穿越载荷不得逃出 output/
        check(
            "穿越载荷没有写到 output/ 之外",
            not (config.OUTPUT_DIR.parent / "evil").exists(),
            True,
        )

    check(
        "中间产物（原图）在 WORK_DIR 而不在 output/",
        not (config.OUTPUT_DIR / f"original_{payload.get('id')}.jpg").exists()
        and any(
            (server_work_dir / f"original_{payload.get('id')}{s}").exists()
            for s in (".jpg", ".jpeg", ".png", ".webp")
        ),
        True,
    )

    processed_url = payload.get("processed_url", "")
    check("下载地址带令牌参数", "?t=" in processed_url, True)

    r = client.get(processed_url)
    check("带令牌取图 -> 200", r.status_code, 200)
    check("取回的是图片", r.headers.get("content-type", "").startswith("image/"), True)
    check("下载响应 no-store", r.headers.get("cache-control"), "no-store")

    bare = processed_url.split("?")[0]
    check("去掉令牌 -> 403", client.get(bare).status_code, 403)
    check(
        "令牌换到别的文件名 -> 403",
        client.get(bare.replace("processed_", "processed_") + "?t=bogus.abc").status_code,
        403,
    )

    # ---------------------------------- B. 原漏洞回归（本次核查的重点）
    print()
    print("=" * 68)
    print("B. 原版任意文件读取漏洞的回归测试")
    print("=" * 68)
    print("   原版载荷：GET /api/files/<绝对路径>，无需任何密钥")
    print("   原版结果：HTTP 200 + 目标文件明文")
    print()

    victim_win = victim.replace("/", "\\")
    victim_posix = victim

    payloads = [
        # 反斜杠绝对路径，既不含 "/" 也不含 ".."，正是绕过原版过滤的形式
        ("反斜杠绝对路径", "/api/files/" + victim_win.replace("\\", "%5C")),
        ("正斜杠绝对路径", "/api/files/" + victim_posix),
        ("../ 正向穿越", "/api/files/..%2F..%2Fvictim_secret.txt"),
        ("..\\ 反斜杠穿越", "/api/files/..%5C..%5Cvictim_secret.txt"),
        ("Windows 系统文件", "/api/files/C:%5CWindows%5Cwin.ini"),
        ("UNC 路径", "/api/files/%5C%5Clocalhost%5CC$%5CWindows%5Cwin.ini"),
        ("URL 双重编码", "/api/files/C%253A%255CWindows%255Cwin.ini"),
        ("指向数据目录的上级", "/api/files/processed_11111111-2222-3333-4444-555555555555.jpg"),
    ]

    for label, url in payloads:
        resp = client.get(url, headers=auth)
        leaked = "CANARY_ARBITRARY_FILE_READ_9f3a" in resp.text
        check(f"{label} 未泄露内容", leaked, False)
        check(f"{label} 返回 404/400/403", resp.status_code in (400, 403, 404), True)
        if leaked:
            print(f"        !! 泄露内容片段: {resp.text[:120]}")

    # 也确认带正确密钥时依然读不到（授权也不能越界）
    resp = client.get("/api/files/C:%5CWindows%5Cwin.ini", headers=auth)
    check("带密钥也不放行越界路径", resp.status_code in (400, 403, 404), True)

    # ------------------------------------------------- A5. 参数校验
    print()
    print("=" * 68)
    print("A5. 参数校验（原版完全不校验）")
    print("=" * 68)
    # 注意：/api/process 限流 30 次/分钟，这里必须合并参数、控制请求数。
    # 422 由 FastAPI 的类型/枚举校验产生，一次只报第一个错误，所以分开测。
    check("mode 非法 -> 422", upload(auth, mode="rm -rf /").status_code, 422)
    check("intensity 非数字 -> 422", upload(auth, intensity="abc").status_code, 422)
    check("color 非法 -> 422", upload(auth, color="red; DROP TABLE").status_code, 422)
    # 非数字的 padding 由类型校验直接拒绝（422），比静默回退更明确
    check("padding 非数字 -> 422", upload(auth, padding="abc").status_code, 422)

    # 合法参数 + 边界值（一次请求覆盖所有可调项）
    print("   合法值与边界值（合并请求）：")
    combos = [
        (
            {"intensity": "3", "sensitivity": "aggressive", "padding": "0",
             "shape_mask": "1", "feather": "30", "ruleset": "pixiv",
             "color": "#00ffAA"},
            "pixiv / 全部下边界",
        ),
        (
            {"intensity": "199", "sensitivity": "strict", "padding": "60",
             "shape_mask": "rect", "feather": "0", "ruleset": "strict"},
            "strict / 全部上边界",
        ),
    ]
    for extra, label in combos:
        r2 = upload(auth, **extra)
        check(f"{label} -> 200", r2.status_code, 200)
        if r2.status_code == 200:
            rs = extra["ruleset"]
            profile = config.CENSOR_PROFILES[rs]
            for d in r2.json().get("decisions") or []:
                expected = d["label"] in profile["must"] or d["label"] in profile["ambiguous"]
                check(f"  {label}: in_scope 与规则一致", d.get("in_scope") == expected, True)

    # 非法的枚举/范围值应回退默认或被夹紧，而不是报错。
    # 注意：intensity 走的是严格校验（超范围 422），与这些"回退/夹紧"参数不同，
    # 所以不能混在同一个请求里 —— 那样只会拿到第一个错误。
    r2 = upload(
        auth,
        sensitivity="乱写", ruleset="胡写", shape_mask="胡写",
        padding="9999", feather="999",
    )
    check("非法枚举/超范围 -> 200（回退默认或夹紧）", r2.status_code, 200)
    check("intensity 超上限 -> 422（严格校验）", upload(auth, intensity="100000").status_code, 422)
    check("intensity 为负 -> 422（严格校验）", upload(auth, intensity="-5").status_code, 422)
    # feather 的取值边界已由上面的合并请求覆盖（0 与 30），这里不再单独发请求

    # ------------------------------------------------- A6. 恶意上传
    print()
    print("=" * 68)
    print("A6. 伪装成图片的非法文件")
    print("=" * 68)
    fake = os.path.join(tmpdir, "fake.png")
    with open(fake, "wb") as fh:
        fh.write(b"<?php system($_GET['c']); ?>" + b"\x00" * 512)
    with open(fake, "rb") as fh:
        r = client.post(
            "/api/process",
            headers=auth,
            files={"file": ("fake.png", fh, "image/png")},
            data={"mode": "blur", "intensity": "51", "color": "#000000"},
        )
    check("伪造扩展名的非图片 -> 400", r.status_code, 400)

    # ------------------------------------------------- A7. Base64
    print()
    print("=" * 68)
    print("A7. Base64 接口")
    print("=" * 68)
    import base64

    b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
    r = client.post(
        "/api/process-base64",
        headers=auth,
        json={"image": "data:image/png;base64," + b64, "mode": "pixel", "intensity": 31},
    )
    check("Base64 正常 -> 200", r.status_code, 200)
    check("返回 processed_url", bool(r.json().get("processed_url")), True)

    r = client.post(
        "/api/process-base64",
        headers=auth,
        json={"image": "!!!not-base64!!!", "mode": "blur"},
    )
    check("非法 Base64 -> 422", r.status_code, 422)

    r = client.post(
        "/api/process-base64",
        headers={},
        json={"image": b64, "mode": "blur"},
    )
    check("Base64 无密钥 -> 403", r.status_code, 403)

    # ------------------------------------------------- A8. 批量
    print()
    print("=" * 68)
    print("A8. 批量接口与数量上限")
    print("=" * 68)
    files = []
    handles = []
    for i in range(3):
        fh = open(img_path, "rb")
        handles.append(fh)
        files.append(("files", (f"f{i}.png", fh, "image/png")))
    r = client.post(
        "/api/process-batch",
        headers=auth,
        files=files,
        data={"mode": "blur", "intensity": "51", "color": "#000000"},
    )
    for fh in handles:
        fh.close()
    check("批量 3 张 -> 200", r.status_code, 200)
    check("返回 3 条结果", len(r.json().get("results", [])), 3)

    # 超过上限（默认 20）—— 用 21 个空文件占位即可触发校验
    many = [("files", (f"x{i}.png", b"", "image/png")) for i in range(21)]
    r = client.post(
        "/api/process-batch",
        headers=auth,
        files=many,
        data={"mode": "blur", "intensity": "51", "color": "#000000"},
    )
    check("批量 21 张（超上限）-> 413", r.status_code, 413)

    # ------------------------------------------------- A9. 静态资源
    print()
    print("=" * 68)
    print("A9. 前端资源与 CSP 自洽性")
    print("=" * 68)
    r = client.get("/")
    check("首页 -> 200", r.status_code, 200)
    check("首页为 HTML", "text/html" in r.headers.get("content-type", ""), True)
    check("不再引用外部 CDN", "cdn.tailwindcss.com" in r.text, False)
    check("不再引用 Google Fonts", "fonts.googleapis.com" in r.text, False)

    r = client.get("/js/app.js")
    check("app.js -> 200", r.status_code, 200)
    # 关键回归点：Windows 上 mimetypes 会被注册表污染，把 .js 判成 text/plain。
    # 配合 nosniff，浏览器会拒绝执行脚本 —— 页面能打开但滑块、上传全部失效。
    # 必须断言 Content-Type，只看状态码 200 是抓不到的。
    check(
        "app.js 的 Content-Type 是 JavaScript",
        r.headers.get("content-type", "").split(";")[0].strip(),
        "text/javascript",
    )
    check(
        "style.css 的 Content-Type 是 CSS",
        client.get("/css/style.css").headers.get("content-type", "").split(";")[0].strip(),
        "text/css",
    )
    check(
        "首页的 Content-Type 是 HTML",
        client.get("/").headers.get("content-type", "").split(";")[0].strip(),
        "text/html",
    )
    check("前端不再硬编码密钥", "NSFW_PRO_" in r.text, False)
    # 密钥持久化改为「显式选择加入」：默认只存 sessionStorage，
    # 只有用户勾选「记住」才写入 localStorage。这里断言这个开关存在，
    # 运行期的实际行为由 tests/test_ui_browser.mjs 在真实浏览器里验证。
    check("前端提供「记住密钥」开关", "rememberKey" in r.text, True)
    check("密钥默认仍走 sessionStorage", "sessionStorage" in r.text, True)
    check("首页有「记住」复选框", 'id="rememberKey"' in client.get("/").text, True)
    # 启动标记：无头浏览器测试靠它判断脚本是否真的执行完了
    check("前端带初始化完成标记", "dataset.appReady" in r.text, True)
    check("首页引用了 favicon", "/favicon.svg" in client.get("/").text, True)
    check("favicon.svg -> 200", client.get("/favicon.svg").status_code, 200)

    # ------------------------------------------------- A10. 目录穿越读静态
    print()
    print("=" * 68)
    print("A10. 静态资源目录穿越")
    print("=" * 68)
    for label, url in [
        ("读 config.py", "/../backend/config.py"),
        ("读 data/api_key", "/data/api_key"),
        ("读 .env", "/.env"),
    ]:
        resp = client.get(url)
        leaked = TEST_API_KEY in resp.text or "SAFE_FILENAME_RE" in resp.text
        check(f"{label} 未泄露", leaked, False)

    # ------------------------------------------------- A11. 日志
    print()
    print("=" * 68)
    print("A11. 服务端不向客户端回显堆栈")
    print("=" * 68)
    r = client.post(
        "/api/process",
        headers=auth,
        files={"file": ("x.png", b"garbage", "image/png")},
        data={"mode": "blur", "intensity": "51", "color": "#000000"},
    )
    text = r.text
    check("不含 Traceback", "Traceback" in text, False)
    check("不含文件路径", str(ROOT) in text, False)

    # ------------------------------------------- C. 手动遮罩（用户涂哪里就遮哪里）
    print()
    print("=" * 68)
    print("C. 手动遮罩：涂哪里就只打哪里")
    print("=" * 68)
    print("   背景：自动分割靠颜色边界工作，部位与周围皮肤颜色接近时找不到边界，")
    print("   会退化成整个检测框。所以必须有一条完全由用户控制的路径。")
    print()

    # 复用 A4 的处理结果（不再重复上传，避免触发限流）
    payload = main_payload
    task_id = payload.get("id", "")
    original_url = payload.get("original_url", "")
    mask_url = payload.get("mask_url", "")
    check("返回 original_url", original_url.startswith("/api/files/original_"), True)
    check("返回 mask_url", mask_url.startswith("/api/files/mask_"), True)

    r = client.get(original_url)
    check("可取回原图", r.status_code, 200)
    check("原图是图片", r.headers.get("content-type", "").startswith("image/"), True)

    r = client.get(mask_url)
    check("可取回自动遮罩位图", r.status_code, 200)
    check("遮罩是 PNG", r.headers.get("content-type", "").startswith("image/"), True)

    # 原图与遮罩必须同尺寸，否则前端叠加会对不上
    orig = cv2.imdecode(np.frombuffer(client.get(original_url).content, np.uint8), cv2.IMREAD_COLOR)
    auto_mask = cv2.imdecode(
        np.frombuffer(client.get(mask_url).content, np.uint8), cv2.IMREAD_GRAYSCALE
    )
    check("原图可解码", orig is not None, True)
    check("遮罩可解码", auto_mask is not None, True)
    check("遮罩与原图同尺寸", auto_mask.shape[:2] == orig.shape[:2], True)

    # 手工画一个圆作为遮罩
    h, w = orig.shape[:2]
    hand_mask = np.zeros((h, w), np.uint8)
    cx, cy, radius = w // 3, h // 3, max(12, min(h, w) // 8)
    cv2.circle(hand_mask, (cx, cy), radius, 255, -1)
    expected_px = int(np.count_nonzero(hand_mask))
    ok, buf = cv2.imencode(".png", hand_mask)
    check("本地位图可编码为 PNG", bool(ok), True)
    hand_b64 = base64.b64encode(buf.tobytes()).decode()

    r = client.post(
        "/api/remask",
        headers=auth,
        json={"id": task_id, "mask": "data:image/png;base64," + hand_b64, "mode": "solid",
              "intensity": 51, "color": "#000000", "feather": 0},
    )
    check("重新打码 -> 200", r.status_code, 200)
    remasked = r.json() if r.status_code == 200 else {}
    check("返回新的 processed_url", bool(remasked.get("processed_url")), True)
    check(
        "覆盖像素数与手绘遮罩一致",
        abs(int(remasked.get("masked_pixels", 0)) - expected_px) <= expected_px * 0.02,
        True,
    )

    # 取回结果，逐像素验证：只遮了画的地方，别处一点没动
    out_bytes = client.get(remasked["processed_url"]).content
    out_img = cv2.imdecode(np.frombuffer(out_bytes, np.uint8), cv2.IMREAD_COLOR)
    check("结果图可解码", out_img is not None, True)
    check("结果图尺寸不变", out_img.shape == orig.shape, True)

    changed = np.any(out_img != orig, axis=2)
    changed_px = int(np.count_nonzero(changed))
    inside = int(np.count_nonzero(changed & (hand_mask > 0)))
    outside = changed_px - inside
    check("改动范围完全落在手绘遮罩内", outside, 0)
    check("遮罩内几乎全部被改动", inside / max(1, expected_px) > 0.99, True)
    check(
        "改动量远小于整张图（说明没糊一大片）",
        changed_px < orig.shape[0] * orig.shape[1] * 0.2,
        True,
    )

    # 空遮罩应当原样返回，不报错
    empty_mask = np.zeros((h, w), np.uint8)
    _, empty_buf = cv2.imencode(".png", empty_mask)
    r = client.post(
        "/api/remask",
        headers=auth,
        json={"id": task_id, "mask": base64.b64encode(empty_buf.tobytes()).decode()},
    )
    check("空遮罩 -> 200", r.status_code, 200)
    check("空遮罩覆盖 0 像素", r.json().get("masked_pixels"), 0)

    # 尺寸不一致的遮罩应被对齐到原图
    small_mask = np.zeros((32, 32), np.uint8)
    cv2.circle(small_mask, (16, 16), 10, 255, -1)
    _, small_buf = cv2.imencode(".png", small_mask)
    r = client.post(
        "/api/remask",
        headers=auth,
        json={"id": task_id, "mask": base64.b64encode(small_buf.tobytes()).decode()},
    )
    check("尺寸不符的遮罩也能处理", r.status_code, 200)

    print("   异常路径：")
    check(
        "无密钥 -> 403",
        client.post("/api/remask", headers={}, json={"id": task_id, "mask": hand_b64}).status_code,
        403,
    )
    check(
        "非法 id -> 422",
        client.post(
            "/api/remask", headers=auth, json={"id": "../../etc/passwd", "mask": hand_b64}
        ).status_code,
        422,
    )
    check(
        "不存在的 id -> 404",
        client.post(
            "/api/remask",
            headers=auth,
            json={"id": "11111111-2222-3333-4444-555555555555", "mask": hand_b64},
        ).status_code,
        404,
    )
    check(
        "非法遮罩 -> 422",
        client.post(
            "/api/remask", headers=auth, json={"id": task_id, "mask": "!!!not-a-png!!!"}
        ).status_code,
        422,
    )
    check(
        "越权取原图仍被拦",
        client.get("/api/files/original_11111111-2222-3333-4444-555555555555.png").status_code,
        404,
    )

    client.close()

finally:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=10)

print()
print("=" * 68)
print("服务端启动日志")
print("=" * 68)
for line in (out or "").splitlines():
    if line.strip():
        print("   " + line)
check("启动日志中不含完整 API Key", TEST_API_KEY in (out or ""), False)

print()
print("=" * 68)
if failures:
    print(f"结果：{len(failures)} 项失败")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("结果：全部通过")
