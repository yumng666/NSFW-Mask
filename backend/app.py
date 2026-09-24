"""NSFW Mask — 加固版 API 服务。

原版的漏洞与加固对照（详细说明见 SECURITY.md）：

| 原版问题 | 本版处理 |
|---|---|
| `GET /api/files/{filename}` 只拦 `".."` 和 `"/"`，Windows 上被反斜杠绕过，可读任意文件 | 严格文件名白名单 + realpath 容器校验 + HMAC 下载令牌 |
| 硬编码默认 API Key，且在三个文件里公开 | 无默认值；环境变量缺失时自动生成随机密钥并以 0600 落盘 |
| `==` 比较密钥，存在时序侧信道 | `hmac.compare_digest` 常量时间比较 |
| `trusted_hosts="*"` 信任任意代理，可以伪造 XFF 绕过限流 | 默认只信任回环地址，可配置 |
| `allow_origins=["*"]` | 默认同源，需要跨域必须显式配置 |
| 硬编码 `host="0.0.0.0"`，开局暴露到局域网 | 默认 `127.0.0.1`，对外需显式开启 |
| `mode`/`intensity`/`color` 完全不校验 | 服务端白名单与范围校验 |
| 清理只在下一次请求时触发，服务闲置则原图永久驻留 | 后台定时任务 |
| 批量接口无数量上限 | 单次上限可配，默认 20 |
| 同步推理阻塞事件循环，并发形同虚设 | 推理放到工作线程，配协程信号量 |
| Deep Scan 瓦片写 `_tile_<pid>.png` 到工作目录 | 内存推理，降级时用唯一临时文件 |
"""

from __future__ import annotations

import asyncio
import base64
import glob
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List

import cv2
import numpy as np
import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend import config
from backend.engines import (
    ImageProcessor,
    ImageTooLargeError,
    LocalNSFWClassifier,
    LocalNudeNetDetector,
    read_image,
)
from backend.security import (
    SecretStore,
    constant_time_equals,
    is_safe_filename,
    resolve_within_subfolder,
    key_hint,
    make_download_token,
    resolve_within,
    verify_download_token,
)

secrets_store = SecretStore(config.API_KEY_FILE, config.SIGNING_KEY_FILE)

# 必须在任何静态文件请求之前完成 MIME 修正。
# 否则在 .js 被系统 MIME 表判定为 text/plain 的机器上，/js/app.js 会以
# text/plain 返回，叠加 X-Content-Type-Options: nosniff 后浏览器拒绝执行，
# 表现为"页面能打开但滑块拖不动、上传点不动"。
_mime_before = config.register_mime_types()


# ==========================================================================
# 清理：后台定时执行，而不是只在下一次请求时顺手跑一次
# ==========================================================================
def cleanup_old_files() -> int:
    """删除超过保留期的上传件、中间产物和残留临时文件。返回删除数量。

    注意：**不包括 OUTPUT_DIR**。用户明确要求把打码结果存到 output/
    就是为了留着，自动清理会直接毁掉这个用途。
    """
    cutoff = time.time() - config.RETENTION_SECONDS
    removed = 0
    for folder in (config.UPLOAD_DIR, config.WORK_DIR, config.TMP_DIR):
        for entry in glob.glob(os.path.join(str(folder), "*")):
            try:
                if os.path.isfile(entry) and os.stat(entry).st_mtime < cutoff:
                    os.remove(entry)
                    removed += 1
            except OSError:
                continue
    if removed:
        print(f"[Cleanup] 已清理 {removed} 个过期文件")
    return removed


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_old_files)
        except Exception as exc:  # 清理失败不应拖垮服务
            print(f"[Cleanup] 出错: {exc}")
        await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)


# ==========================================================================
# 引擎：线程安全地惰性加载，推理不阻塞事件循环
# ==========================================================================
_engines: tuple | None = None
_engine_load_lock: asyncio.Lock | None = None
_processor = ImageProcessor()


def _load_engines_sync() -> tuple:
    global _engines
    if _engines is None:
        print("[NSFW Mask] 正在加载本地模型…")
        detector = LocalNudeNetDetector(config.DETECTOR_MODEL_RESOLVED or None)
        classifier = LocalNSFWClassifier()
        _engines = (detector, classifier)
        # SAM 分割器同步预热：轮廓贴合（shape_mask）依赖它，加载失败
        # 只是打印警告并退回 GrabCut，不影响服务启动。
        from backend.engines import get_sam

        get_sam()
        print("[NSFW Mask] 模型就绪。")
    return _engines


