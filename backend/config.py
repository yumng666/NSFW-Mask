"""集中配置。

所有可调项都从环境变量读取，并给出**安全默认值**：
不安全的方向（对外监听、通配 CORS、信任任意代理）都必须显式开启，
而不是像原始版本那样默认打开。

这个模块自身不产生副作用，除了创建数据目录。
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("NSFW_DATA_DIR") or (BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
# 中间产物：用户上传的原图副本、自动遮罩位图。受保留期自动清理。
WORK_DIR = DATA_DIR / "processed"
TMP_DIR = DATA_DIR / "tmp"

# 最终打码结果。放在项目根的 output/ 下，用户可以直接到这个目录取文件。
# 这个目录**不做自动清理** —— 用户明确要求把结果存这里，就是想留着。
# 可用 NSFW_OUTPUT_DIR 指到别处；给空字符串或相对路径时按相对 BASE_DIR 解析。
OUTPUT_DIR = Path(
    os.getenv("NSFW_OUTPUT_DIR") or str(BASE_DIR / "output")
).resolve()
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = (BASE_DIR / OUTPUT_DIR).resolve()

FRONTEND_DIR = BASE_DIR / "frontend"


def ensure_dirs() -> None:
    """创建运行所需目录。幂等，可在启动时重复调用。"""
    for d in (DATA_DIR, UPLOAD_DIR, WORK_DIR, OUTPUT_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# MIME 类型
# --------------------------------------------------------------------------
# 为什么必须显式注册：
#
# Python 的 mimetypes 在 Windows 上除了内置表，还会读取注册表里
# HKEY_CLASSES_ROOT\<ext>\Content Type 的值。只要机器上装过某个软件把 .js
# 关联成 text/plain（这在装了某些编辑器/打包工具后很常见），
# mimetypes.guess_type("app.js") 就会返回 text/plain。
#
# Starlette 的 StaticFiles 正是用 mimetypes 决定响应头，于是 /js/app.js
# 会被以 Content-Type: text/plain 返回。叠加我们设置的
# X-Content-Type-Options: nosniff 之后，浏览器会直接拒绝执行该脚本。
#
# 症状很有迷惑性：页面能正常打开、CSS 样式也正确（因为 .css 没被污染），
# 但**所有 JavaScript 交互全部失效** —— 滑块拖不动、点上传没反应。
# 日志里一切正常，静态文件都是 200，看不出任何异常。
#
# 所以这里不信任系统 MIME 表，对前端用到的扩展名做强制覆盖。
MIME_OVERRIDES: dict[str, str] = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def register_mime_types() -> dict[str, tuple]:
    """强制覆盖 MIME 类型，返回注册前后的实际取值，便于启动时自检。

    必须在任何静态文件请求发生之前调用（本模块的 app.py 在导入期就会调用）。
    """
    before: dict[str, tuple] = {}
    # 先 init 一次，把注册表/系统表读进来，避免后续 guess_type 再触发惰性初始化
    mimetypes.init()
    for ext, mime in MIME_OVERRIDES.items():
        before[ext] = mimetypes.guess_type("x" + ext)
        mimetypes.add_type(mime, ext, strict=True)
    return before


# --------------------------------------------------------------------------
# 环境变量解析助手
# --------------------------------------------------------------------------
def _env_int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    if lo is not None and val < lo:
        return default
    if hi is not None and val > hi:
        return default
    return val


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    if raw.strip() == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# 网络：默认只监听本机回环
# --------------------------------------------------------------------------
# 原版硬编码 host="0.0.0.0"，等于开局就把服务暴露到整个局域网。
# 这里默认 127.0.0.1；要对外提供服务请显式设置 NSFW_HOST=0.0.0.0，
# 并且务必在前面套一层反向代理做 TLS 与访问控制。
HOST: str = os.getenv("NSFW_HOST", "127.0.0.1")
PORT: int = _env_int("NSFW_PORT", 8000, lo=1, hi=65535)

# --------------------------------------------------------------------------
# 反向代理信任
# --------------------------------------------------------------------------
# 只有来自这些地址的连接，其 X-Forwarded-For / X-Forwarded-Proto 才会被采信。
# 原版写成 trusted_hosts="*"，任何人伪造 XFF 都能绕过 slowapi 的限流。
# 默认只信任本机回环（即同机 Nginx / Cloudflare Tunnel 的场景）。
TRUSTED_PROXIES: list[str] = _env_list(
    "NSFW_TRUSTED_PROXIES", ["127.0.0.1", "::1"]
)

# 显式设为 "1" 才信任所有代理。仅在你完全清楚后果时使用。
TRUST_ALL_PROXIES: bool = os.getenv("NSFW_TRUST_ALL_PROXIES", "").strip() == "1"

# --------------------------------------------------------------------------
# CORS：默认关闭（同源）。前端与后端由同一个服务提供，不需要跨域。
# --------------------------------------------------------------------------
CORS_ORIGINS: list[str] = _env_list("NSFW_CORS_ORIGINS", [])

# --------------------------------------------------------------------------
# 上传与并发
# --------------------------------------------------------------------------
MAX_FILE_SIZE: int = _env_int("NSFW_MAX_FILE_SIZE", 10 * 1024 * 1024, lo=1024)
# Base64 体积约为原始字节的 4/3，留出余量
MAX_BASE64_CHARS: int = int(MAX_FILE_SIZE * 1.4)
# 批量接口单次允许的最大文件数（原版无上限，可被用来打满磁盘与内存）
MAX_BATCH_FILES: int = _env_int("NSFW_MAX_BATCH_FILES", 20, lo=1, hi=200)
MAX_CONCURRENCY: int = _env_int("NSFW_MAX_CONCURRENCY", 2, lo=1, hi=16)

# 处理接口限流。默认 240/分钟：文件夹批量处理时前端是逐张发请求的，
# 原来的 30/分钟会在 15 张之后就卡住。仍是本机 + 必须带 API Key 的前提。
RATE_LIMIT_PROCESS = os.getenv("NSFW_RATE_LIMIT_PROCESS", "240/minute").strip()
MAX_IMAGE_PIXELS: int = _env_int("NSFW_MAX_IMAGE_PIXELS", 4096 * 4096, lo=10000)

ALLOWED_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MODES: set[str] = {"blur", "pixel", "solid"}

# --------------------------------------------------------------------------
# 检测灵敏度
# --------------------------------------------------------------------------
# 决定一级部位（生殖器、肛门、裸露乳房、臀部）的打码门槛。
#
# 注意一个来自依赖库的硬限制：nudenet 的 _postprocess 里写死了
# `cv2.dnn.NMSBoxes(boxes, scores, 0.25, 0.45)`，也就是**分数低于 0.25 的框
# 在模型库内部就已经被丢掉了**，我们拿不到更低分的候选。
# 所以 "aggressive" 只能等于 0.25（模型下限），再往下设没有意义。
#
# 三个档位的取向：
#   strict     宁可漏，不可误杀 —— 适合内容大体安全、只想兜个底的场景
#   balanced   默认档，明显偏向"不漏"
#   aggressive 模型给出的任何生殖器/肛门疑似都遮
SENSITIVITY_THRESHOLDS: dict[str, float] = {
    # 检测层下限已降到 0.10，所以"高召回"不再等于下限，
    # 且新增一档"最高召回"真正用满模型给出的一切候选。
    # 交合/口交这类难姿势的给分普遍落在 0.10~0.30，
    # 用「标准」档时这部分会被门槛丢掉 —— 这就是"该遮的没遮"的主因之一。
    "max": 0.10,         # 最高召回：模型看到的一切（与检测下限相同）
    "aggressive": 0.12,  # 高召回
    "balanced": 0.30,    # 标准
    "strict": 0.60,      # 严格
}
ALLOWED_SENSITIVITY: set[str] = set(SENSITIVITY_THRESHOLDS)
DEFAULT_SENSITIVITY = "max"   # 打码工具宁可多遮；要少误杀再手动调回

# --------------------------------------------------------------------------
# 遮罩内收（解决"矩形框遮到周围皮肤"）
# --------------------------------------------------------------------------
# NudeNet 的检测框普遍偏松（框住部位之外还带一圈皮肤），而轮廓分割
# 在"部位与周围皮肤颜色相近"时找不到边界、只能退回矩形 ——
# 两件事叠加就是"矩形 + 糊到皮肤"。
# 内收按检测框宽高的百分比从四周向里缩，直接把松出来的那圈裁掉。
# 部位在框内大致居中，默认 15% 是安全的；不确定就从小往大调。
DEFAULT_MASK_INSET = _env_int("NSFW_MASK_INSET", 15, lo=0, hi=40)

# --------------------------------------------------------------------------
# 女性阴部的两级处理
# --------------------------------------------------------------------------
# NudeNet 没有「掰开 / 未掰开（一线天）」的粒度，两者都输出
# FEMALE_GENITALIA_EXPOSED。默认 0.10 = 检测下限：**所有检出的女性
# 阴部一律打码，一线天也不例外**（用户决定）。如果以后想恢复
# 「一线天放行」，把阈值调回 0.45 并配合 NSFW_FG_MIN_MASK 使用
# （该机制仍保留在代码里）。
_FG_THRESHOLD = float(os.getenv("NSFW_FG_THRESHOLD", "0.10"))

# 男性生殖器专用门槛。模型对它给分波动大（角度/遮挡/毛发影响明显），
# 觉得误检多就调高（如 0.35），漏检多就保持 0.10。
_MG_THRESHOLD = float(os.getenv("NSFW_MG_THRESHOLD", "0.10"))

# 疲软（未勃起）的阴茎常被模型误判成 MALE_GENITALIA_COVERED（"被遮挡"），
# 独立门槛可调；误遮穿着内裤的情况也调这里（或手动擦除）。
_MGC_THRESHOLD = float(os.getenv("NSFW_MGC_THRESHOLD", "0.10"))

# 每标签专属门槛表：值 > 灵敏度门槛时取更严者，否则灵敏度说了算。
# 默认都是 0.10（= 检测下限），即模型检出的都遮。
LABEL_THRESHOLDS: dict[str, float] = {
    "FEMALE_GENITALIA_EXPOSED": _FG_THRESHOLD,
    "MALE_GENITALIA_EXPOSED": _MG_THRESHOLD,
    "MALE_GENITALIA_COVERED": _MGC_THRESHOLD,
}

# 仅当 NSFW_FG_THRESHOLD > 检测下限时生效：分数落在中段的检出用
# 分割出的掩膜面积判定 —— 面积/框 ≥ 此值为实体区域（打码），
# 低于它（或分割失败）视为一线天（放行）。
FEMALE_GENITALIA_MIN_MASK_RATIO: float = float(
    os.getenv("NSFW_FG_MIN_MASK", "0.15")
)

# SAM 候选掩膜的最低「区分度」：掩膜内颜色与周围背景环的平均色差。
# 低于它说明没有任何候选真正分出了与背景不同的物体（比如 SAM 把
# 连片肤色区域整个当成物体），此时放弃轮廓分割退回兜底。
SAM_MIN_DISCRIMINANCE = _env_int("NSFW_SAM_MIN_DISC", 14, lo=4, hi=80)

# SAM 掩膜最多允许超出检测框多少像素 —— 框是模型认定的部位范围，
# 掩膜溢出到框外更远处通常是衣服、腿等不该遮的东西。
MASK_OUT_MARGIN = _env_int("NSFW_MASK_OUT_MARGIN", 8, lo=0, hi=60)
MIN_MASK_INSET = 0
MAX_MASK_INSET = 40

# --------------------------------------------------------------------------
# 打码规则预设
# --------------------------------------------------------------------------
# 不同平台对"什么必须打、什么可以不打"的规定不一样，所以做成预设而不是写死。
#
# 每个预设分两级：
#   must      —— 明确需要遮挡的部位。不受"防误杀"逻辑影响，只要分数过门槛就遮。
#   ambiguous —— 有歧义的部位。整图越"干净"越宽容（防误杀）。
#
# 两个预设都不是拍脑袋定的：
#
# pixiv —— 依《pixiv 投稿规范》：裸露的性器官与肛门必须打码；
#          臀部、胸部可以不打；被衣物等遮挡的器官不算裸露。
# strict —— 全部敏感部位都遮，适合"宁可多遮"的场景。
#
# **模型粒度的固有限制，必须让用户知道**（NudeNet 只到这一层）：
#   * 它分不清"掰开"与"没掰开"。一线天、未掰开的肛门在真实照片里
#     很可能仍被标成 *_EXPOSED 而被打码 —— 这是模型的判定，不是配置问题。
#   * 没有独立的"睾丸"标签，男性生殖器的框通常把阴囊一起框进去，
#     所以睾丸会被一起遮掉，无法只留阴茎。
#   这两类误差只能靠手动涂抹修（涂掉不该遮的 / 补上该遮的）。
CENSOR_PROFILES: dict[str, dict] = {
    "pixiv": {
        "label": "pixiv 投稿规范",
        "must": {
            "FEMALE_GENITALIA_EXPOSED",   # 女性阴部（含阴蒂）；掰开与否模型无法区分
            "MALE_GENITALIA_EXPOSED",     # 阴茎，勃起与否都遮（框通常含睾丸，无法单独保留）
            "MALE_GENITALIA_COVERED",     # 疲软的阴茎常被模型误判为此标签，一并遮
            "ANUS_EXPOSED",               # 肛门；掰开与否模型无法区分
        },
        "ambiguous": {
            # pixiv 规则下没有"视情况"的部位：
            # 臀部、胸部、一线天、被遮挡的器官一律不打
        },
    },
    "strict": {
        "label": "严格（全部敏感部位）",
        "must": {
            "FEMALE_GENITALIA_EXPOSED",
            "MALE_GENITALIA_EXPOSED",
            "MALE_GENITALIA_COVERED",
            "ANUS_EXPOSED",
            "FEMALE_BREAST_EXPOSED",
            "BUTTOCKS_EXPOSED",
        },
        "ambiguous": {
            "FEMALE_BREAST_COVERED",
            "BUTTOCKS_COVERED",
            "FEMALE_GENITALIA_COVERED",
            "BELLY_EXPOSED",
        },
    },
}
ALLOWED_RULESETS: set[str] = set(CENSOR_PROFILES)
DEFAULT_RULESET = "pixiv"

# 二级部位（被衣物遮挡的版本、腹部）的判定门槛。这些不影响"是否遮住敏感部位"，
# 只影响"是否把有歧义的区域也一起遮掉"，所以保守一些。
AMBIGUOUS_THRESHOLD = 0.55
AMBIGUOUS_THRESHOLD_HIGH_RISK = 0.30
AMBIGUOUS_STRICT_NORMAL = 0.75      # normal_score > 0.90 时
AMBIGUOUS_LOOSE_NORMAL = 0.75       # normal_score > 0.70 时

# --------------------------------------------------------------------------
# 遮罩扩张
# --------------------------------------------------------------------------
# 以检测框为基准向外扩张的像素数。0 = 严格只遮检测框本身。
# 原版硬编码 padding=40（高风险时），对小块区域来说会把遮罩面积放大好几倍，
# 这正是"一个照片糊一大片"的来源。现在改为用户可控，默认只留 8px 余量。
# 以检测框（矩形模式）或分割轮廓（贴合模式）为基准向外扩张的像素数。
# 0 = 严格只遮边界本身。
#
# 默认给 3 而不是 0：分割出来的轮廓可能比部位真实边缘内缩 1~2 像素，
# 留一点点余量可以避免边缘露出一圈细缝。
DEFAULT_MASK_PADDING = 3
MIN_MASK_PADDING = 0
MAX_MASK_PADDING = 60

# --------------------------------------------------------------------------
# 遮罩形状：轮廓贴合（类似 PS 快速选择）还是矩形
# --------------------------------------------------------------------------
# 开启后，以检测框作为"确定的前景"种子，在框外一圈上下文里用 GrabCut
# 迭代出真正贴合该部位轮廓的选区，只对选区内打码。
# 这样遮罩不再是包住整个框的矩形，而是顺着部位边缘走。
DEFAULT_SHAPE_MASK = os.getenv("NSFW_SHAPE_MASK", "1").strip() != "0"

# 上下文边距：为了让 GrabCut 有"确定的背景"可学，合成区域要比检测框大一圈。
# 取检测框短边的比例，并保底一个像素数。
SEGMENT_CONTEXT_RATIO = 0.45
SEGMENT_CONTEXT_MIN = 14

# GrabCut 迭代次数。次数越多越准，也越慢。
GRABCUT_ITERATIONS = 3

# 分割前先把长边压到这个尺寸以内（速度优先，掩膜会再放大回原尺寸）
SEGMENT_MAX_SIDE = 256

# 分割结果占检测框面积的比例低于此值即判定分割失败，退回矩形。
# 对打码工具来说，"宁可多遮"比"漏掉"安全，所以失败一律走矩形兜底。
SEGMENT_MIN_KEEP_RATIO = 0.12

# --------------------------------------------------------------------------
# 边缘羽化
# --------------------------------------------------------------------------
# 轮廓遮罩的边缘会有一圈硬边。做一点羽化能让过渡自然些。
DEFAULT_FEATHER = 2
MIN_FEATHER = 0
MAX_FEATHER = 30

# --------------------------------------------------------------------------
# 打码参数（服务端强校验，原版完全不校验 intensity）
# --------------------------------------------------------------------------
DEFAULT_MODE = "blur"
DEFAULT_INTENSITY = 51
MIN_INTENSITY = 3
MAX_INTENSITY = 199
DEFAULT_COLOR = "#000000"

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# --------------------------------------------------------------------------
# 数据保留与清理
# --------------------------------------------------------------------------
# 原版的清理只在有请求时才被触发：服务一旦闲置，上传的原图就会永久留在磁盘上。
# 这里改为后台定时任务。
RETENTION_SECONDS: int = _env_int("NSFW_RETENTION_SECONDS", 3600, lo=60)
CLEANUP_INTERVAL_SECONDS: int = _env_int("NSFW_CLEANUP_INTERVAL_SECONDS", 600, lo=30)

# --------------------------------------------------------------------------
# 密钥与签名令牌
# --------------------------------------------------------------------------
API_KEY_FILE = DATA_DIR / "api_key"
SIGNING_KEY_FILE = DATA_DIR / "signing_key"

# 下载令牌有效期（秒）。令牌与具体文件名绑定，防止 URL 被转发后长期可用。
TOKEN_TTL: int = _env_int("NSFW_TOKEN_TTL", 3600, lo=60)

# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
# 检测模型。留空用 nudenet 内置的 320n（小模型，召回有限）。
#
# 口交、生殖器靠近面部这类姿势，320n 经常检不出来 —— 这是模型能力上限。
# 换 640m 大模型能明显改善。把 640m.onnx 放进 <项目根>/models/ 即可自动启用
# （运行 tools/fetch_model.py 可自动下载），或用本变量指向任意 onnx 路径。
DETECTOR_MODEL: str = os.getenv("NSFW_DETECTOR_MODEL", "").strip()

def _autodetect_detector_model() -> str:
    """环境变量未指定时，自动寻找项目 models/ 目录下的大模型。"""
    if DETECTOR_MODEL:
        return DETECTOR_MODEL
    models_dir = BASE_DIR / "models"
    if not models_dir.is_dir():
        return ""
    for name in ("640m.onnx", "480m.onnx"):     # 按效果优先级排列
        candidate = models_dir / name
        if candidate.is_file() and candidate.stat().st_size > 10 * 1024 * 1024:
            return str(candidate)
    return ""


DETECTOR_MODEL_RESOLVED: str = _autodetect_detector_model()


# --------------------------------------------------------------------------
# SAM（Segment Anything）：真正的「PS 快速选择」级轮廓分割
# --------------------------------------------------------------------------
# GrabCut 靠颜色分布区分前景/背景，部位与周围皮肤颜色相近时根本找不到
# 边界、只能把整个检测框判成前景（实测退化成矩形）。SAM 是用上亿张图
# 训练的分割模型，以检测框为提示就能分割出部位的真实轮廓。
# 模型约 358MB，存放在 models/sam-vit-base/（tools/fetch_sam.py 一键下载）。
SAM_ENABLED: bool = _env_int("NSFW_SAM", 1, lo=0, hi=1) == 1

_SAM_MODEL_RAW = os.getenv("NSFW_SAM_MODEL", "").strip()


def _autodetect_sam_model() -> str:
    """优先用项目 models/ 下的本地模型；其次交给 HF hub（本地缓存或下载）。"""
    if _SAM_MODEL_RAW:
        return _SAM_MODEL_RAW
    local = BASE_DIR / "models" / "sam-vit-base" / "config.json"
    if local.is_file() and local.stat().st_size > 100:
        return str(local.parent)
    return "facebook/sam-vit-base"


SAM_MODEL_RESOLVED: str = _autodetect_sam_model()

# --------------------------------------------------------------------------
# 计算设备
# --------------------------------------------------------------------------
# auto = 有可用的 CUDA 就用，没有就退回 CPU（默认）
# cuda = 只用 GPU，环境不满足时直接报错并给出安装指引
# cpu  = 只用 CPU
DEVICE: str = os.getenv("NSFW_DEVICE", "auto").strip().lower()
if DEVICE not in {"auto", "cuda", "cpu"}:
    DEVICE = "auto"

# --------------------------------------------------------------------------
# 检测召回
# --------------------------------------------------------------------------
# 推理分辨率。默认保持原库的 320。
#
# 320n 是按 320 输入训练的：提分辨率对"远处的小目标"有利，
# 但**特写大目标反而会检不到**（实测回归：特写的阴茎/阴部在 640 下漏检），
# 所以默认回退 320，靠 Deep Scan 瓦片扫描去照顾小目标。
# 如果你的图多是远距离小目标，可以试 NSFW_DETECTOR_RESOLUTION=640。
DETECTOR_RESOLUTION: int = _env_int("NSFW_DETECTOR_RESOLUTION", 320, lo=320, hi=1280)

# 检测分数下限。原库把 <0.2 的候选在预处理阶段直接丢弃、NMS 里又硬编码 0.25，
# 低置信检出（恰恰是难姿势唯一的线索）根本到不了我们手里。降到这里之后，
# 最终遮不遮仍然由下面的灵敏度门槛决定，所以误杀风险是可控的。
DETECTOR_SCORE_FLOOR: float = float(os.getenv("NSFW_DETECTOR_SCORE_FLOOR", "0.10"))
CLASSIFIER_MODEL: str = os.getenv(
    "NSFW_CLASSIFIER_MODEL", "Falconsai/nsfw_image_detection"
)
# 国内网络访问 huggingface.co 常常失败，可设为 https://hf-mirror.com
HF_ENDPOINT: str = os.getenv("HF_ENDPOINT", "")
DEEP_SCAN: bool = os.getenv("NSFW_DEEP_SCAN", "1").strip() != "0"
# 低风险阈值：图片被判定干净的置信度高于此值时放宽打码（原版硬编码逻辑保留）
HIGH_RISK_THRESHOLD: float = 0.60
