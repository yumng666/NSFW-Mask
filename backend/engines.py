"""本地推理引擎：NudeNet 部位检测 + ViT 整图分类 + OpenCV 打码。

相对原始版本的修改（安全 + 正确性）：

1. `ImageProcessor` 不再用实例属性保存 `blur_radius`。原版在每次请求里
   `_processor.blur_radius = intensity`，而 `_processor` 是全局单例，
   并发请求会互相覆盖对方的强度。现在强度作为参数传入。
2. Deep Scan 不再往工作目录写 `_tile_<pid>.png`。原版固定用进程号命名，
   并发时两个请求会写同一个文件、甚至互相 `os.remove`，导致检测结果错乱。
   现在直接把 numpy 切片交给 NudeNet（它本身就支持内存图像），
   连磁盘 I/O 都省掉了；仅在内存调用失败时才降级到唯一命名的临时文件。
3. 分类器真正支持 GPU。原版虽然提供 requirements-gpu.txt，但
   `pipeline(...)` 没有传 `device`，实际一直在 CPU 上跑。
4. 对模型返回的检测框 / 分数做类型与范围校验，避免畸形数据让
   `map(int, box)` 抛出后污染整个请求。
5. 增加图片像素上限，防止"图片炸弹"（尺寸极小的文件解压出巨大位图）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from backend import config

# 必须在导入 transformers / huggingface_hub 之前设置，否则不生效。
# 国内网络直连 huggingface.co 常常失败，可设 HF_ENDPOINT=https://hf-mirror.com
if config.HF_ENDPOINT:
    os.environ.setdefault("HF_ENDPOINT", config.HF_ENDPOINT)

try:  # torch / transformers 属于重依赖，缺失时降级为"仅检测、不分类"
    from transformers import pipeline
    import torch
except ImportError:  # pragma: no cover
    pipeline = None
    torch = None


# 部位分级来自 config.CENSOR_PROFILES（pixiv / strict 两套预设）。
#
# 这个分级结构是修复"男性生殖器识别不到"的关键：
#
# 原实现的 _should_censor 把生殖器也放进了那套"防误杀"降级逻辑里 ——
# 当整图被 ViT 判定为偏正常时，暴露类标签的打码门槛会被抬到 0.60~0.80，
# 而 nudenet 自身的最低输出分只有 0.25，于是 0.25~0.55 之间的检出被无条件丢弃。
# 男性生殖器恰恰是模型给分偏低的部位，因此几乎必然被漏掉。
#
# 现在的原则：must 级部位（明确的裸露）**不参与任何降级**，
# 只受用户选择的灵敏度门槛约束；降级逻辑只作用于 ambiguous 级。
#
# 供外部引用的集合 = 所有预设里出现过的部位（用于前端提示等）
_PROFILE_LABELS = [set(p["must"]) | set(p["ambiguous"]) for p in config.CENSOR_PROFILES.values()]
SENSITIVE_LABELS = set.union(*_PROFILE_LABELS) if _PROFILE_LABELS else set()

HIGH_RISK_LABELS = {"porn", "hentai", "nsfw", "unsafe"}


def _hf_hub_cache_dir() -> str:
    """定位 HuggingFace 的模型缓存目录，仅用于在报错时给出可操作的路径。"""
    try:
        from huggingface_hub import constants as hf_constants

        return str(hf_constants.HF_HUB_CACHE)
    except Exception:
        base = os.getenv("HF_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface"
        )
        return os.path.join(base, "hub")



class ImageTooLargeError(ValueError):
    """图片尺寸超过允许的像素上限。"""


def _imwrite(path: str, img: np.ndarray) -> bool:
    """写图，兼容非 ASCII 路径。

    Windows 上 cv2.imwrite 用系统 ANSI 编码处理路径，中文目录
    （output/我的相册/ 之类）会静默失败返回 False —— 结果图根本
    落不了盘。改用 imencode + 字节写入，路径交给 Python 处理。
    """
    ext = os.path.splitext(str(path))[1] or ".jpg"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    try:
        with open(str(path), "wb") as fh:
            fh.write(buf.tobytes())
        return True
    except OSError:
        return False


def read_image(path: str) -> np.ndarray | None:
    """稳健读图：cv2 → Pillow → 裸字节 imdecode 三级兜底。"""
    img = cv2.imread(str(path))
    if img is not None:
        return _guard_pixels(img)

    try:
        with Image.open(str(path)) as pil_img:
            rgb = pil_img.convert("RGB")
            img = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
        return _guard_pixels(img)
    except Exception:
        pass

    try:
        with open(str(path), "rb") as fh:
            raw = np.frombuffer(fh.read(), dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if img is not None:
            return _guard_pixels(img)
    except Exception:
        pass

    return None


def _guard_pixels(img: np.ndarray) -> np.ndarray:
    """限制解码后的总像素数，防止解压炸弹吃光内存。"""
    h, w = img.shape[:2]
    if h * w > config.MAX_IMAGE_PIXELS:
        raise ImageTooLargeError(
            f"图片解码后为 {w}x{h}，超过上限 {config.MAX_IMAGE_PIXELS} 像素。"
        )
    return img


def _coerce_detection(raw: dict) -> dict | None:
    """把模型输出整理成 {box:[x,y,w,h], score:float, label:str}，畸形则丢弃。"""
    if not isinstance(raw, dict):
        return None
    box = raw.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    try:
        score = float(raw.get("score", 0.0))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(score):
        return None
    label = raw.get("class") or raw.get("label") or "unknown"
    return {"box": [x, y, w, h], "score": score, "label": str(label)}


class BaseDetector(ABC):
    @abstractmethod
    def detect(self, image_path: str) -> list[dict]:
        raise NotImplementedError


class LocalNudeNetDetector(BaseDetector):
    """NudeNet 部位检测器，可选 3x3 重叠瓦片的 Deep Scan。

    不再使用 nudenet 包自带的 NudeDetector 封装，原因有二：

    1. 它把分数下限写死（预处理丢 <0.2、NMS 再丢 <0.25），低置信检出
       全部被库内部扔掉 —— 而那些恰恰是口交等难姿势唯一的线索；
    2. 它的 ONNX 会话把 providers 参数注释掉了，永远跑在 CPU 上。

    预处理与坐标还原搬运自 nudenet 3.4.2（MIT License）的
    ``_read_image`` / ``_postprocess``，除下列两点外逐行保持一致，
    确保坐标换算与原版完全等价：

    * 分数下限改为 config.DETECTOR_SCORE_FLOOR（默认 0.10）；
    * 通道转换兼容 3 通道 BGR 输入（原库只认 4 通道，导致内存图像
      每次都要落到临时文件）。
    """

    # 与 nudenet 320n.onnx 的输出通道顺序一一对应，顺序不能改
    _LABELS = (
        "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED",
        "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED",
        "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED",
        "BELLY_COVERED", "FEET_COVERED", "ARMPITS_COVERED",
        "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
        "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED",
        "BUTTOCKS_COVERED",
    )

    def __init__(self, model_path: str | None = None) -> None:
        import onnxruntime as ort

        path = model_path or _bundled_model_path()
        if not path:
            raise RuntimeError("找不到 NudeNet 模型文件（320n.onnx）")

        providers = _ort_providers()
        if config.DEVICE == "cuda" and "CUDAExecutionProvider" not in providers:
            raise RuntimeError(
                "NSFW_DEVICE=cuda 但 onnxruntime 没有 CUDA 支持。"
                "请安装 GPU 版计算库：pip install onnxruntime-gpu nvidia-cudnn-cu13"
                "（详见 README「GPU 加速」一节），或改用 NSFW_DEVICE=auto。"
            )

        self.session = ort.InferenceSession(path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.resolution = config.DETECTOR_RESOLUTION
        self.detector = self          # deep_scan 沿用 self.detector.detect 的调用方式
        print(
            f"[Detector] 模型={os.path.basename(path)} "
            f"推理分辨率={self.resolution} 分数下限={config.DETECTOR_SCORE_FLOOR:.2f} "
            f"设备={self.session.get_providers()[0]}"
        )

    def _detect_raw(self, image) -> list[dict]:
        """对单张图（路径或 BGR ndarray）做一次推理。"""
        blob, x_ratio, y_ratio, x_pad, y_pad, ow, oh = _preprocess_blob(
            image, self.resolution
        )
        outputs = self.session.run(None, {self.input_name: blob})
        return _postprocess(
            outputs, x_pad, y_pad, x_ratio, y_ratio, ow, oh,
            self.resolution, self.resolution, config.DETECTOR_SCORE_FLOOR,
            self._LABELS,
        )

    def detect(self, image_path: str, use_deep_scan: bool | None = None) -> list[dict]:
        if use_deep_scan is None:
            use_deep_scan = config.DEEP_SCAN

        img = read_image(image_path)
        if img is None:
            print(f"[Detector] 无法解码图片，跳过检测: {image_path}")
            return []

        h, w = img.shape[:2]
        collected: list[dict] = []
        for raw in self._detect_raw(image_path) or []:
            item = _coerce_detection(raw)
            if item:
                collected.append(item)

        if use_deep_scan and w > 100 and h > 100:
            collected.extend(self._deep_scan(img, w, h))

        return self._apply_nms(collected)

    def _deep_scan(self, img: np.ndarray, w: int, h: int) -> list[dict]:
        """3x3 重叠瓦片扫描，捕捉高分辨率图中的小面积敏感区域。"""
        findings: list[dict] = []
        tile_w, tile_h = w // 2, h // 2
        x_starts = (0, w // 4, w // 2)
        y_starts = (0, h // 4, h // 2)

        for y_start in y_starts:
            for x_start in x_starts:
                x2 = min(w, x_start + tile_w)
                y2 = min(h, y_start + tile_h)
                tile = img[y_start:y2, x_start:x2]
                if tile.size == 0 or tile.shape[0] < 8 or tile.shape[1] < 8:
                    continue

                for raw in self._detect_tile(tile):
                    item = _coerce_detection(raw)
                    if not item:
                        continue
                    # 把瓦片内坐标还原成整图坐标
                    bx, by, bw, bh = item["box"]
                    item["box"] = [bx + x_start, by + y_start, bw, bh]
                    findings.append(item)
        return findings

    def _detect_tile(self, tile: np.ndarray) -> list:
        """优先把内存图像直接交给 NudeNet；失败才落到唯一命名的临时文件。

        原版无条件写 `_tile_<pid>.png` 到工作目录，并发时会互相覆盖。
        """
        try:
            return list(self._detect_raw(tile) or [])
        except Exception:
            pass

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=str(config.TMP_DIR))
            os.close(fd)
            if not _imwrite(tmp_path, tile):
                return []
            return list(self._detect_raw(tmp_path) or [])
        except Exception as exc:
            print(f"[Detector] 瓦片检测失败: {exc}")
            return []
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _apply_nms(
        self, detections: list[dict], iou_threshold: float = 0.4
    ) -> list[dict]:
        """同类框做非极大值抑制，去掉瓦片重叠产生的重复检测。"""
        if not detections:
            return []
        pending = sorted(detections, key=lambda d: d["score"], reverse=True)
        keep: list[dict] = []
        while pending:
            best = pending.pop(0)
            keep.append(best)
            pending = [
                d
                for d in pending
                if d["label"] != best["label"]
                or self._iou(d["box"], best["box"]) < iou_threshold
            ]
        return keep

    @staticmethod
    def _iou(a: list[float], b: list[float]) -> float:
        xa, ya = max(a[0], b[0]), max(a[1], b[1])
        xb, yb = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
        inter = max(0.0, xb - xa) * max(0.0, yb - ya)
        union = a[2] * a[3] + b[2] * b[3] - inter
        return inter / (union + 1e-10)


class LocalNSFWClassifier:
    """ViT 整图分类，给出 normal / nsfw 等标签的概率。"""

    def __init__(self, model_name: str | None = None) -> None:
        self.classifier = None
        self.device = "cpu"
        if pipeline is None:
            print("[Classifier] 未安装 torch/transformers，分类功能不可用。")
            return
        try:
            if torch is not None and torch.cuda.is_available():
                self.device = "cuda"
            # 原版漏传 device，导致 GPU 实际上从未被使用
            device_arg = 0 if self.device == "cuda" else -1
            if config.DEVICE == "cuda" and self.device != "cuda":
                raise RuntimeError(
                    "NSFW_DEVICE=cuda 但当前 torch 不支持 CUDA。"
                    "请安装 CUDA 版 PyTorch："
                    "pip install torch==2.14.0+cu130 --index-url"
                    " https://download.pytorch.org/whl/cu130，"
                    "或改用 NSFW_DEVICE=auto。"
                )
            self.classifier = pipeline(
                "image-classification",
                model=model_name or config.CLASSIFIER_MODEL,
                device=device_arg,
            )
            print(
                f"[Classifier] 已加载 {model_name or config.CLASSIFIER_MODEL}"
                f"（{self.device}）"
            )
        except Exception as exc:
            # 模型加载失败是**可接受**的降级路径，不是致命错误：
            # 打码功能只依赖 NudeNet 检测器，照常工作。
            # 但报错原文（例如 "not a valid JSON file"）对使用者毫无指向性，
            # 这里补上具体原因和排查步骤。
            print("[Classifier] 模型加载失败，将退化为「仅检测器」模式，打码功能不受影响。")
            print(f"[Classifier] 原因: {exc}")
            print("[Classifier] 排查建议：")
            print("  1) 首次运行需要访问 HuggingFace 下载约 340 MB 权重。")
            print("     国内网络直连通常失败，请设置 HF_ENDPOINT=https://hf-mirror.com 后重启。")
            print("  2) 若之前下载被中断过，缓存里会残留损坏文件，导致后续启动")
            print("     报出与真实原因无关的 JSON 解析错误。删除该缓存目录后重试：")
            print(f"     {_hf_hub_cache_dir()}")
            print("  3) 也可以先用 NSFW_CLASSIFIER_MODEL 指向本地已下载好的模型目录。")

    def classify(self, image_path: str) -> list[dict]:
        if not self.classifier:
            return []
        try:
            results = self.classifier(image_path) or []
        except Exception as exc:
            print(f"[Classifier] 推理失败: {exc}")
            return []

        out: list[dict] = []
        for item in results:
            try:
                out.append(
                    {"label": str(item["label"]), "score": float(item["score"])}
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


class ImageProcessor:
    """按检测框做选择性打码。无实例可变状态，可安全并发复用。"""

    def process(
        self,
        image_path: str,
        detections: list[dict],
        output_path: str,
        mode: str = config.DEFAULT_MODE,
        intensity: int = config.DEFAULT_INTENSITY,
        nsfw_scores: list[dict] | None = None,
        color_hex: str = config.DEFAULT_COLOR,
        sensitivity: str = config.DEFAULT_SENSITIVITY,
        mask_padding: int = config.DEFAULT_MASK_PADDING,
        shape_mask: bool = config.DEFAULT_SHAPE_MASK,
        feather: int = config.DEFAULT_FEATHER,
        mask_output_path: str | None = None,
        ruleset: str = config.DEFAULT_RULESET,
        mask_inset: int = config.DEFAULT_MASK_INSET,
    ) -> tuple[str, int, list[dict]]:
        """按检测框打码。

        返回 (输出路径, 实际打码的框数量, 逐个检测框的处置说明)。

        第三个返回值专供前端做"为什么这块没被遮住"的诊断，
        每项形如 {label, score, box, censored, reason}。

        `intensity` / `sensitivity` / `mask_padding` 一律通过参数传入而非实例
        属性 —— 原版把 intensity 存在全局单例上，并发请求会互相覆盖。
        """
        if mode not in config.ALLOWED_MODES:
            raise ValueError(f"不支持的打码模式: {mode}")
        if sensitivity not in config.ALLOWED_SENSITIVITY:
            sensitivity = config.DEFAULT_SENSITIVITY
        intensity = max(
            config.MIN_INTENSITY, min(config.MAX_INTENSITY, int(intensity))
        )
        mask_padding = max(
            config.MIN_MASK_PADDING, min(config.MAX_MASK_PADDING, int(mask_padding))
        )
        feather = max(config.MIN_FEATHER, min(config.MAX_FEATHER, int(feather)))
        shape_mask = bool(shape_mask)
        mask_inset = max(config.MIN_MASK_INSET, min(config.MAX_MASK_INSET, int(mask_inset)))
        if ruleset not in config.ALLOWED_RULESETS:
            ruleset = config.DEFAULT_RULESET

        img = read_image(image_path)
        if img is None:
            raise ValueError(f"无法解码图片: {image_path}")

        h_img, w_img = img.shape[:2]
        scores = nsfw_scores or []

        normal_score = next(
            (s["score"] for s in scores if str(s.get("label", "")).lower() == "normal"),
            0.0,
        )
        is_high_risk = any(
            str(s.get("label", "")).lower() in HIGH_RISK_LABELS
            and float(s.get("score", 0.0)) > config.HIGH_RISK_THRESHOLD
            for s in scores
        )

        bgr_color = _parse_hex_color(color_hex)
        applied = 0
        annotations: list[dict] = []
        # 整图遮罩：把所有检测框的掩膜并起来，落盘后供前端载入为涂抹初始状态
        full_mask = np.zeros((h_img, w_img), np.uint8)

        # ---- 第一遍：逐个检测框做"遮不遮"的策略判定 ----
        censored: list[tuple[dict, int, int, int, int, bool]] = []
        for det in detections:
            label = str(det.get("label", ""))
            try:
                score = float(det.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            raw_box = det.get("box")

            entry = {"label": label, "score": score, "box": raw_box}
            annotations.append(entry)

            should, reason = self._should_censor(
                label, score, normal_score, is_high_risk, sensitivity, ruleset
            )
            entry["in_scope"] = _label_in_scope(label, ruleset)
            if not should:
                entry["censored"] = False
                entry["reason"] = reason
                continue

            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
                entry["censored"] = False
                entry["reason"] = "检测框格式无效"
                continue
            try:
                x, y, w, h = (int(float(v)) for v in raw_box)
            except (TypeError, ValueError):
                entry["censored"] = False
                entry["reason"] = "检测框数值无效"
                continue
            # 退化框直接跳过，与 _coerce_detection 的判定保持一致
            if w <= 0 or h <= 0:
                entry["censored"] = False
                entry["reason"] = "检测框尺寸为 0"
                continue

            entry["censored"] = True
            entry["reason"] = reason
            censored.append((entry, x, y, w, h, is_high_risk))

        # ---- 第二遍：几何关联 ----
        # 交合、口交这类姿势里相关部位是挨着的：把相邻的必遮框合并成同一区域，
        # 并向邻近的面部延伸，否则两个框之间的缝隙就是漏遮的接触区域。
        faces: list[list[int]] = []
        for det in detections:
            if str(det.get("label", "")) not in _FACE_LABELS:
                continue
            b = det.get("box")
            if not isinstance(b, (list, tuple)) or len(b) != 4:
                continue
            try:
                fx, fy, fw, fh = (int(float(v)) for v in b)
            except (TypeError, ValueError):
                continue
            if fw > 0 and fh > 0:
                faces.append([fx, fy, fw, fh])

        groups = _group_related(
            [(x, y, w, h) for (_, x, y, w, h, _) in censored],
            faces,
            labels=[c[0]["label"] for c in censored],
        )

        # SAM 批量分割：只对孤立框（非合并/桥接区域）做轮廓贴合，
        # 一次前向处理所有框，图像编码器只跑一遍。
        sam_masks: dict[int, np.ndarray] = {}
        if shape_mask:
            solo = [
                (gi, groups[gi][0])
                for gi in range(len(groups))
                if not groups[gi][2]
            ]
            if solo:
                boxes_xyxy = [
                    [b[0], b[1], b[0] + b[2], b[1] + b[3]] for _, b in solo
                ]
                results = _sam_segment_full(img, boxes_xyxy)
                for (sgi, _), m in zip(solo, results):
                    if m is not None:
                        sam_masks[sgi] = m

        for gi, (box, member_idx, note) in enumerate(groups):
            orig_box = tuple(box)          # 内收前的原始框，供 SAM 掩膜精修用
            x, y, w, h = box

            # 遮罩内收：检测框普遍偏松，从四周按比例裁掉松出来的那圈皮肤。
            # 只作用于矩形掩膜与 GrabCut 种子 —— SAM 掩膜本身已贴合部位，
            # 再按内收框裁会把部位边缘切掉（实测目标覆盖 98% → 84%）。
            # 合并/桥接区域（note 非空）不做内收 —— 它们覆盖的是接触带，
            # 四周裁 15% 会把交合区域本体的边缘切掉。
            if mask_inset > 0 and not note and w > 16 and h > 16:
                ix = min(int(w * mask_inset / 100.0), (w - 8) // 2)
                iy = min(int(h * mask_inset / 100.0), (h - 8) // 2)
                x, y, w, h = x + ix, y + iy, w - 2 * ix, h - 2 * iy

            # 合并后的区域取成员中最高的风险等级（影响遮挡强度）
            region_risk = any(censored[i][5] for i in member_idx)
            # 合并/延伸出来的区域必须用矩形完整覆盖：
            # 轮廓分割会把两个框之间的缝隙裁掉，而那正是要遮的接触区域。
            use_shape = shape_mask and not note

            # 合成区域：检测框再往外扩一圈，作为 GrabCut 的上下文。
            ctx = _context_rect(x, y, w, h, w_img, h_img, mask_padding)
            if ctx is None:
                for i in member_idx:
                    censored[i][0]["censored"] = False
                    censored[i][0]["reason"] = "遮罩区域为空"
                continue
            cx1, cy1, cx2, cy2 = ctx
            roi = img[cy1:cy2, cx1:cx2]
            if roi.size == 0:
                for i in member_idx:
                    censored[i][0]["censored"] = False
                    censored[i][0]["reason"] = "遮罩区域为空"
                continue

            # SAM 掩膜（整图坐标系）裁到上下文区域，做部位级精修与合理性检查。
            # 精修用内收前的原始框 —— SAM 掩膜已贴合部位，不能用内收框再裁。
            contour_override = None
            if use_shape and gi in sam_masks:
                cand = sam_masks[gi][cy1:cy2, cx1:cx2]
                if cand.shape[:2] == (cy2 - cy1, cx2 - cx1):
                    ox, oy, ow, oh = orig_box
                    refined = _refine_part_mask(
                        cand, (ox - cx1, oy - cy1, ow, oh)
                    )
                    if refined is not None:
                        kept = int(
                            np.count_nonzero(
                                refined[y - cy1 : y - cy1 + h, x - cx1 : x - cx1 + w]
                            )
                        )
                        total = int(np.count_nonzero(refined))
                        if (
                            kept / float(max(1, w * h))
                            >= config.SEGMENT_MIN_KEEP_RATIO
                            and total <= 3.0 * w * h
                        ):
                            contour_override = refined

            mask, used_contour, seg_method = self._build_mask(
                roi,
                (x - cx1, y - cy1, w, h),
                use_shape,
                mask_padding,
                feather,
                contour_mask=contour_override,
            )


            censored_roi = self._censor_roi(
                roi, mode, intensity, bgr_color, region_risk
            )
            img[cy1:cy2, cx1:cx2] = _composite(roi, censored_roi, mask)

            # 并进整图遮罩（取最大值，重叠区域不会互相冲淡）
            patch = full_mask[cy1:cy2, cx1:cx2]
            np.maximum(patch, mask, out=patch)

            applied += 1

            # 报告实际生效的范围。轮廓模式下这是轮廓的外接框，
            # 可能明显小于检测框，前端据此展示"贴合程度"。
            ys, xs = np.nonzero(mask)
            if len(xs):
                applied_box = [
                    cx1 + int(xs.min()),
                    cy1 + int(ys.min()),
                    int(xs.max() - xs.min() + 1),
                    int(ys.max() - ys.min() + 1),
                ]
            else:
                applied_box = [cx1, cy1, cx2 - cx1, cy2 - cy1]

            base_reason = "已打码（轮廓贴合）" if used_contour else "已打码（矩形）"
            for i in member_idx:
                entry = censored[i][0]
                entry["applied_box"] = applied_box
                entry["shape"] = "contour" if used_contour else "rect"
                if used_contour:
                    entry["segmenter"] = seg_method
                entry["mask_ratio"] = round(
                    float(np.count_nonzero(mask)) / float(max(1, w * h)), 3
                )
                entry["censored"] = True
                entry["reason"] = f"{base_reason}；{note}" if note else base_reason

        if not _imwrite(output_path, img):
            raise ValueError(f"写入结果图片失败: {output_path}")

        if mask_output_path:
            if not _imwrite(mask_output_path, full_mask):
                raise ValueError(f"写入遮罩位图失败: {mask_output_path}")

        return output_path, applied, annotations

    def apply_user_mask(
        self,
        image_path: str,
        mask: np.ndarray,
        output_path: str,
        mode: str = config.DEFAULT_MODE,
        intensity: int = config.DEFAULT_INTENSITY,
        color_hex: str = config.DEFAULT_COLOR,
        feather: int = 0,
    ) -> tuple[str, int]:
        """按用户给定的遮罩位图打码，返回 (输出路径, 打码像素数)。

        遮罩是单通道灰度图，非零处表示需要打码。尺寸会被对齐到原图。
        这是"手动选区"的落地实现 —— 用户涂哪里就只打哪里，
        完全绕开自动分割在肤色相近场景下不可靠的问题。
        """
        if mode not in config.ALLOWED_MODES:
            raise ValueError(f"不支持的打码模式: {mode}")
        if mask is None or mask.size == 0:
            raise ValueError("遮罩为空")
        intensity = max(config.MIN_INTENSITY, min(config.MAX_INTENSITY, int(intensity)))
        feather = max(config.MIN_FEATHER, min(config.MAX_FEATHER, int(feather)))

        img = read_image(image_path)
        if img is None:
            raise ValueError(f"无法解码图片: {image_path}")
        h_img, w_img = img.shape[:2]

        if mask.ndim == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        if mask.shape[:2] != (h_img, w_img):
            mask = cv2.resize(mask, (w_img, h_img), interpolation=cv2.INTER_NEAREST)

        # 二值化后只保留明确的选区，避免抗锯齿边缘带来半透明打码
        mask = np.where(mask > 127, 255, 0).astype(np.uint8)
        covered = int(np.count_nonzero(mask))
        if covered == 0:
            if not _imwrite(output_path, img):
                raise ValueError(f"写入结果图片失败: {output_path}")
            return output_path, 0

        if feather > 0:
            fk = 2 * feather + 1
            mask = cv2.GaussianBlur(mask, (fk, fk), 0)

        ys, xs = np.nonzero(mask)
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        # 多取几像素，保证羽化边缘也被覆盖到
        y1, x1 = max(0, y1 - feather), max(0, x1 - feather)
        y2, x2 = min(h_img, y2 + feather), min(w_img, x2 + feather)

        roi = img[y1:y2, x1:x2]
        censored = self._censor_roi(
            roi, mode, intensity, _parse_hex_color(color_hex), False
        )
        sub_mask = mask[y1:y2, x1:x2]
        img[y1:y2, x1:x2] = _composite(roi, censored, sub_mask)

        if not _imwrite(output_path, img):
            raise ValueError(f"写入结果图片失败: {output_path}")
        return output_path, covered

    @staticmethod
    def _build_mask(
        roi: np.ndarray,
        rect: tuple[int, int, int, int],
        shape_mask: bool,
        padding: int,
        feather: int,
        contour_mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, bool, str]:
        """构造 0/255 掩膜，返回 (掩膜, 是否用上了轮廓, 分割器名)。

        这是"只打码该部位"的核心：轮廓模式下先分割出部位的真实边界，
        遮罩就顺着边界走，而不是把整个检测框涂成一个方块。
        contour_mask 不为 None 时直接采用（SAM 的整图掩膜裁剪而来），
        否则回退到 GrabCut。
        """
        used_contour = False
        method = ""
        mask: np.ndarray | None = None

        if shape_mask:
            fg = contour_mask
            if fg is None:
                fg = _segment_foreground(roi, rect)
            if fg is not None:
                x, y, w, h = rect
                # 只在检测框范围内统计有效性 —— 框外本来就是确定的背景
                kept = int(np.count_nonzero(fg[y : y + h, x : x + w]))
                if kept / float(max(1, w * h)) >= config.SEGMENT_MIN_KEEP_RATIO:
                    mask = fg
                    used_contour = True
                    method = "sam" if contour_mask is not None else "grabcut"

        if mask is None:
            # 矩形兜底。对打码工具来说"多遮一点"远比"漏掉"安全，
            # 所以分割失败时一律退回矩形，而不是什么都不遮。
            mask = _rect_mask(roi.shape[:2], rect, padding)
        elif padding > 0:
            # 轮廓模式下，扩张量作用在轮廓上，而不是把矩形撑大
            k = 2 * padding + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            mask = cv2.dilate(mask, kernel)

        if feather > 0:
            fk = 2 * feather + 1
            mask = cv2.GaussianBlur(mask, (fk, fk), 0)

        return mask, used_contour, method

    @staticmethod
    def _should_censor(
        label: str,
        score: float,
        normal_score: float,
        is_high_risk: bool,
        sensitivity: str = config.DEFAULT_SENSITIVITY,
        ruleset: str = config.DEFAULT_RULESET,
    ) -> tuple[bool, str]:
        """判断单个检测框是否需要打码，返回 (是否打码, 理由)。

        按所选预设（config.CENSOR_PROFILES）把部位分成两级：

        **must 级**（如 pixiv 预设中的男女生殖器、肛门）
        不参与任何"防误杀"降级，只跟用户选的灵敏度门槛比。
        男性生殖器在模型里给分普遍偏低，一旦套用降级逻辑就会被整片丢光。

        **ambiguous 级**（被遮挡的版本、腹部等）
        这类区域在正常照片里也常见，因此保留分级逻辑：整图越"干净"就越宽容。

        不在预设里的部位一律不打码 —— 比如_pixiv 预设下的臀部与胸部。
        """
        if sensitivity not in config.ALLOWED_SENSITIVITY:
            sensitivity = config.DEFAULT_SENSITIVITY
        threshold = config.SENSITIVITY_THRESHOLDS[sensitivity]
        profile = config.CENSOR_PROFILES.get(
            ruleset if ruleset in config.ALLOWED_RULESETS else config.DEFAULT_RULESET
        )
        must = profile["must"]
        ambiguous = profile["ambiguous"]

        if label in must:
            # 每标签专属门槛（女性阴部 / 男性生殖器等）：取它与灵敏度
            # 门槛中更严的一个 —— 值低于灵敏度时不生效，灵敏度说了算。
            eff_threshold = threshold
            extra = config.LABEL_THRESHOLDS.get(label)
            if extra is not None and extra > threshold:
                eff_threshold = extra
            if score >= eff_threshold:
                return True, "已打码"
            return False, f"分数 {score:.2f} 低于{_level_name(sensitivity)}门槛 {eff_threshold:.2f}"

        if label in ambiguous:
            if is_high_risk:
                if score < config.AMBIGUOUS_THRESHOLD_HIGH_RISK:
                    return False, f"高风险图，但分数 {score:.2f} 低于 {config.AMBIGUOUS_THRESHOLD_HIGH_RISK:.2f}"
                return True, "已打码"
            if normal_score > 0.90:
                return False, f"整图判定为正常（{normal_score:.2f}），该部位属有歧义区域，按防误杀策略跳过"
            if normal_score > 0.70:
                if score < config.AMBIGUOUS_LOOSE_NORMAL:
                    return False, f"整图倾向正常（{normal_score:.2f}），分数 {score:.2f} 未达 {config.AMBIGUOUS_LOOSE_NORMAL:.2f}"
                return True, "已打码"
            if score < config.AMBIGUOUS_THRESHOLD:
                return False, f"分数 {score:.2f} 低于 {config.AMBIGUOUS_THRESHOLD:.2f}"
            return True, "已打码"

        return False, f"当前规则（{profile['label']}）不需要遮挡该部位"

    @staticmethod
    def _censor_roi(
        roi: np.ndarray,
        mode: str,
        intensity: int,
        bgr_color: tuple[int, int, int],
        is_high_risk: bool,
    ) -> np.ndarray:
        if mode == "pixel":
            h_roi, w_roi = roi.shape[:2]
            divisor = max(4, intensity // 4)
            if is_high_risk:
                divisor = int(divisor * 1.3)
            nw = max(1, w_roi // divisor)
            nh = max(1, h_roi // divisor)
            small = cv2.resize(roi, (nw, nh), interpolation=cv2.INTER_LINEAR)
            return cv2.resize(small, (w_roi, h_roi), interpolation=cv2.INTER_NEAREST)

        if mode == "solid":
            return np.full_like(roi, bgr_color)

        radius = intensity if intensity % 2 != 0 else intensity + 1
        return cv2.GaussianBlur(roi, (radius, radius), 0)


def _context_rect(
    x: int, y: int, w: int, h: int, img_w: int, img_h: int, extra: int = 0
) -> tuple[int, int, int, int] | None:
    """把检测框向外扩一圈上下文，返回裁剪到图像内的 (x1, y1, x2, y2)。

    分割算法需要"确定的背景"才能定位前景边界。只在检测框内部做，
    算法没有任何背景样本可学，结果会退化成整个框。

    `extra` 传用户设置的扩张量：合成区域必须比"检测框 + 扩张量"更大，
    否则矩形模式下大扩张量会被上下文边界裁掉，用户要 40px 只得到十几像素。
    """
    margin = max(
        config.SEGMENT_CONTEXT_MIN,
        int(min(w, h) * config.SEGMENT_CONTEXT_RATIO),
        int(extra) + 4,
    )
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(img_w, x + w + margin)
    y2 = min(img_h, y + h + margin)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _rect_mask(
    shape_hw: tuple[int, int], rect: tuple[int, int, int, int], padding: int
) -> np.ndarray:
    """矩形掩膜。rect 是局部坐标，padding 向外扩张。"""
    mask = np.zeros(shape_hw, np.uint8)
    x, y, w, h = rect
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(shape_hw[1], x + w + padding)
    y2 = min(shape_hw[0], y + h + padding)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = 255
    return mask


def _segment_foreground(
    roi: np.ndarray, rect: tuple[int, int, int, int]
) -> np.ndarray | None:
    """以 rect 为"确定的前景"种子做 GrabCut 分割，返回与 roi 同尺寸的 0/255 掩膜。

    等价于 PS 快速选择的思路：给一个大概范围，算法自己找到边界。
    分割前会先降采样以控制耗时，掩膜再放大回原尺寸。
    失败（尺寸太小、OpenCV 报错）返回 None，由调用方退回矩形。
    """
    h, w = roi.shape[:2]
    x, y, bw, bh = rect
    if bw < 10 or bh < 10 or h < 24 or w < 24:
        return None

    scale = 1.0
    if max(h, w) > config.SEGMENT_MAX_SIDE:
        scale = config.SEGMENT_MAX_SIDE / float(max(h, w))
    if scale != 1.0:
        work = cv2.resize(
            roi,
            (max(24, int(w * scale)), max(24, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        work = roi

    wh, ww = work.shape[:2]
    rx = max(1, min(ww - 3, int(round(x * scale))))
    ry = max(1, min(wh - 3, int(round(y * scale))))
    rw = max(2, min(ww - rx - 1, int(round(bw * scale))))
    rh = max(2, min(wh - ry - 1, int(round(bh * scale))))
    if rw < 4 or rh < 4:
        return None

    gc_mask = np.zeros((wh, ww), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(
            work,
            gc_mask,
            (rx, ry, rw, rh),
            bgd,
            fgd,
            config.GRABCUT_ITERATIONS,
            cv2.GC_INIT_WITH_RECT,
        )
    except cv2.error:
        return None

    fg = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    if scale != 1.0:
        fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST)
    return fg


def _refine_part_mask(
    mask: np.ndarray, rect: tuple[int, int, int, int]
) -> np.ndarray | None:
    """SAM 掩膜的部位级精修，解决「打到衣服/周围皮肤上」：

    1. 只保留检测框向外扩 MASK_OUT_MARGIN 范围内的部分 —— 框是模型
       认定的部位范围，掩膜延伸到框外更远处通常是衣服、腿。
    2. 只保留与框中心相关的最大连通域 —— 去掉散落在别处的碎片。
    返回 None 表示掩膜不可用，调用方退回 GrabCut。
    """
    x, y, w, h = rect
    mh, mw = mask.shape[:2]
    margin = config.MASK_OUT_MARGIN
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(mw, x + w + margin)
    y2 = min(mh, y + h + margin)
    if x2 <= x1 or y2 <= y1:
        return None

    cropped = np.zeros_like(mask)
    cropped[y1:y2, x1:x2] = mask[y1:y2, x1:x2]

    num, labels, _, _ = cv2.connectedComponentsWithStats(
        (cropped > 0).astype(np.uint8), 8
    )
    if num <= 1:
        return None

    # 优先取包含框中心的连通域（部位在框内大致居中）；
    # 中心恰好不在任何掩膜里时，取与框重叠面积最大的连通域。
    ccx = min(max(x + w // 2, x1), x2 - 1)
    ccy = min(max(y + h // 2, y1), y2 - 1)
    center_label = int(labels[ccy, ccx])
    if center_label != 0:
        chosen = labels == center_label
    else:
        best_label, best_overlap = 0, 0
        for i in range(1, num):
            overlap = int(
                np.count_nonzero(labels[y : y + h, x : x + w] == i)
            )
            if overlap > best_overlap:
                best_label, best_overlap = i, overlap
        if best_label == 0:
            return None
        chosen = labels == best_label

    return chosen.astype(np.uint8) * 255


def _cand_discriminance(
    image_bgr: np.ndarray, mask: np.ndarray, box: tuple[int, int, int, int]
) -> float:
    """候选掩膜的「区分度」：掩膜内平均颜色与周围背景环平均颜色的距离。

    分对的掩膜（部位本体）颜色和周围皮肤不同；分错的候选（SAM 把
    连片肤色区域当成物体）与背景几乎同色，区分度接近 0。
    这是从图像内容上区分"分对/分错"的信号 —— 单看掩膜几何分不出来。
    """
    x, y, w, h = box
    mh, mw = mask.shape[:2]
    fg = image_bgr[mask > 0]
    if fg.size == 0:
        return 0.0

    mx = max(8, int(w * 0.3))
    my = max(8, int(h * 0.3))
    x1, y1 = max(0, x - mx), max(0, y - my)
    x2, y2 = min(mw, x + w + mx), min(mh, y + h + my)
    ring = np.zeros((mh, mw), dtype=bool)
    ring[y1:y2, x1:x2] = True
    ring[y : y + h, x : x + w] = False
    ring &= mask == 0
    bg = image_bgr[ring]
    if bg.size == 0:
        return 255.0
    return float(
        np.abs(fg.mean(axis=0).astype(np.float32) - bg.mean(axis=0).astype(np.float32)).sum() / 3.0
    )


def _composite(
    original: np.ndarray, censored: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """按掩膜把打码结果叠回原图。掩膜被羽化过时，边缘是渐变过渡。"""
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    blended = censored.astype(np.float32) * alpha + original.astype(
        np.float32
    ) * (1.0 - alpha)
    return np.clip(blended, 0, 255).astype(np.uint8)


def _label_in_scope(label: str, ruleset: str) -> bool:
    """该部位是否在所选规则的遮挡范围内（供前端区分"规则外"与"被跳过"）。"""
    profile = config.CENSOR_PROFILES.get(
        ruleset if ruleset in config.ALLOWED_RULESETS else config.DEFAULT_RULESET
    )
    return label in profile["must"] or label in profile["ambiguous"]


# 面部标签：不参与打码，但用于"生殖器靠近面部"的接触区域推断
# --------------------------------------------------------------------------
# NudeNet 的预处理与后处理。
# 搬运自 nudenet 3.4.2（MIT License）的 _read_image / _postprocess，
# 除文中所列两处改动外逐行一致，保证坐标换算与原版完全等价。
# --------------------------------------------------------------------------
def _bundled_model_path() -> str:
    """nudenet 包自带的 320n 模型路径。"""
    try:
        import nudenet
        p = Path(nudenet.__file__).resolve().parent / "320n.onnx"
        if p.is_file():
            return str(p)
    except Exception:
        pass
    return ""


def _add_cuda_dll_dirs() -> None:
    """把 nvidia pip 轮子里的 CUDA DLL 目录加进搜索路径。

    Windows 上 onnxruntime-gpu 依赖的 cublas/cudnn 不在系统 PATH 里，
    pip 安装的 nvidia-* 包把它们放在 site-packages/nvidia/*/bin 下。
    不加这一步，CUDAExecutionProvider 会因缺 DLL 静默退回 CPU。
    """
    if getattr(sys, "_nsfw_cuda_dll_done", False):
        return
    venv = Path(sys.prefix)
    nvidia = venv / "Lib" / "site-packages" / "nvidia"
    dirs: list[str] = []
    if nvidia.is_dir():
        for p in nvidia.rglob("*"):
            if p.is_dir() and p.name in {"bin", "lib"}:
                dirs.append(str(p))
    # torch 自带的 CUDA DLL（cu130 轮子把 cublas 等放在 torch/lib）
    torch_lib = venv / "Lib" / "site-packages" / "torch" / "lib"
    if torch_lib.is_dir():
        dirs.append(str(torch_lib))
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
    if dirs:
        os.environ["PATH"] = ";".join(dirs + [os.environ.get("PATH", "")])
    sys._nsfw_cuda_dll_done = True


def _ort_providers() -> list[str]:
    """按 config.DEVICE 决定 ONNX 执行器，返回实际可用的列表。"""
    import onnxruntime as ort

    _add_cuda_dll_dirs()
    if config.DEVICE == "cpu":
        return ["CPUExecutionProvider"]
    available = ort.get_available_providers()
    want = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    providers = [p for p in want if p in available]
    return providers or ["CPUExecutionProvider"]


def _preprocess_blob(image, target_size: int):
    """nudenet _read_image 的等价实现：加边成方形、归一化、转 NCHW。"""
    if isinstance(image, str):
        mat = cv2.imread(image)
        if mat is None:
            raise ValueError(f"无法读取图像: {image}")
    elif isinstance(image, np.ndarray):
        mat = image
    else:
        raise ValueError("仅支持路径或 ndarray")

    if mat.ndim == 2:
        mat = cv2.cvtColor(mat, cv2.COLOR_GRAY2BGR)
    elif mat.ndim == 3 and mat.shape[2] == 4:
        mat = cv2.cvtColor(mat, cv2.COLOR_RGBA2BGR)
    # 3 通道 BGR 直接使用（原库在这里只认 4 通道，是它必须走临时文件的原因）

    oh, ow = mat.shape[:2]
    max_size = max(oh, ow)
    x_pad = max_size - ow
    y_pad = max_size - oh
    x_ratio = max_size / ow
    y_ratio = max_size / oh
    mat_pad = cv2.copyMakeBorder(mat, 0, y_pad, 0, x_pad, cv2.BORDER_CONSTANT)
    blob = cv2.dnn.blobFromImage(
        mat_pad,
        1 / 255.0,
        (target_size, target_size),
        (0, 0, 0),
        swapRB=True,
        crop=False,
    )
    return blob, x_ratio, y_ratio, x_pad, y_pad, ow, oh


def _postprocess(
    output, x_pad, y_pad, x_ratio, y_ratio,
    ow, oh, mw, mh, floor: float, labels,
):
    """nudenet _postprocess 的等价实现。

    与原库的唯一差异：候选保留与 NMS 的分数下限由参数 floor 控制
    （原库硬编码 0.2 / 0.25）。
    """
    outputs = np.transpose(np.squeeze(output[0]))
    boxes: list = []
    scores: list = []
    class_ids: list = []

    for i in range(outputs.shape[0]):
        classes_scores = outputs[i][4:]
        max_score = float(np.amax(classes_scores))
        if max_score < floor:
            continue
        class_id = int(np.argmax(classes_scores))
        x, y, w, h = outputs[i][0:4]
        x -= w / 2
        y -= h / 2
        x = x * (ow + x_pad) / mw
        y = y * (oh + y_pad) / mh
        w = w * (ow + x_pad) / mw
        h = h * (oh + y_pad) / mh
        x = max(0.0, min(x, float(ow)))
        y = max(0.0, min(y, float(oh)))
        w = min(w, ow - x)
        h = min(h, oh - y)
        class_ids.append(class_id)
        scores.append(max_score)
        boxes.append([x, y, w, h])

    indices = cv2.dnn.NMSBoxes(boxes, scores, floor, 0.45)
    detections: list[dict] = []
    for i in indices:
        x, y, w, h = boxes[i]
        detections.append(
            {
                "class": labels[class_ids[i]],
                "score": float(scores[i]),
                "box": [int(x), int(y), int(w), int(h)],
            }
        )
    return detections


# --------------------------------------------------------------------------
# SAM（Segment Anything）：以检测框为提示分割出部位的真实轮廓。
# 这是「贴合部位轮廓」真正的实现 —— GrabCut 只认颜色分布，部位与周围
# 皮肤颜色相近时退化成整个检测框（表现为"还是矩形"），SAM 没有这个局限。
# --------------------------------------------------------------------------
_sam_state: tuple | None = None
_sam_warned = False


def get_sam():
    """懒加载 SAM，返回 (model, processor, device) 或 None。

    加载失败不抛异常：轮廓分割自动退回 GrabCut，服务照常运行。
    """
    global _sam_state, _sam_warned
    if _sam_state is not None:
        return _sam_state[0] and _sam_state
    if not config.SAM_ENABLED:
        _sam_state = (False,)
        return None
    try:
        from transformers import SamModel, SamProcessor

        path = config.SAM_MODEL_RESOLVED
        t0 = time.time()
        proc = SamProcessor.from_pretrained(path)
        model = SamModel.from_pretrained(path)
        device = "cpu"
        if torch is not None:
            device = "cuda" if (config.DEVICE != "cpu" and torch.cuda.is_available()) else "cpu"
            model = model.to(device).eval()
        _sam_state = (True, model, proc, device)
        print(f"[SAM] 轮廓分割就绪 ({path}, {time.time() - t0:.1f}s, device={device})")
    except Exception as exc:  # noqa: BLE001
        _sam_state = (False,)
        if not _sam_warned:
            _sam_warned = True
            print(f"[SAM] 不可用（{exc!r}），轮廓分割退回 GrabCut")
    return _sam_state[0] and _sam_state


def _sam_segment_full(image_bgr: np.ndarray, boxes_xyxy: list[list[int]]) -> list:
    """对整图按框提示批量分割，返回与 boxes 对齐的 0/255 掩膜（失败为 None）。

    一次前向处理所有框：图像编码器只跑一遍，多个框共用其输出。
    """
    sam = get_sam()
    if not sam:
        return [None] * len(boxes_xyxy)
    _, model, proc, device = sam
    h, w = image_bgr.shape[:2]
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    try:
        inputs = proc(rgb, input_boxes=[boxes_xyxy], return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if torch is not None:
            with torch.no_grad():
                out = model(**inputs)
        else:  # pragma: no cover
            out = model(**inputs)
        masks = proc.image_processor.post_process_masks(
            out.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )
    except Exception as exc:  # noqa: BLE001
        if not _sam_warned:
            print(f"[SAM] 推理失败（{exc!r}），本次退回 GrabCut")
        return [None] * len(boxes_xyxy)

    scores = out.iou_scores[0].float().cpu().numpy()   # (n, 3)
    arr = masks[0]                                     # (n, 3, H, W) logits
    results: list = []
    for i in range(len(boxes_xyxy)):
        try:
            # 从 3 个候选里挑"分对了目标"的那个。两步筛选：
            # 1. 丢弃面积占比异常的候选（≈空 / ≈整个框）；
            # 2. 按「区分度」排序 —— 掩膜内颜色与周围背景的差异。
            #    不能只看 IoU：实测 SAM 对"框内连片肤色区域"会给出
            #    IoU 0.99 但完全没分对目标的候选（它和背景同色）。
            #    区分度都低于阈值时视为分割失败，返回 None 退回兜底。
            bx, by, bx2, by2 = boxes_xyxy[i]
            bw_real = max(1, bx2 - bx)
            bh_real = max(1, by2 - by)
            box_area = max(1.0, float(bw_real * bh_real))
            cands = []
            for cand_i in range(arr.shape[1]):
                cm = arr[i, cand_i].numpy()
                area_ratio = float((cm > 0).sum()) / box_area
                if area_ratio < 0.02 or area_ratio > 0.92:
                    continue
                cand_mask = (cm > 0.0).astype(np.uint8) * 255
                if cand_mask.shape[:2] != (h, w):
                    cand_mask = cv2.resize(
                        cand_mask, (w, h), interpolation=cv2.INTER_NEAREST
                    )
                disc = _cand_discriminance(
                    image_bgr, cand_mask, (bx, by, bw_real, bh_real)
                )
                cands.append((disc, float(scores[i][cand_i]), cand_i, cand_mask))
            if not cands:
                results.append(None)
                continue
            # 拒绝条件：区分度全都低于绝对阈值，且候选之间没有明显分化
            #（后者很重要 —— 部位占框很大时掩膜必然含大量背景，
            # 区分度绝对值会偏低，此时只看绝对值会误杀正确候选）。
            discs = [c[0] for c in cands]
            if (
                max(discs) < config.SAM_MIN_DISCRIMINANCE
                and max(discs) - min(discs) < 10.0
            ):
                results.append(None)
                continue
            # 区分度明显更高者优先；接近时（差 < 5）取 IoU 高者
            cands.sort(key=lambda c: (-c[0], -c[1]))
            disc0, _, best_idx, best_mask = cands[0]
            if len(cands) > 1 and cands[1][0] > disc0 - 5.0:
                better = max(cands[:2], key=lambda c: c[1])
                _, _, best_idx, best_mask = better
            results.append(best_mask)
        except Exception:  # noqa: BLE001
            results.append(None)
    return results


_FACE_LABELS = {"FACE_FEMALE", "FACE_MALE"}


def _box_gap(a: list[int], b: list[int]) -> tuple[int, int]:
    """两个框之间的水平/垂直净间距（重叠时为 0）。"""
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]), 0)
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]), 0)
    return dx, dy


def _is_cross_type(
    members_a: list[int], members_b: list[int], labels: list[str] | None
) -> bool:
    """两组成员是否为异类（合并只发生在异类之间）。labels 缺省视为异类。"""
    if not labels:
        return True
    la = {labels[i] for i in members_a if i < len(labels)}
    lb = {labels[i] for i in members_b if i < len(labels)}
    return not (la and lb and la == lb)


def _boxes_related(a: list[int], b: list[int], ratio: float = 0.55) -> bool:
    """两框是否重叠，或间距不超过较小框短边的 ratio 倍。

    用于把交合/接触中的两个部位框并成同一遮罩区域 ——
    否则两个框之间的缝隙就是漏遮的接触位置。
    """
    dx, dy = _box_gap(a, b)
    scale = max(min(a[2], a[3], b[2], b[3]), 8)
    return dx <= scale * ratio and dy <= scale * ratio


def _union_box(a: list[int], b: list[int]) -> list[int]:
    x1, y1 = min(a[0], b[0]), min(a[1], b[1])
    x2 = max(a[0] + a[2], b[0] + b[2])
    y2 = max(a[1] + a[3], b[1] + b[3])
    return [x1, y1, x2 - x1, y2 - y1]


def _bridge_to_face(
    gen: list[int], face: list[int], max_ratio: float = 1.6
) -> list[int] | None:
    """把生殖器框向邻近的面部框延伸到其边缘，遮住两者之间的接触区域。

    口交、生殖器贴近面部的姿势里，模型经常检不出接触部位本身；
    当生殖器确实被检出且与面部足够近时，把中间那段一起遮住。
    只延伸到面部边缘为止，不进入面部内部 —— 面部本身不在打码范围。
    间距超过自身尺寸的 max_ratio 倍视为不接触，返回 None。
    """
    gx1, gy1, gw, gh = gen
    gx2, gy2 = gx1 + gw, gy1 + gh
    fx1, fy1, fw, fh = face
    fx2, fy2 = fx1 + fw, fy1 + fh

    dx, dy = _box_gap(gen, face)
    scale = max(gw, gh, 8)
    if dx > scale * max_ratio or dy > scale * max_ratio:
        return None
    if dx == 0 and dy == 0:
        return None                      # 已经重叠，无需延伸

    left = min(gx1, fx1)
    right = max(gx2, fx2)
    top = min(gy1, fy1)
    bottom = max(gy2, fy2)

    # 垂直方向更近：沿垂直方向桥接（口交通常是脸在上、生殖器在下）
    if dy >= dx:
        if fy2 <= gy1:                   # 面部在上方
            return [left, fy2, right - left, gy2 - fy2]
        if gy2 <= fy1:                   # 面部在下方
            return [left, gy1, right - left, fy1 - gy1]
        return None
    # 水平方向更近
    if fx2 <= gx1:                       # 面部在左侧
        return [fx2, top, gx1 - fx2, bottom - top]
    if gx2 <= fx1:                       # 面部在右侧
        return [gx1, top, fx1 - gx1, bottom - top]
    return None


def _group_related(
    boxes: list[list[int]],
    faces: list[list[int]],
    labels: list[str] | None = None,
) -> list[tuple[list[int], list[int], str]]:
    """合并相邻的必遮框，并把合并后的框向邻近面部延伸。

    返回 [(box, 成员下标列表, 说明), ...]。说明非空表示该区域是
    合并/延伸出来的，调用方必须用矩形完整覆盖（轮廓分割会把缝隙裁掉）。

    合并只在**异类**标签之间进行：男 + 女生殖器 = 交合，需要覆盖接触带；
    而**同类**相邻框（画面里有多个阴茎）是多个独立物体，各自走轮廓
    贴合才精准 —— 合并会把它们连成一大块矩形（实测回归）。
    """
    regions: list[list] = [[list(b), [i], ""] for i, b in enumerate(boxes)]

    # 1) 贪心合并：异类且相邻才并成一块，直到不再变化
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                if _boxes_related(regions[i][0], regions[j][0]) and _is_cross_type(
                    regions[i][1], regions[j][1], labels
                ):
                    regions[i][0] = _union_box(regions[i][0], regions[j][0])
                    regions[i][1].extend(regions[j][1])
                    if not regions[i][2]:
                        regions[i][2] = "相邻部位已合并为同一遮罩区域"
                    regions.pop(j)
                    merged = True
                    break
            if merged:
                break

    # 2) 向最近的面部延伸（只取延伸距离最短的一张脸）
    out: list[tuple[list[int], list[int], str]] = []
    for box, members, note in regions:
        best: list[int] | None = None
        best_dist: int | None = None
        for f in faces:
            ext = _bridge_to_face(box, f)
            if ext is None:
                continue
            dx, dy = _box_gap(box, f)
            dist = dx + dy
            if best_dist is None or dist < best_dist:
                best, best_dist = ext, dist
        if best is not None:
            box = best
            note = (note + "；" if note else "") + "已向邻近面部延伸以覆盖接触区域"
        out.append((box, members, note))
    return out


_SENSITIVITY_NAMES = {
    "max": "最高召回",
    "strict": "严格",
    "balanced": "标准",
    "aggressive": "高召回",
}


def _level_name(sensitivity: str) -> str:
    return _SENSITIVITY_NAMES.get(sensitivity, sensitivity)


def _parse_hex_color(color_hex: str) -> tuple[int, int, int]:
    """把 #RRGGBB 解析成 BGR 元组。非法输入回退为黑色，不抛异常。"""
    text = (color_hex or "").strip()
    if not config.HEX_COLOR_RE.match(text):
        text = config.DEFAULT_COLOR
    text = text.lstrip("#")
    r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    return b, g, r
