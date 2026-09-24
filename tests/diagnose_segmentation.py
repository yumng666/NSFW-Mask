"""实验：三种分割初始化方式在"肤色相近"场景下的表现对比。

对比对象：
  A. 矩形（基线，不做分割）
  B. GrabCut + GC_INIT_WITH_RECT   （当前实现）
  C. GrabCut + GC_INIT_WITH_MASK   （候选改进：中心给确定前景，外圈给确定背景）
"""

from __future__ import annotations

import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def build_scene(contrast: float, seed: int = 7):
    """contrast 越大，目标与周围皮肤的差异越明显。"""
    H, W = 420, 520
    ys, xs = np.mgrid[0:H, 0:W]
    base = np.stack(
        [
            132 + 26 * np.sin(xs / 95) + 14 * np.cos(ys / 70),
            158 + 24 * np.cos(ys / 85) + 12 * np.sin(xs / 60),
            196 + 18 * np.sin((xs - ys) / 110),
        ],
        axis=-1,
    )
    rng = np.random.default_rng(seed)
    skin = np.clip(base + rng.integers(-6, 6, (H, W, 3)), 0, 255).astype(np.uint8)

    tgt = np.zeros((H, W), np.uint8)
    poly = np.array(
        [[238, 178], [262, 158], [292, 172], [300, 206], [284, 240], [252, 252], [226, 232], [222, 202]],
        np.int32,
    )
    cv2.fillPoly(tgt, [poly], 255)
    soft = (cv2.GaussianBlur(tgt, (0, 0), 3).astype(np.float32) / 255.0)[..., None]
    hair = rng.integers(0, int(90 * contrast) or 1, (H, W, 3)).astype(np.float32)
    part = skin.astype(np.float32) * (1 - 0.28 * contrast) + hair * (0.30 * contrast)
    skin = np.clip(skin.astype(np.float32) * (1 - soft) + part * soft, 0, 255).astype(np.uint8)
    return skin, tgt, poly


def rect_mask(shape_hw, rect, pad=0):
    m = np.zeros(shape_hw, np.uint8)
    x, y, w, h = rect
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(shape_hw[1], x + w + pad), min(shape_hw[0], y + h + pad)
    if x2 > x1 and y2 > y1:
        m[y1:y2, x1:x2] = 255
    return m


def grabcut_rect_init(roi, rect, iters, max_side):
    h, w = roi.shape[:2]
    sc = min(1.0, max_side / float(max(h, w)))
    work = cv2.resize(roi, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA) if sc < 1.0 else roi
    wh, ww = work.shape[:2]
    x, y, bw, bh = rect
    r = (
        max(1, min(ww - 3, int(x * sc))), max(1, min(wh - 3, int(y * sc))),
        max(2, min(ww - 2, int(bw * sc))), max(2, min(wh - 2, int(bh * sc))),
    )
    gm = np.zeros((wh, ww), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(work, gm, r, bgd, fgd, iters, cv2.GC_INIT_WITH_RECT)
    fg = np.where((gm == cv2.GC_FGD) | (gm == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST) if sc < 1.0 else fg


def grabcut_mask_init(roi, rect, iters, max_side, core_inset=0.30):
    """中心小块标记为确定前景，框外全部标记为确定背景，中间交给算法。"""
    h, w = roi.shape[:2]
    sc = min(1.0, max_side / float(max(h, w)))
    work = cv2.resize(roi, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA) if sc < 1.0 else roi
    wh, ww = work.shape[:2]
    x, y, bw, bh = rect
    x, y, bw, bh = int(x * sc), int(y * sc), int(bw * sc), int(bh * sc)

    gm = np.full((wh, ww), cv2.GC_BGD, np.uint8)          # 框外：确定背景
    gm[y : y + bh, x : x + bw] = cv2.GC_PR_FGD            # 框内：可能前景
    ix, iy = int(bw * core_inset), int(bh * core_inset)   # 中心：确定前景
    if bw - 2 * ix > 2 and bh - 2 * iy > 2:
        gm[y + iy : y + bh - iy, x + ix : x + bw - ix] = cv2.GC_FGD

    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(work, gm, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    fg = np.where((gm == cv2.GC_FGD) | (gm == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST) if sc < 1.0 else fg


def evaluate(scene, tgt, box, mask, label, results):
    reg = mask[box[1] : box[1] + box[3], box[0] : box[0] + box[2]]
    filled = reg > 0
    masked = int(np.count_nonzero(filled))
    tcrop = tgt[box[1] : box[1] + box[3], box[0] : box[0] + box[2]]
    inside = int(np.count_nonzero((tcrop > 0) & filled))
    area_tgt = int(np.count_nonzero(tcrop))
    area_box = box[2] * box[3]
    results.append(
        {
            "label": label,
            "ratio": masked / area_box * 100,
            "cover": inside / max(1, area_tgt) * 100,
            "extra": masked - inside,
        }
    )


print("=" * 74)
print("分割初始化方式对比（目标仅占检测框 22%，肤色相近）")
print("=" * 74)

for contrast, cname in [(1.0, "肤色差异明显"), (0.35, "肤色差异很弱")]:
    scene, tgt, poly = build_scene(contrast)
    x, y, bw, bh = cv2.boundingRect(poly)
    pad = 34
    box = [max(0, x - pad), max(0, y - pad), bw + 2 * pad, bh + 2 * pad]
    ctx_margin = max(14, int(min(box[2], box[3]) * 0.45), 3 + 4)
    cx1, cy1 = max(0, box[0] - ctx_margin), max(0, box[1] - ctx_margin)
    cx2, cy2 = min(520, box[0] + box[2] + ctx_margin), min(420, box[1] + box[3] + ctx_margin)
    roi = scene[cy1:cy2, cx1:cx2]
    local = (box[0] - cx1, box[1] - cy1, box[2], box[3])

    results = []
    full_rect = rect_mask((420, 520), (box[0], box[1], box[2], box[3]), 3)
    evaluate(scene, tgt, box, full_rect, "A 矩形(基线)", results)

    m_rect = np.zeros((420, 520), np.uint8)
    m_rect[cy1:cy2, cx1:cx2] = grabcut_rect_init(roi, local, 3, 256)
    evaluate(scene, tgt, box, m_rect, "B 矩形初始化(当前)", results)

    for iters in (5,):
        m_mask = np.zeros((420, 520), np.uint8)
        m_mask[cy1:cy2, cx1:cx2] = grabcut_mask_init(roi, local, iters, 256)
        evaluate(scene, tgt, box, m_mask, f"C 掩膜初始化({iters}次)", results)

    print()
    print(f"--- {cname} (contrast={contrast}) ---")
    print(f"  {'方案':22s} {'遮罩/框':>8s} {'遮住目标':>9s} {'多遮皮肤':>9s}")
    print("  " + "-" * 54)
    for r in results:
        print(
            f"  {r['label']:22s} {r['ratio']:7.1f}% {r['cover']:8.1f}% {r['extra']:9d}"
        )
