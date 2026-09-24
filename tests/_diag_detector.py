"""诊断：对比 vendor 的预处理/后处理与原库是否逐字节等价，
并评估 640 分辨率对 320n 模型的影响。仅供排查。"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.getcwd())

img_path = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/HiTE/AppData/Local/Temp/big_photo.png"

print("=== 0. 原库的 RGBA2BGR 对 3 通道图会不会抛错 ===")
mat = cv2.imread(img_path)
print("  imread 通道数:", mat.shape[2])
try:
    cv2.cvtColor(mat, cv2.COLOR_RGBA2BGR)
    print("  RGBA2BGR 对 3 通道: 不抛错")
except Exception as exc:
    print("  RGBA2BGR 对 3 通道: 抛错 ->", str(exc)[:80])

from nudenet import nudenet as nn  # noqa: E402
from nudenet import NudeDetector  # noqa: E402

from backend import config  # noqa: E402
from backend.engines import _postprocess, _preprocess_blob  # noqa: E402
from backend.engines import LocalNudeNetDetector as _LND  # noqa: E402


def LocalNudeNetDetector_LABELS():
    return _LND._LABELS

print()
print("=== 1. 预处理等价性（320 分辨率逐字节对比）===")
m1 = nn._read_image(img_path, 320)
m2 = _preprocess_blob(img_path, 320)
same = np.array_equal(m1[0], m2[0])
print("  形状:", m1[0].shape, m2[0].shape, "| 逐字节相等:", same)
if not same:
    diff = np.abs(m1[0].astype(int) - m2[0].astype(int))
    print("  最大差异:", diff.max(), "| 有差异的通道位置:", int((diff.max(axis=(0, 2, 3)) > 0).sum()))
print("  元数据一致:", m1[1:] == m2[1:], m1[1:], m2[1:])

print()
print("=== 2. 后处理等价性（同 blob、同下限 0.25）===")
det = NudeDetector()
out1 = det.onnx_session.run(None, {det.input_name: m1[0]})
d_orig = nn._postprocess(
    out1, m1[3], m1[4], m1[1], m1[2], m1[5], m1[6], 320, 320
)
d_mine = _postprocess(
    out1, m2[3], m2[4], m2[1], m2[2], m2[5], m2[6], 320, 320, 0.25,
    LocalNudeNetDetector_LABELS(),
)
print("  原库检出数:", len(d_orig), "| vendor 检出数:", len(d_mine))
orig_set = sorted((d["class"], tuple(d["box"])) for d in d_orig)
mine_set = sorted((d["class"], tuple(d["box"])) for d in d_mine)
print("  结果一致:", orig_set == mine_set)
if orig_set != mine_set:
    print("  原库:", orig_set[:5])
    print("  vendor:", mine_set[:5])

print()
print("=== 3. 分辨率与下限的影响（同一张图）===")
for res in (320, 640):
    m = _preprocess_blob(img_path, res)
    out = det.onnx_session.run(None, {det.input_name: m[0]})
    for floor in (0.25, 0.10):
        dd = _postprocess(
            out, m[3], m[4], m[1], m[2], m[5], m[6], res, res, floor,
            LocalNudeNetDetector_LABELS(),
        )
        sensitive = [d for d in dd if d["class"] in (
            "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
            "ANUS_EXPOSED", "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED")]
        top = sorted(dd, key=lambda d: -d["score"])[:3]
        print(f"  分辨率={res} 下限={floor:.2f}: 总检出 {len(dd):3d}  敏感类 {len(sensitive)}"
              f"  最高分={top[0]['score']:.2f}({top[0]['class']})" if dd else
              f"  分辨率={res} 下限={floor:.2f}: 总检出 0")

print()
print("=== 4. 原图尺寸与缩放比例 ===")
print("  原图:", m1[5], "x", m1[6], "| x_ratio:", m1[1], "| x_pad:", m1[3])