async def get_engines() -> tuple:
    """惰性加载模型。用锁避免并发首请求重复加载。"""
    global _engine_load_lock
    if _engines is not None:
        return _engines
    if _engine_load_lock is None:
        _engine_load_lock = asyncio.Lock()
    async with _engine_load_lock:
        if _engines is None:
            await asyncio.to_thread(_load_engines_sync)
    return _engines


# 限制同时进行的重推理任务数（协程层面）
_process_gate = asyncio.Semaphore(config.MAX_CONCURRENCY)


# ==========================================================================
# 鉴权
# ==========================================================================
async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> str:
    """校验 X-API-KEY。使用常量时间比较，且不把密钥写进日志。"""
    expected = secrets_store.api_key
    if not constant_time_equals(x_api_key or "", expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing API Key.",
            headers={"WWW-Authenticate": "X-API-KEY"},
        )
    return x_api_key  # type: ignore[return-value]


# ==========================================================================
# 参数校验
# ==========================================================================
def validate_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in config.ALLOWED_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode 必须是 {sorted(config.ALLOWED_MODES)} 之一。",
        )
    return mode


def validate_intensity(raw: int | str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="intensity 必须是整数。") from None
    if not config.MIN_INTENSITY <= value <= config.MAX_INTENSITY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"intensity 必须在 {config.MIN_INTENSITY}–{config.MAX_INTENSITY} 之间。"
            ),
        )
    return value


def validate_color(color: str) -> str:
    color = (color or "").strip()
    if not config.HEX_COLOR_RE.match(color):
        raise HTTPException(
            status_code=422, detail="color 必须是 #RRGGBB 形式的十六进制颜色。"
        )
    return color.lower()


def validate_feather(value: int | str | None) -> int:
    """轮廓遮罩的边缘羽化像素数。超范围夹紧。"""
    try:
        parsed = int(value) if value is not None else config.DEFAULT_FEATHER
    except (TypeError, ValueError):
        return config.DEFAULT_FEATHER
    return max(config.MIN_FEATHER, min(config.MAX_FEATHER, parsed))


def validate_shape_mask(value) -> bool:
    """遮罩形状开关。表单里传字符串，JSON 里传布尔，两种都接。

    也是"非法值回退默认"而不是报错 —— 前端可能来自旧缓存。
    """
    if value is None or value == "":
        return config.DEFAULT_SHAPE_MASK
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "shape", "contour"}:
        return True
    if text in {"0", "false", "no", "off", "rect"}:
        return False
    return config.DEFAULT_SHAPE_MASK


def validate_sensitivity(value: str | None) -> str:
    """检测灵敏度。非法值一律回退到默认档，而不是报错 ——
    前端可能来自旧缓存，没必要因此打断用户的处理流程。"""
    value = (value or "").strip().lower()
    if value not in config.ALLOWED_SENSITIVITY:
        return config.DEFAULT_SENSITIVITY
    return value


def validate_mask_inset(value: int | str | None) -> int:
    """遮罩内收百分比。非法值回退默认。"""
    try:
        parsed = int(value) if value is not None else config.DEFAULT_MASK_INSET
    except (TypeError, ValueError):
        return config.DEFAULT_MASK_INSET
    return max(config.MIN_MASK_INSET, min(config.MAX_MASK_INSET, parsed))


def validate_ruleset(value: str | None) -> str:
    """打码规则预设（pixiv / strict）。非法值回退默认。"""
    value = (value or "").strip().lower()
    if value not in config.ALLOWED_RULESETS:
        return config.DEFAULT_RULESET
    return value


@dataclass(frozen=True)
class ProcessOptions:
    """一次处理请求的全部可调项。

    参数已经涨到八个，用散装参数传递容易在调用处写错顺序，
    统一收进这个不可变对象里。
    """

    mode: str
    intensity: int
    color: str
    sensitivity: str
    padding: int
    shape_mask: bool
    feather: int
    ruleset: str
    subfolder: str = ""
    mask_inset: int = config.DEFAULT_MASK_INSET


