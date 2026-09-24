"""分类器链路的桩测试。

背景：本机环境无法访问 huggingface.co（含镜像站），ViT 权重下载不到，
因此真实分类器无法在这里跑通。但"分类结果如何影响打码决策"这一段
是本版自己写的逻辑，用桩对象就可以完整验证 —— 而且不依赖网络。

直接运行： python tests/test_pipeline_stub.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.engines as engines  # noqa: E402
from backend import config  # noqa: E402
from backend.engines import ImageProcessor  # noqa: E402

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}: 得到 {actual!r}，期望 {expected!r}")
        failures.append(label)


class FakePipeline:
    """模拟 transformers pipeline 的返回值，包含一条畸形数据。"""

    def __init__(self, payload):
        self.payload = payload

    def __call__(self, path):
        return self.payload


config.ensure_dirs()
work = tempfile.mkdtemp(prefix="stub_")
src = os.path.join(work, "src.png")
cv2.imwrite(src, np.random.default_rng(3).integers(0, 256, (400, 400, 3), dtype=np.uint8))
processor = ImageProcessor()

print("=" * 66)
print("1. classify() 的返回整理能力")
print("=" * 66)

engines.pipeline = lambda *a, **k: FakePipeline(
    [
        {"label": "normal", "score": 0.12},
        {"label": "nsfw", "score": 0.88},
        {"label": "broken"},                 # 缺 score，应被丢弃
        {"score": 0.5},                      # 缺 label，应被丢弃
        "not-a-dict",                        # 类型错误，应被丢弃
    ]
)
classifier = engines.LocalNSFWClassifier()
check("桩已生效", classifier.classifier is not None, True)
scores = classifier.classify(src)
check("畸形条目被过滤，只留 2 条", len(scores), 2)
check("标签为 normal", scores[0], {"label": "normal", "score": 0.12})
check("标签为 nsfw", scores[1], {"label": "nsfw", "score": 0.88})

engines.pipeline = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
broken = engines.LocalNSFWClassifier()
check("加载失败时分类器为空", broken.classifier, None)
check("加载失败时 classify 返回空列表", broken.classify(src), [])

print()
print("=" * 66)
print("2. 分类结果 → 打码决策 的联动")
print("=" * 66)

# 一级部位（≥0.30 即遮），二级部位（受防误杀影响）
tier1 = [{"label": "MALE_GENITALIA_EXPOSED", "score": 0.42, "box": [100, 100, 120, 120]}]
tier2 = [{"label": "FEMALE_BREAST_COVERED", "score": 0.90, "box": [100, 100, 120, 120]}]

CLEAN = [{"label": "normal", "score": 0.95}]
RISKY = [{"label": "porn", "score": 0.90}]


def run(dets, scores_list, tag, **kwargs):
    # 本文件测的是"分类结果如何影响打码决策"，用的是全套部位（含二级），
    # 所以固定 strict 规则；噪声图也不用分割，避免引入无关变量。
    kwargs.setdefault("ruleset", "strict")
    kwargs.setdefault("shape_mask", False)
    out = os.path.join(work, f"{tag}.png")
    _, count, decisions = processor.process(
        src, dets, out, mode="blur", intensity=51, nsfw_scores=scores_list, **kwargs
    )
    return count, decisions


# 2a. 无分类结果
count, _ = run(tier1, [], "t1_none")
check("无分类结果时，一级部位打码", count, 1)

# 2b. 核心回归：整图被判定"非常干净"，一级部位也必须打码
count, decisions = run(tier1, CLEAN, "t1_clean")
check("整图判定干净(0.95)时，一级部位仍打码", count, 1)
check("决策理由写明已打码", decisions[0]["reason"].startswith("已打码"), True)

# 2c. 二级部位仍然受防误杀影响
count, decisions = run(tier2, CLEAN, "t2_clean")
check("整图判定干净时，二级部位被跳过", count, 0)
check("跳过理由写明了防误杀", "防误杀" in decisions[0]["reason"], True)

count, _ = run(tier2, RISKY, "t2_risk")
check("高风险图里二级部位也打码", count, 1)

# 2d. 高风险图的遮罩范围不再被自动放大
_, d_plain = run(tier1, [], "pad_plain", mask_padding=8)
_, d_risk = run(tier1, RISKY, "pad_risk", mask_padding=8)
check(
    "高风险不再额外放大遮罩（原版会 +40px）",
    d_risk[0]["applied_box"] == d_plain[0]["applied_box"],
    True,
)

# 2e. 高风险会加强遮挡强度（马赛克块更大），但范围不变
out_a = os.path.join(work, "strength_plain.png")
out_b = os.path.join(work, "strength_risk.png")
processor.process(src, tier1, out_a, mode="pixel", intensity=40, nsfw_scores=[], mask_padding=8)
processor.process(src, tier1, out_b, mode="pixel", intensity=40, nsfw_scores=RISKY, mask_padding=8)
check(
    "高风险时马赛克强度更高（像素结果不同）",
    bool(np.any(cv2.imread(out_a) != cv2.imread(out_b))),
    True,
)

print()
print("=" * 66)
print("3. 分类器不可用时的降级行为")
print("=" * 66)
engines.pipeline = None
degraded = engines.LocalNSFWClassifier()
check("无 pipeline 时 classifier 为 None", degraded.classifier, None)
check("无 pipeline 时 classify 返回 []", degraded.classify(src), [])
count, _ = run(tier1, [], "degraded")
check("降级后检测器仍可独立完成打码", count, 1)

print()
print("=" * 66)
print("4. 灵敏度档位在完整链路中生效")
print("=" * 66)
low = [{"label": "MALE_GENITALIA_EXPOSED", "score": 0.42, "box": [100, 100, 120, 120]}]
count_strict, _ = run(low, [], "sens_strict", sensitivity="strict")
count_balanced, _ = run(low, [], "sens_balanced", sensitivity="balanced")
count_aggressive, _ = run(low, [], "sens_aggressive", sensitivity="aggressive")
check("严格档放过 0.42 的检出", count_strict, 0)
check("标准档遮住 0.42 的检出", count_balanced, 1)
check("高召回档遮住 0.42 的检出", count_aggressive, 1)

print()
print("=" * 66)
if failures:
    print(f"结果：{len(failures)} 项失败")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("结果：全部通过")