def build_options(
    mode,
    intensity,
    color,
    sensitivity=None,
    padding=None,
    shape_mask=None,
    feather=None,
    ruleset=None,
    subfolder=None,
    mask_inset=None,
) -> ProcessOptions:
    """校验并归一化用户参数。所有校验集中在这里，三个接口共用。"""
    return ProcessOptions(
        mode=validate_mode(mode),
        intensity=validate_intensity(intensity),
        color=validate_color(color),
        sensitivity=validate_sensitivity(sensitivity),
        padding=validate_padding(padding),
        shape_mask=validate_shape_mask(shape_mask),
        feather=validate_feather(feather),
        ruleset=validate_ruleset(ruleset),
        subfolder=_sanitize_subfolder(subfolder),
        mask_inset=validate_mask_inset(mask_inset),
    )


def validate_padding(value: int | str | None) -> int:
    """遮罩向外扩张的像素数。超范围直接夹紧到边界。

    非法数字（如 "abc"）会先被 FastAPI 的表单类型校验收掉并返回 422，
    走不到这里；下面的兜底只是防御其他调用方（例如未来的内部调用）。
    """
    try:
        parsed = int(value) if value is not None else config.DEFAULT_MASK_PADDING
    except (TypeError, ValueError):
        return config.DEFAULT_MASK_PADDING
    return max(config.MIN_MASK_PADDING, min(config.MAX_MASK_PADDING, parsed))


# ==========================================================================
# 核心处理（同步，在线程中执行）
# ==========================================================================
def _process_one_sync(
    input_path: Path,
    output_path: Path,
    options: ProcessOptions,
    mask_path: Path | None = None,
) -> dict:
    """对已经落盘的图片执行检测 + 分类 + 打码。"""
    detector, classifier = _engines  # type: ignore[misc]
    scores = classifier.classify(str(input_path))
    detections = detector.detect(str(input_path))
    _, blur_count, annotations = _processor.process(
        str(input_path),
        detections,
        str(output_path),
        mode=options.mode,
        intensity=options.intensity,
        nsfw_scores=scores,
        color_hex=options.color,
        sensitivity=options.sensitivity,
        mask_padding=options.padding,
        shape_mask=options.shape_mask,
        feather=options.feather,
        mask_output_path=str(mask_path) if mask_path else None,
        ruleset=options.ruleset,
        mask_inset=options.mask_inset,
    )
    return {
        "scores": scores,
        "detections": detections,
        "blur_count": blur_count,
        "decisions": annotations,
    }


def _save_upload(data, dest: Path) -> int:
    """把上传内容写到目标路径，返回字节数。"""
    try:
        if hasattr(data, "read"):
            with open(dest, "wb") as fh:
                shutil.copyfileobj(data, fh)
        else:
            with open(dest, "wb") as fh:
                fh.write(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="保存上传文件失败。") from exc
    return dest.stat().st_size


def _download_url(filename: str) -> str:
    """生成带签名令牌的下载地址，令牌与文件名绑定且有有效期。"""
    token = make_download_token(filename, secrets_store.signing_key, config.TOKEN_TTL)
    return f"/api/files/{filename}?t={token}"


def _normalized_ext(filename: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext if ext in config.ALLOWED_EXTS else ".jpg"


def _output_filename(original_name: str | None, ext: str) -> str:
    """给 output/ 里的结果起一个人类可读且**可复现**的名字。

    形如 masked_photo.jpg。刻意不带时间戳和随机后缀：
    同一张图重复处理时直接覆盖上一次的结果，不会堆积一堆副本。
    原始文件名里的 Unicode 字符（中文等）保留 —— 白名单与防穿越
    由字符类（不含分隔符与点号）和 realpath 包含性检查共同保证。
    """
    stem = os.path.splitext(original_name or "image")[0]
    cleaned = re.sub(r"[^\w-]+", "_", stem, flags=re.UNICODE).strip("_")[:60]
    return f"masked_{cleaned or 'image'}{ext}"


def _sanitize_subfolder(value: str | None) -> str:
    """清洗用户给的文件夹名，保留原名（含中文等 Unicode 字符）。

    只替换会造成路径问题的字符：各类分隔符、Windows 非法字符、
    点号（防 ".." 与隐藏目录）和控制字符。名字里出现什么就输出什么，
    上传「我的相册」结果就在 output/我的相册/ 之下。
    """
    text = (value or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f.]+', "_", text).strip(" ._")
    return cleaned[:80].strip(" ._") or ""


_SAFE_SEGMENT = re.compile(r"^[\w][\w -]{0,79}$")


def _SAFE_SEGMENT_OK(segment: str) -> bool:
    return bool(_SAFE_SEGMENT.match(segment))


def _artifact_path(relative: str) -> Path | None:
    """定位结果文件：可能在 output/ 的子文件夹里，也可能是中间产物。"""
    sub_resolved = resolve_within_subfolder(config.OUTPUT_DIR, relative)
    if sub_resolved is not None:
        return sub_resolved
    if "/" not in relative:
        flat = resolve_within(config.WORK_DIR, relative)
        if flat is not None:
            return flat
    return None


async def handle_single_image(
    file_data,
    filename: str | None,
    options: ProcessOptions,
) -> dict:
    """处理单张图片：落盘 → 校验可解码 → 推理 → 打码 → 返回带令牌的地址。"""
    async with _process_gate:
        await get_engines()

        file_id = str(uuid.uuid4())
        ext = _normalized_ext(filename)
        input_path = config.UPLOAD_DIR / f"{file_id}{ext}"
        # 最终结果写到 output/（拖入文件夹时进同名子文件夹，不自动清理）
        output_name = _output_filename(filename, ext)
        if options.subfolder:
            output_name = f"{options.subfolder}/{output_name}"
            (config.OUTPUT_DIR / options.subfolder).mkdir(parents=True, exist_ok=True)
        output_path = config.OUTPUT_DIR / output_name
        # 原图与遮罩位图是中间产物，放 data/processed/，受保留期清理
        original_name = f"original_{file_id}{ext}"
        original_path = config.WORK_DIR / original_name
        mask_name = f"mask_{file_id}.png"
        mask_path = config.WORK_DIR / mask_name

        size = await asyncio.to_thread(_save_upload, file_data, input_path)
        if size == 0:
            input_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="上传的文件为空。")

        try:
            if await asyncio.to_thread(read_image, str(input_path)) is None:
                raise HTTPException(
                    status_code=400,
                    detail="无法解码为图片，请上传 JPG / PNG / WEBP。",
                )

            result = await asyncio.to_thread(
                _process_one_sync,
                input_path,
                output_path,
                options,
                mask_path,
            )
            # 原图另存一份到结果目录，等保留期到了由清理任务统一删除
            await asyncio.to_thread(shutil.copyfile, input_path, original_path)
        except HTTPException:
            raise
        except ImageTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            print(f"[Process] 处理失败: {exc!r}")
            raise HTTPException(status_code=500, detail="图片处理失败。") from exc
        finally:
            # uploads 里的临时副本处理完立即删除
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

        return {
            "id": file_id,
            "filename": filename or input_path.name,
            "scores": result["scores"],
            "detections": result["detections"],
            "blur_count": result["blur_count"],
            # 逐个检测框的处置说明，前端据此显示"为什么这块没被遮住"
            "decisions": result["decisions"],
            "processed_url": _download_url(output_name),
            # 供手动改遮罩用：原图 + 自动遮罩位图
            "original_url": _download_url(original_name),
            "mask_url": _download_url(mask_name),
        }


# ==========================================================================
# 应用
# ==========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()

    if secrets_store.api_key_was_generated:
        print("=" * 62)
        print("[安全提示] 未检测到 NSFW_API_KEY，已自动生成随机密钥并写入：")
        print(f"           {config.API_KEY_FILE}")
        print("           请在 Web 界面右上角填入该密钥，或设置环境变量 NSFW_API_KEY。")
        print("=" * 62)
    else:
        print(f"[NSFW Mask] 使用环境变量中的 API Key（{key_hint(secrets_store.api_key)}）")

    # 自检：如果本机 MIME 表把 .js 判错了，说明刚才是靠 register_mime_types
    # 兜回来的。明确告知，免得以后换机器再踩同一个坑。
    js_before = _mime_before.get(".js", (None, None))[0]
    if js_before != "text/javascript":
        print("=" * 62)
        print("[提示] 本机系统 MIME 表把 .js 判定为：", js_before)
        print("       已强制修正为 text/javascript。若不修正，浏览器会因")
        print("       Content-Type 不是 JS 而拒绝执行前端脚本，导致")
        print("       页面能打开但滑块、上传等交互全部失效。")
        print("=" * 62)

    if config.HOST not in ("127.0.0.1", "localhost", "::1"):
        print("=" * 62)
        print(f"[安全警告] 正在监听 {config.HOST}，服务将对外可达。")
        print("           请务必在前面部署反向代理完成 TLS 与访问控制，")
        print("           并确认防火墙规则符合预期。")
        print("=" * 62)

    # 启动时先清一次历史残留，之后交给定时任务
    await asyncio.to_thread(cleanup_old_files)
    cleanup_task = asyncio.create_task(_cleanup_loop())

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="NSFW Mask (Hardened)",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应补上安全响应头。"""

    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # 前端完全自包含（无外部 CDN），可以用很严的 CSP
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; "
            "img-src 'self' data: blob:; "
            "style-src 'self'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "frame-ancestors 'none'",
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-KEY", "Content-Type"],
    )

# 只信任明确配置的代理，避免 X-Forwarded-For 被伪造后绕过限流
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts="*" if config.TRUST_ALL_PROXIES else config.TRUSTED_PROXIES,
)


# ==========================================================================
# 接口
# ==========================================================================
@app.post("/api/process")
@limiter.limit(config.RATE_LIMIT_PROCESS)
async def process_image(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(config.DEFAULT_MODE),
    intensity: int = Form(config.DEFAULT_INTENSITY),
    color: str = Form(config.DEFAULT_COLOR),
    sensitivity: str = Form(config.DEFAULT_SENSITIVITY),
    padding: int = Form(config.DEFAULT_MASK_PADDING),
    shape_mask: str = Form("" if config.DEFAULT_SHAPE_MASK else "0"),
    feather: int = Form(config.DEFAULT_FEATHER),
    ruleset: str = Form(config.DEFAULT_RULESET),
    subfolder: str = Form(""),
    api_key: str = Depends(verify_api_key),
):
    options = build_options(
        mode, intensity, color, sensitivity, padding, shape_mask, feather, ruleset,
        subfolder,
    )

    position = file.file.tell()
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(position)
    if size > config.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，上限 {config.MAX_FILE_SIZE // 1024 // 1024} MB。",
        )

    return await handle_single_image(file.file, file.filename, options)


@app.post("/api/process-batch")
@limiter.limit("5/minute")
async def process_batch(
    request: Request,
    files: List[UploadFile] = File(...),
    mode: str = Form(config.DEFAULT_MODE),
    intensity: int = Form(config.DEFAULT_INTENSITY),
    color: str = Form(config.DEFAULT_COLOR),
    sensitivity: str = Form(config.DEFAULT_SENSITIVITY),
    padding: int = Form(config.DEFAULT_MASK_PADDING),
    shape_mask: str = Form("" if config.DEFAULT_SHAPE_MASK else "0"),
    feather: int = Form(config.DEFAULT_FEATHER),
    ruleset: str = Form(config.DEFAULT_RULESET),
    subfolder: str = Form(""),
    api_key: str = Depends(verify_api_key),
):
    options = build_options(
        mode, intensity, color, sensitivity, padding, shape_mask, feather, ruleset
    )
    if not files:
        raise HTTPException(status_code=422, detail="未收到任何文件。")
    if len(files) > config.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"单次最多处理 {config.MAX_BATCH_FILES} 个文件。",
        )

    for f in files:
        f.file.seek(0, os.SEEK_END)
        if f.file.tell() > config.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413, detail=f"文件 “{f.filename}” 超过大小上限。"
            )
        f.file.seek(0)

    results = await asyncio.gather(
        *(handle_single_image(f.file, f.filename, options) for f in files),
        return_exceptions=True,
    )

    payload = []
    for item in results:
        if isinstance(item, HTTPException):
            payload.append({"error": item.detail})
        elif isinstance(item, BaseException):
            print(f"[Batch] 单项失败: {item!r}")
            payload.append({"error": "处理失败。"})
        else:
            payload.append(item)
    return {"results": payload}


class ProcessRequest(BaseModel):
    image: str = Field(..., description="Base64 或 data URL")
    mode: str = config.DEFAULT_MODE
    intensity: int = config.DEFAULT_INTENSITY
    color: str = config.DEFAULT_COLOR
    sensitivity: str = config.DEFAULT_SENSITIVITY
    padding: int = config.DEFAULT_MASK_PADDING
    shape_mask: bool = config.DEFAULT_SHAPE_MASK
    feather: int = config.DEFAULT_FEATHER
    ruleset: str = config.DEFAULT_RULESET
    return_base64: bool = False


@app.post("/api/process-base64")
@limiter.limit(config.RATE_LIMIT_PROCESS)
async def process_base64(
    request: Request,
    req: ProcessRequest,
    api_key: str = Depends(verify_api_key),
):
    options = build_options(
        req.mode,
        req.intensity,
        req.color,
        req.sensitivity,
        req.padding,
        req.shape_mask,
        req.feather,
        req.ruleset,
    )

    if len(req.image) > config.MAX_BASE64_CHARS:
        raise HTTPException(status_code=413, detail="Base64 数据过大。")

    import base64
    import binascii

    raw_b64 = req.image.split(",", 1)[1] if "," in req.image else req.image
    try:
        blob = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="Base64 解码失败。") from None
    if not blob:
        raise HTTPException(status_code=400, detail="解码后内容为空。")
    if len(blob) > config.MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="图片数据过大。")

    async with _process_gate:
        await get_engines()
        file_id = str(uuid.uuid4())
        input_path = config.UPLOAD_DIR / f"{file_id}.jpg"
        output_name = _output_filename(None, ".jpg")
        output_path = config.OUTPUT_DIR / output_name

        await asyncio.to_thread(_save_upload, blob, input_path)
        try:
            if await asyncio.to_thread(read_image, str(input_path)) is None:
                raise HTTPException(status_code=400, detail="无法解码为图片。")
            result = await asyncio.to_thread(
                _process_one_sync,
                input_path,
                output_path,
                options,
            )
        except HTTPException:
            raise
        except ImageTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            print(f"[ProcessBase64] 处理失败: {exc!r}")
            raise HTTPException(status_code=500, detail="图片处理失败。") from exc
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

    payload = {
        "id": file_id,
        "scores": result["scores"],
        "detections": result["detections"],
        "blur_count": result["blur_count"],
        "decisions": result["decisions"],
    }
    if req.return_base64:
        data = await asyncio.to_thread(output_path.read_bytes)
        payload["processed_image"] = (
            "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        )
    else:
        payload["processed_url"] = _download_url(output_name)
    return payload


# --------------------------------------------------------------------------
# 手动改遮罩
# --------------------------------------------------------------------------
class RemaskRequest(BaseModel):
    id: str = Field(..., description="/api/process 返回的任务 id")
    mask: str = Field(..., description="Base64 或 data URL 的单通道 PNG，白色=需要打码")
    mode: str = config.DEFAULT_MODE
    intensity: int = config.DEFAULT_INTENSITY
    color: str = config.DEFAULT_COLOR
    feather: int = config.DEFAULT_FEATHER
    output_name: str = Field("", description="传入 /api/process 返回的文件名可覆盖原结果")


def _decode_mask_png(data: str) -> np.ndarray | None:
    """解码前端传来的遮罩 PNG，返回单通道灰度图。"""
    payload = (data or "").strip()
    if payload.lower().startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    buf = np.frombuffer(raw, np.uint8)
    if buf.size == 0:
        return None
    decoded = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if decoded is None or decoded.size == 0:
        return None
    return decoded


@app.post("/api/remask")
@limiter.limit(config.RATE_LIMIT_PROCESS)
async def remask(
    request: Request,
    req: RemaskRequest,
    api_key: str = Depends(verify_api_key),
):
    """按用户手工涂抹的遮罩重新打码。

    自动分割在"部位与周围皮肤颜色相近"时并不可靠（GrabCut 靠颜色分布
    区分前景背景，肤色一致就找不到边界）。所以这里给用户一条完全可控的
    路径：涂哪里就只打哪里，不依赖模型的判断。
    """
    try:
        file_id = str(uuid.UUID((req.id or "").strip()))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail="无效的任务 id。")

    original_path = None
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = config.WORK_DIR / f"original_{file_id}{suffix}"
        if candidate.exists():
            original_path = candidate
            break
    if original_path is None:
        raise HTTPException(
            status_code=404, detail="原图不存在或已过期，请重新上传处理。"
        )

    mask = await asyncio.to_thread(_decode_mask_png, req.mask)
    if mask is None or mask.size == 0:
        raise HTTPException(status_code=422, detail="遮罩解码失败。")

    mode = validate_mode(req.mode)
    intensity = validate_intensity(req.intensity)
    color = validate_color(req.color)
    feather = validate_feather(req.feather)

    # 客户端传回了原输出名 → 覆盖同一份文件（手动改遮罩不会越改越多副本）
    wanted = (req.output_name or "").replace("\\", "/").strip("/")
    segments = [p for p in wanted.split("/") if p]
    valid_rel = (
        segments
        and len(segments) <= 2
        and segments[-1].startswith("masked_")
        and all(is_safe_filename(seg) or _SAFE_SEGMENT_OK(seg) for seg in segments)
    )
    if valid_rel:
        target = config.OUTPUT_DIR.joinpath(*segments)
        if target.exists():
            output_name = wanted
        else:
            output_name = _output_filename(None, ".png")
    else:
        output_name = _output_filename(None, ".png")
    output_path = config.OUTPUT_DIR / output_name

    try:
        async with _process_gate:
            _, masked_pixels = await asyncio.to_thread(
                _processor.apply_user_mask,
                str(original_path),
                mask,
                str(output_path),
                mode,
                intensity,
                color,
                feather,
            )
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        print(f"[Remask] 处理失败: {exc!r}")
        raise HTTPException(status_code=500, detail="重新打码失败。") from exc

    return {
        "processed_url": _download_url(output_name),
        "masked_pixels": masked_pixels,
    }


@app.get("/api/local-key")
async def local_key(request: Request):
    """把 API Key 交给本机浏览器，供「一键填写」使用。

    仅响应来自回环地址的请求。这是一个明确的权衡：它让"不用去命令行
    翻密钥"成为可能，代价是任何本机进程都能拿到密钥 —— 而在服务只监听
    127.0.0.1 的前提下，本机进程本就能直接读 data/api_key，所以并未
    引入新的暴露面。**一旦把服务暴露到 0.0.0.0，必须在反向代理上封掉
    这个路径**，否则等于把密钥公开。
    """
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="仅限本机访问。")
    return {"api_key": secrets_store.api_key}


@app.get("/api/files/{file_path:path}")
async def get_file(
    file_path: str,
    t: str = Query("", description="下载令牌"),
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
):
    """取回打码后的图片。

    与原版的区别：
    1. 文件名必须严格匹配服务端自身的命名格式（白名单，任何分隔符都进不来）；
    2. 再用 realpath 确认解析结果仍位于允许的目录内（挡住符号链接等）；
    3. 必须携带有效的 HMAC 下载令牌，或合法的 API Key。

    文件可能在两个目录：最终结果在 output/，中间产物在 data/processed/。
    依次查找，但无论在哪个目录，resolve_within 的包含性检查都会执行。

    原版这里既没有鉴权，路径校验又漏掉了 Windows 反斜杠，因此可以读任意文件。
    """
    # 统一路径分隔符并去掉首尾斜杠，穿越载荷到不了白名单那一步
    rel = (file_path or "").replace("\\", "/").strip("/")
    path = _artifact_path(rel)
    if path is None:
        # 统一返回 404，不区分"名字非法"和"文件不存在"，避免多余信息泄露
        raise HTTPException(status_code=404, detail="File not found.")

    authorized = verify_download_token(
        rel, t, secrets_store.signing_key
    ) or constant_time_equals(x_api_key or "", secrets_store.api_key)
    if not authorized:
        raise HTTPException(status_code=403, detail="Invalid or missing token.")

    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/health")
async def health():
    """健康检查。只暴露非敏感信息。"""
    return {
        "status": "ok",
        "version": app.version,
        "engines_loaded": _engines is not None,
        "deep_scan": config.DEEP_SCAN,
        "max_concurrency": config.MAX_CONCURRENCY,
        "retention_seconds": config.RETENTION_SECONDS,
    }


# 前端静态资源挂在最后，避免吃掉 /api 前缀
app.mount(
    "/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend"
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：内部异常只记服务端日志，不回显堆栈给调用方。"""
    print(f"[Unhandled] {request.method} {request.url.path}: {exc!r}")
    return JSONResponse(status_code=500, content={"detail": "内部错误。"})


if __name__ == "__main__":
    config.ensure_dirs()
    uvicorn.run(app, host=config.HOST, port=config.PORT)
