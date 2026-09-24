"""推理与打码逻辑测试。

用**合成图片 + 伪造的检测框**来验证打码链路，
不需要、也不使用任何真实的敏感素材。

直接运行： python tests/test_engines.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config  # noqa: E402
from backend.engines import (  # noqa: E402
    ImageProcessor,
    LocalNudeNetDetector,
    _parse_hex_color,
    read_image,
)

# 两个预设的部位集合
STRICT_MUST = config.CENSOR_PROFILES["strict"]["must"]
STRICT_AMBIGUOUS = config.CENSOR_PROFILES["strict"]["ambiguous"]
PIXIV_MUST = config.CENSOR_PROFILES["pixiv"]["must"]

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}: 得到 {actual!r}，期望 {expected!r}")
        failures.append(label)


def make_noise_image(path: str, w: int = 512, h: int = 512) -> None:
    """高对比度随机噪声图 —— 打码后像素统计会有明显变化，便于断言。"""
    rng = np.random.default_rng(20260923)
    cv2.imwrite(path, rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8))


def region_variance(img: np.ndarray, box) -> float:
    x, y, w, h = box
    return float(np.var(img[y : y + h, x : x + w]))


config.ensure_dirs()
workdir = tempfile.mkdtemp(prefix="nsfwtest_")
src = os.path.join(workdir, "src.png")
make_noise_image(src)

print("=" * 66)
print("1. 读图与像素上限")
print("=" * 66)
img = read_image(src)
check("能读回图片", img is not None and img.shape[:2] == (512, 512), True)
check("cv2.imread 可用", cv2.imread(src) is not None, True)

print()
print("=" * 66)
print("2. 打码：三种模式都要真的改动 ROI")
print("=" * 66)
processor = ImageProcessor()
# 这些用例测的是打码机械行为（三种模式/并发/参数防御），与打码规则无关，
# 而 BELLY_EXPOSED 在 pixiv 规则下不遮，所以显式用 strict。
detection = [{"label": "BELLY_EXPOSED", "score": 0.99, "box": [150, 150, 200, 200]}]
roi_box = (150, 150, 200, 200)

before = cv2.imread(src)
before_var = region_variance(before, roi_box)

for mode in ("blur", "pixel", "solid"):
    out = os.path.join(workdir, f"out_{mode}.png")
    _, count, _ = processor.process(
        src,
        detection,
        out,
        mode=mode,
        intensity=51,
        nsfw_scores=[],
        color_hex="#FF00AA",
        # 噪声图没有可分割的"物体"，这里固定用矩形模式，
        # 否则断言会依赖 GrabCut 在纯噪声上的随机表现。
        shape_mask=False,
        ruleset="strict",
        mask_inset=0,
    )
    after = cv2.imread(out)
    check(f"{mode}: 打码 1 处", count, 1)
    check(f"{mode}: 输出文件存在", os.path.exists(out), True)
    check(
        f"{mode}: ROI 像素统计发生变化",
        abs(region_variance(after, roi_box) - before_var) > 1.0,
        True,
    )

# solid 模式要精确填成指定颜色（BGR = (AA, 00, FF)）
solid_img = cv2.imread(os.path.join(workdir, "out_solid.png"))
check("solid: 填充色为 #FF00AA", solid_img[250, 250].tolist(), [170, 0, 255])
# 注意：不能用 np.var==0 判断"纯色"，因为 np.var 会把三个通道混在一起算，
# 纯色区的方差恒为 11238.89。正确的判据是区域内只有一种颜色。
solid_roi = solid_img[150:350, 150:350]
check("solid: ROI 内只有一种颜色", len(np.unique(solid_roi.reshape(-1, 3), axis=0)), 1)
check(
    "solid: 填充区边界正确(140 与 361)",
    solid_img[139, 139].tolist() != [170, 0, 255]
    and solid_img[361, 361].tolist() != [170, 0, 255],
    True,
)
check("_parse_hex_color(#FF00AA) -> BGR", _parse_hex_color("#FF00AA"), (170, 0, 255))
check(
    "_parse_hex_color(非法值) 回退黑色",
    _parse_hex_color("not-a-color; rm -rf"),
    (0, 0, 0),
)
check("_parse_hex_color(空串) 回退黑色", _parse_hex_color(""), (0, 0, 0))

print()
print("=" * 66)
print("3. 一级部位：不得被「防误杀」逻辑降级（本次修复的核心）")
print("=" * 66)
print("   背景：原实现把生殖器也塞进了防误杀降级里 —— 整图被判偏正常时，")
print("   暴露类标签的门槛被抬到 0.60~0.80，而 nudenet 最低只输出 0.25，")
print("   于是 0.25~0.55 之间的检出被全部丢弃，男性生殖器几乎必然漏掉。")
print()


def censor(
    label,
    score,
    normal_score=0.0,
    is_high_risk=False,
    sensitivity="balanced",
    ruleset="strict",
):
    """默认用 strict 预设，便于沿用历史用例；pixiv 的用例显式传 ruleset。"""
    return ImageProcessor._should_censor(
        label, score, normal_score, is_high_risk, sensitivity, ruleset
    )


# 核心回归：低分 + 整图判定非常干净，也必须打码
# （女性阴部例外：专用门槛 0.45 高于这些分数，0.42 会被放行 —— 见 6d 节）
for label in sorted(STRICT_MUST):
    ok, _ = censor(label, 0.42, normal_score=0.95)
    check(f"strict: {label} @0.42 + 整图很干净(0.95) 仍打码（含一线天）", ok, True)

ok, reason = censor("MALE_GENITALIA_EXPOSED", 0.42, normal_score=0.95)
check("理由写明已打码", reason, "已打码")

# 门槛边界
ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.30, sensitivity="balanced")
check("标准档 0.30 达到门槛", ok, True)
ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.29, sensitivity="balanced")
check("标准档 0.29 未达门槛", ok, False)

ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.60, sensitivity="strict")
check("严格档 0.60 达到门槛", ok, True)
ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.59, sensitivity="strict")
check("严格档 0.59 未达门槛", ok, False)

ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.25, sensitivity="aggressive")
check("高召回档 0.25（模型下限）打码", ok, True)

# 灵敏度单调性：同一条检出，宽松档不会比严格档更容易放过
for score in (0.2, 0.3, 0.5, 0.65):
    strict_ok = censor("MALE_GENITALIA_EXPOSED", score, sensitivity="strict")[0]
    balanced_ok = censor("MALE_GENITALIA_EXPOSED", score, sensitivity="balanced")[0]
    check(f"灵敏度单调性 @{score}", bool(balanced_ok) >= bool(strict_ok), True)

# 整图被判"非常干净"也不能让一级部位漏掉
for normal in (0.0, 0.75, 0.95, 0.999):
    ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.35, normal_score=normal)
    check(f"normal_score={normal} 时仍打码", ok, True)

print()
print("=" * 66)
print("4. 二级部位：保留原有「防误杀」策略")
print("=" * 66)
for label in sorted(STRICT_AMBIGUOUS):
    ok, reason = censor(label, 0.90, normal_score=0.95)
    check(f"strict: {label} 整图很干净时跳过", ok, False)

ok, reason = censor("FEMALE_BREAST_COVERED", 0.90, normal_score=0.95)
check("跳过理由会说明原因", "防误杀" in reason, True)

ok, _ = censor("FEMALE_BREAST_COVERED", 0.90, normal_score=0.10, is_high_risk=True)
check("高风险图仍会遮二级部位", ok, True)

ok, _ = censor("BELLY_EXPOSED", 0.80, normal_score=0.85)
check("整图倾向正常(0.85)时 0.80 达标", ok, True)

ok, _ = censor("BELLY_EXPOSED", 0.50, normal_score=0.0)
check("普通情况 0.50 低于 0.55 门槛", ok, False)

ok, reason = censor("FACE_FEMALE", 0.99, normal_score=0.0)
check("面部不打码", ok, False)
check("非敏感部位的理由", "不需要遮挡该部位" in reason, True)

print()
print("=" * 66)
print("4b. pixiv 规则：只遮性器官与肛门，臀部胸部不打")
print("=" * 66)
print("   依 pixiv 投稿规范：必须遮 阴蒂/掰开的阴部、掰开的肛门、阴茎；")
print("   臀部、胸部、睾丸、一线天、未掰开的肛门不打。")
print()

check("pixiv 预设的 must 集合正是这几个必遮部位", PIXIV_MUST,
      {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
       "MALE_GENITALIA_COVERED", "ANUS_EXPOSED"})
check("pixiv 是默认规则", config.DEFAULT_RULESET, "pixiv")

# 该遮的：低分也要遮（这是 pixiv 过审的硬要求）
for label in sorted(PIXIV_MUST):
    ok, _ = censor(label, 0.30, normal_score=0.95, ruleset="pixiv")
    check(f"pixiv: {label} @0.30 仍打码（含一线天）", ok, True)

# 明确不打：即使分数很高、整图判定高风险
not_censored = [
    ("BUTTOCKS_EXPOSED", "臀部"),
    ("BUTTOCKS_COVERED", "臀部(被遮)"),
    ("FEMALE_BREAST_EXPOSED", "胸部"),
    ("FEMALE_BREAST_COVERED", "胸部(被遮)"),
    ("MALE_BREAST_EXPOSED", "男性胸部"),
    ("FEMALE_GENITALIA_COVERED", "一线天/被遮的阴部"),
    ("ANUS_COVERED", "未掰开的肛门"),
    ("BELLY_EXPOSED", "腹部"),
    ("FACE_FEMALE", "面部"),
]
for label, desc in not_censored:
    ok, reason = censor(label, 0.99, normal_score=0.0, is_high_risk=True, ruleset="pixiv")
    check(f"pixiv: {desc}({label}) 不打码", ok, False)
    check(f"pixiv: {desc} 的理由写明规则", "pixiv" in reason, True)

# 灵敏度在 pixiv 规则下依然生效
ok, _ = censor("MALE_GENITALIA_EXPOSED", 0.50, sensitivity="strict", ruleset="pixiv")
check("pixiv: 严格档会放过 0.50 的生殖器", ok, False)

# in_scope 标记：pixiv 规则下臀部不在范围内
ok, _ = censor("BUTTOCKS_EXPOSED", 0.99, ruleset="pixiv")
check("pixiv: 臀部跳过且理由明确", ok, False)

# 未知的规则名回退默认（pixiv）
ok_pixiv, _ = censor("MALE_GENITALIA_EXPOSED", 0.35, ruleset="不存在的规则")
ok_strict, _ = censor("FEMALE_BREAST_EXPOSED", 0.99, ruleset="不存在的规则")
check("未知规则回退 pixiv（生殖器仍打码）", ok_pixiv, True)
check("未知规则回退 pixiv（胸部不打码）", ok_strict, False)

print()
print("=" * 66)
print("4c. 几何关联：交合区域合并、生殖器向面部延伸")
print("=" * 66)
from backend.engines import _group_related, _bridge_to_face  # noqa: E402

# 交合：男女生殖器框紧挨着（缝隙 15px）→ 异类合并，否则缝隙漏遮
groups = _group_related(
    [[200, 300, 80, 90], [210, 375, 90, 80]],
    [],
    labels=["MALE_GENITALIA_EXPOSED", "FEMALE_GENITALIA_EXPOSED"],
)
check("交合：相邻框合并为一组", len(groups), 1)
check("交合：成员数正确", len(groups[0][1]), 2)
check("交合：合并框覆盖缝隙", groups[0][0][1] + groups[0][0][3] >= 455, True)
check("交合：说明文字已标注", "相邻部位已合并" in groups[0][2], True)

# 多个同类框（画面里有多个阴茎）：相邻也不合并 —— 各自走轮廓贴合，
# 合并把它们连成一大块矩形（实测回归：挡一大块）
groups = _group_related(
    [[100, 200, 70, 80], [190, 210, 70, 85], [280, 195, 70, 80]],
    [],
    labels=["MALE_GENITALIA_EXPOSED"] * 3,
)
check("同类相邻框不合并（数量）", len(groups), 3)
check("同类相邻框不合并（成员各自独立）",
      sorted(len(g[1]) for g in groups), [1, 1, 1])
check("同类框无合并说明", all(g[2] == "" for g in groups), True)

# 异类合并不受影响（男 + 女）
groups = _group_related(
    [[100, 200, 70, 80], [110, 260, 75, 80]],
    [],
    labels=["MALE_GENITALIA_EXPOSED", "FEMALE_GENITALIA_EXPOSED"],
)
check("异类相邻框仍合并", len(groups), 1)

# 口交：阴茎在下方、面部在上，间隔 70px → 桥接到面部底边为止
groups = _group_related([[300, 400, 90, 110]], [[310, 180, 140, 150]])
box, _, note = groups[0]
check("口交：桥接后顶部到达面部底边(330)", box[1], 330)
check("口交：桥接后仍覆盖原框底部", box[1] + box[3] >= 510, True)
check("口交：说明文字已标注", "面部" in note, True)

# 未掰开的正常距离不应桥接
groups = _group_related([[300, 900, 90, 110]], [[310, 100, 140, 150]])
check("远距不桥接", groups[0][2], "")

# 孤立框保持原样（走轮廓贴合）
groups = _group_related([[100, 100, 50, 60]], [])
check("孤立框不变", groups[0][0], [100, 100, 50, 60])
check("孤立框无合并说明", groups[0][2], "")

# 桥接边界：面部在生殖器下方
ext = _bridge_to_face([300, 200, 90, 110], [310, 400, 140, 150])
check("面部在下方的桥接", ext, [300, 200, 150, 200])
# 面部在左侧
ext = _bridge_to_face([300, 200, 90, 110], [100, 210, 140, 150])
check("面部在左侧的桥接", ext[2] > 0 and ext[0] <= 240, True)

print()
print("=" * 66)
print("5. 遮罩扩张（矩形模式）：这是「糊一大片」的直接来源")
print("=" * 66)
small = [{"label": "MALE_GENITALIA_EXPOSED", "score": 0.90, "box": [200, 200, 40, 40]}]


def mask_area(padding: int, shape: bool = False) -> int:
    """测量实际遮罩的外接框面积。

    这里必须显式 feather=0：默认羽化会把掩膜边缘向外糊开几个像素，
    外接框就不再等于几何值了。这些断言测的是几何关系，不是羽化效果。
    """
    path = os.path.join(workdir, f"pad_{padding}_{shape}.png")
    _, _, decisions = processor.process(
        src,
        small,
        path,
        mode="solid",
        intensity=51,
        mask_padding=padding,
        shape_mask=shape,
        feather=0,
        mask_inset=0,
    )
    box = decisions[0]["applied_box"]
    return box[2] * box[3]


area_0 = mask_area(0)
area_8 = mask_area(8)
area_40 = mask_area(40)

check("padding=0 时遮罩严格等于检测框", area_0, 40 * 40)
check("padding=8 时遮罩大于检测框", area_8, 56 * 56)
check("padding=40 时遮罩大幅膨胀（原版行为）", area_40, 120 * 120)
check("padding=40 的遮罩面积是 padding=0 的 9 倍", round(area_40 / area_0), 9)
check("默认扩张量已收小（3px）", config.DEFAULT_MASK_PADDING, 3)

# 超范围要被夹紧
big = os.path.join(workdir, "pad_clamp.png")
_, _, decisions = processor.process(
    src,
    small,
    big,
    mode="solid",
    intensity=51,
    mask_padding=9999,
    shape_mask=False,
    feather=0,
    mask_inset=0,
)
check("padding 超上限被夹紧到 60", decisions[0]["applied_box"][2], 40 + 60 * 2)

# 顺带确认羽化确实会让外接框变大（说明羽化真的生效了）
_, _, soft_dec = processor.process(
    src,
    small,
    os.path.join(workdir, "pad_soft.png"),
    mode="solid",
    intensity=51,
    mask_padding=0,
    shape_mask=False,
    feather=6,
    mask_inset=0,
)
check(
    "羽化会让遮罩外接框略微变大",
    soft_dec[0]["applied_box"][2] > 40,
    True,
)

print()
print("=" * 66)
print("6. 轮廓贴合遮罩：只打码该部位，不糊整个框")
print("=" * 66)
print("   做法与 PS 快速选择一致：以检测框为种子，在框外一圈上下文里")
print("   用 GrabCut 分割出部位的真实边界，只在轮廓内打码。")
print()

# 造一张有明确"部位"的合成图：皮肤色渐变背景 + 不规则高对比目标
import tempfile as _tf  # noqa: E402

scene_dir = _tf.mkdtemp(prefix="nsfwscene_")
SH, SW = 400, 400
scene = np.zeros((SH, SW, 3), np.uint8)
for yy in range(SH):
    scene[yy, :] = (
        int(160 + 25 * np.sin(yy / 30)),
        int(175 + 25 * np.cos(yy / 22)),
        int(200 + 20 * np.sin(yy / 17)),
    )
scene_rng = np.random.default_rng(5)
scene = np.clip(
    scene.astype(int) + scene_rng.integers(-8, 8, (SH, SW, 3)), 0, 255
).astype(np.uint8)

target = np.zeros((SH, SW), np.uint8)
poly = np.array(
    [[150, 120], [196, 104], [238, 138], [244, 196], [212, 238], [162, 244], [126, 206], [124, 156]],
    np.int32,
)
cv2.fillPoly(target, [poly], 255)
tex = scene_rng.integers(90, 170, (SH, SW, 3))
scene[target > 0] = np.clip(
    tex[target > 0] * 0.7 + np.array([70, 45, 95]), 0, 255
).astype(np.uint8)

scene_path = os.path.join(scene_dir, "scene.png")
cv2.imwrite(scene_path, scene)

tx, ty, tw, th = cv2.boundingRect(poly)
target_area = int(np.count_nonzero(target))
rect_area = tw * th
scene_det = [{"label": "MALE_GENITALIA_EXPOSED", "score": 0.80, "box": [tx, ty, tw, th]}]

print(f"   合成场景：目标本体 {target_area} px，外接矩形 {rect_area} px ({tw}x{th})")


def run_scene(shape: bool, pad: int = 0, feather_px: int = 0):
    out_path = os.path.join(scene_dir, f"s_{shape}_{pad}_{feather_px}.png")
    _, cnt, dec = processor.process(
        scene_path,
        scene_det,
        out_path,
        mode="solid",
        intensity=51,
        mask_padding=pad,
        shape_mask=shape,
        feather=feather_px,
        mask_inset=0,
    )
    img_out = cv2.imread(out_path)
    region = img_out[ty : ty + th, tx : tx + tw]
    filled = np.all(region == 0, axis=2)
    masked = int(np.count_nonzero(filled))
    inside = int(np.count_nonzero((target[ty : ty + th, tx : tx + tw] > 0) & filled))
    return dec[0], masked, inside


d_rect, masked_rect, inside_rect = run_scene(False)
check("矩形模式：遮罩填满整个检测框", masked_rect, rect_area)
check("矩形模式：多遮了背景", masked_rect - inside_rect, rect_area - target_area)
check("矩形模式的 shape 标记", d_rect["shape"], "rect")
check("矩形模式的遮罩占比为 100%", d_rect["mask_ratio"], 1.0)

d_cont, masked_cont, inside_cont = run_scene(True)
check("轮廓模式：shape 标记为 contour", d_cont["shape"], "contour")
check("轮廓模式：遮罩明显小于检测框", masked_cont < rect_area, True)
check(
    "轮廓模式：遮罩面积比矩形减少 20% 以上",
    (rect_area - masked_cont) / rect_area > 0.20,
    True,
)
check(
    "轮廓模式：仍覆盖目标本体的 98% 以上",
    inside_cont / target_area >= 0.98,
    True,
)
check(
    "轮廓模式：多遮的背景显著减少",
    (masked_cont - inside_cont) < (rect_area - target_area) * 0.1,
    True,
)
check("轮廓模式的遮罩占比 < 100%", d_cont["mask_ratio"] < 1.0, True)
check("轮廓模式的外接框贴近目标", abs(d_cont["applied_box"][2] - tw) <= 4, True)
check(
    "轮廓模式的理由文案",
    d_cont["reason"],
    "已打码（轮廓贴合）",
)

# 扩张量在轮廓模式下作用在轮廓上，而不是把矩形撑大
d_cont_pad, masked_pad, _ = run_scene(True, pad=6)
check(
    "轮廓模式下扩张量只小幅增大遮罩",
    masked_pad < rect_area,
    True,
)
check("加了扩张后遮罩确实变大", masked_pad > masked_cont, True)

# 羽化只影响边缘过渡，不改变遮罩的最大覆盖范围
d_soft, masked_soft, _ = run_scene(True, feather_px=6)
check(
    "羽化不显著扩大遮罩面积",
    abs(masked_soft - masked_cont) < rect_area * 0.15,
    True,
)
check("羽化后仍标记为轮廓模式", d_soft["shape"], "contour")

# 分割失败时必须退回矩形（对打码工具来说，多遮远比漏掉安全）
tiny = os.path.join(scene_dir, "tiny.png")
cv2.imwrite(tiny, np.zeros((8, 8, 3), np.uint8))
_, _, tiny_dec = processor.process(
    tiny,
    [{"label": "MALE_GENITALIA_EXPOSED", "score": 0.9, "box": [2, 2, 4, 4]}],
    os.path.join(scene_dir, "tiny_out.png"),
    mode="solid",
    shape_mask=True,
)
check("区域过小时退回矩形", tiny_dec[0]["shape"], "rect")

print()
print("=" * 66)
print("6b. 遮罩内收：解决「矩形框遮到周围皮肤」")
print("=" * 66)
# 同一个 200x200 的框，内收 25% 后遮罩应明显变小
def mask_inset_area(inset_pct: int) -> int:
    path = os.path.join(workdir, f"inset_{inset_pct}.png")
    _, _, decisions = processor.process(
        src,
        small,
        path,
        mode="solid",
        intensity=51,
        mask_padding=0,
        shape_mask=False,
        feather=0,
        mask_inset=inset_pct,
    )
    box = decisions[0]["applied_box"]
    return box[2] * box[3]


a0 = mask_inset_area(0)
a25 = mask_inset_area(25)
a40 = mask_inset_area(40)
check("内收 0% = 整个检测框", a0, 40 * 40)
check("内收 25% 后遮罩缩小", a25 < a0, True)
check("内收 25% 的面积符合预期", a25, 20 * 20)
check("内收 40% 比 25% 更小", a40 < a25, True)
check("默认内收为 15%", config.DEFAULT_MASK_INSET, 15)

print()
print("=" * 66)
print("6c. 最高召回档：0.10~0.30 之间的检出不再被门槛丢弃")
print("=" * 66)
# 0.15 的生殖器检出：标准档丢弃、最高召回遮住 —— 这正是难姿势漏检的主因
ok_bal, r_bal = censor("MALE_GENITALIA_EXPOSED", 0.15, ruleset="pixiv", sensitivity="balanced")
check("标准档丢弃 0.15 的检出", ok_bal, False)
ok_max, r_max = censor("MALE_GENITALIA_EXPOSED", 0.15, ruleset="pixiv", sensitivity="max")
check("最高召回遮住 0.15 的检出", ok_max, True)
ok_max2, _ = censor("FEMALE_GENITALIA_EXPOSED", 0.10, ruleset="pixiv", sensitivity="max")
# 专用门槛已降为 0.10（= 检测下限）：一线天也打码（用户决定）
check("女性阴部 0.10（一线天分数段）也打码", ok_max2, True)
ok_max3, _ = censor("ANUS_EXPOSED", 0.12, ruleset="pixiv", sensitivity="max")
check("最高召回遮住 0.12 的肛门", ok_max3, True)
# 灵敏度阈值单调性
check("阈值单调：max < aggressive < balanced < strict",
      config.SENSITIVITY_THRESHOLDS["max"] < config.SENSITIVITY_THRESHOLDS["aggressive"]
      < config.SENSITIVITY_THRESHOLDS["balanced"] < config.SENSITIVITY_THRESHOLDS["strict"],
      True)
check("默认灵敏度是最高召回", config.DEFAULT_SENSITIVITY, "max")

print()
print("=" * 66)
print("6d. 女性阴部：一线天也打码（专用门槛 = 检测下限）")
print("=" * 66)
ok_fg_low, r_fg_low = censor("FEMALE_GENITALIA_EXPOSED", 0.32, ruleset="pixiv", sensitivity="max")
check("女性阴部 0.32（一线天分数段）打码", ok_fg_low, True)
check("理由为已打码", r_fg_low, "已打码")
ok_fg_high, _ = censor("FEMALE_GENITALIA_EXPOSED", 0.62, ruleset="pixiv", sensitivity="max")
check("女性阴部 0.62 打码（掰开）", ok_fg_high, True)
# 其他部位不受影响
ok_male, _ = censor("MALE_GENITALIA_EXPOSED", 0.32, ruleset="pixiv", sensitivity="max")
check("男性生殖器 0.32 仍打码", ok_male, True)
ok_anus, _ = censor("ANUS_EXPOSED", 0.32, ruleset="pixiv", sensitivity="max")
check("肛门 0.32 仍打码", ok_anus, True)
check("女性门槛默认 0.10（= 检测下限，一线天也打码）",
      config.LABEL_THRESHOLDS["FEMALE_GENITALIA_EXPOSED"], 0.10)
# 男性生殖器专用门槛（默认与检测下限一致，可独立调整）
check("男性门槛默认 0.10", config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"], 0.10)
ok_mg, _ = censor("MALE_GENITALIA_EXPOSED", 0.20, ruleset="pixiv", sensitivity="max")
check("男性 @0.20 默认门槛下打码", ok_mg, True)
# 模拟用户调高门槛：低于门槛的检出被放行
saved = config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"]
config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"] = 0.35
ok_mg2, r_mg2 = censor("MALE_GENITALIA_EXPOSED", 0.32, ruleset="pixiv", sensitivity="max")
check("男性门槛调 0.35 后 @0.32 放行", ok_mg2, False)
check("放行理由写明 0.35", "0.35" in r_mg2, True)
ok_mg3, _ = censor("MALE_GENITALIA_EXPOSED", 0.40, ruleset="pixiv", sensitivity="max")
check("男性门槛调 0.35 后 @0.40 打码", ok_mg3, True)
config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"] = saved
# 灵敏度门槛更严时取更严者（strict 0.60 > 专属 0.35）
config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"] = 0.35
ok_mg4, r_mg4 = censor("MALE_GENITALIA_EXPOSED", 0.50, ruleset="pixiv", sensitivity="strict")
check("strict 档下取更严门槛 0.60", ok_mg4, False)
check("理由写明 strict 门槛 0.60", "0.60" in r_mg4, True)
config.LABEL_THRESHOLDS["MALE_GENITALIA_EXPOSED"] = saved

# 疲软阴茎常被模型误判成 COVERED —— 一并遮（勃起与否都打码）
ok_cvd, r_cvd = censor("MALE_GENITALIA_COVERED", 0.32, ruleset="pixiv", sensitivity="max")
check("男性 COVERED（疲软误判）@0.32 pixiv 打码", ok_cvd, True)
check("COVERED 理由为已打码", r_cvd, "已打码")
ok_cvd2, _ = censor("MALE_GENITALIA_COVERED", 0.90, ruleset="pixiv", sensitivity="strict")
check("男性 COVERED @0.90 strict 打码", ok_cvd2, True)
check("MG COVERED 门槛默认 0.10", config.LABEL_THRESHOLDS["MALE_GENITALIA_COVERED"], 0.10)

# strict 档：门槛取更严者
ok_strict, _ = censor("FEMALE_GENITALIA_EXPOSED", 0.55, ruleset="pixiv", sensitivity="strict")
check("strict 档下 0.55 < 0.60 放行", ok_strict, False)
ok_strict2, r_strict2 = censor("FEMALE_GENITALIA_EXPOSED", 0.65, ruleset="pixiv", sensitivity="strict")
check("strict 档下 0.65 >= 0.60 直接打码", ok_strict2 and r_strict2 == "已打码", True)

# SAM 掩膜精修（保留的能力）
from backend.engines import _refine_part_mask  # noqa: E402

refine_roi = np.zeros((220, 220), np.uint8)
refine_roi[40:140, 40:140] = 255          # 框内主体
refine_roi[10:30, 180:210] = 255          # 框外碎片
refined = _refine_part_mask(refine_roi, (40, 40, 100, 100))
assert refined is not None
check("精修后碎片被移除", int(np.count_nonzero(refined[10:30, 180:210])), 0)
check("精修后主体保留", int(np.count_nonzero(refined[40:140, 40:140])), 100 * 100)
off_roi = np.zeros((220, 220), np.uint8)
off_roi[40:140, 40:130] = 255
off_roi[0:20, 0:20] = 255
refined2 = _refine_part_mask(off_roi, (50, 50, 100, 100))
check("远角小块被移除", int(np.count_nonzero(refined2[0:20, 0:20])), 0)
far_roi = np.zeros((220, 220), np.uint8)
far_roi[0:20, 0:20] = 255
check("掩膜完全在框外时返回 None", _refine_part_mask(far_roi, (100, 100, 80, 80)) is None, True)

print()
print("=" * 66)
print("7. 检测明细（前端「为什么没遮」的数据来源）")
print("=" * 66)
mixed = [
    {"label": "MALE_GENITALIA_EXPOSED", "score": 0.90, "box": [50, 50, 60, 60]},
    {"label": "FEMALE_BREAST_COVERED", "score": 0.95, "box": [200, 200, 60, 60]},
    {"label": "FACE_MALE", "score": 0.99, "box": [300, 300, 60, 60]},
]
out = os.path.join(workdir, "mixed.png")
_, count, decisions = processor.process(
    src,
    mixed,
    out,
    mode="blur",
    intensity=51,
    nsfw_scores=[{"label": "normal", "score": 0.96}],
    shape_mask=False,
    ruleset="strict",
    mask_inset=0,
)
check("只有一级部位被打码", count, 1)
check("明细条数与检出数一致", len(decisions), 3)
check("明细带 label", decisions[0]["label"], "MALE_GENITALIA_EXPOSED")
check("明细带 score", decisions[0]["score"], 0.90)
check("明细带 censored 标记", decisions[0]["censored"], True)
check("被跳过的项标记为 False", decisions[1]["censored"], False)
check(
    "被跳过的项带理由",
    isinstance(decisions[1]["reason"], str) and decisions[1]["reason"] != "",
    True,
)
check("已打码的项记录实际遮罩框", len(decisions[0]["applied_box"]), 4)
check("已打码的项带 shape 标记", decisions[0]["shape"], "rect")
check("已打码的项带遮罩占比", isinstance(decisions[0]["mask_ratio"], float), True)

print()
print("=" * 66)
print("8. 参数防御：非法输入不应该抛异常或产生副作用")
print("=" * 66)
out = os.path.join(workdir, "out_clamp.png")
_, count, _ = processor.process(
    src, detection, out, mode="blur", intensity=100000, shape_mask=False, ruleset="strict",
    mask_inset=0,
)
check("intensity=100000 被夹紧且成功", count, 1)
_, count, _ = processor.process(
    src, detection, out, mode="blur", intensity=-999, shape_mask=False, ruleset="strict",
    mask_inset=0,
)
check("intensity=-999 被夹紧且成功", count, 1)
_, count, _ = processor.process(
    src, detection, out, mode="blur", sensitivity="瞎写的", shape_mask=False, ruleset="strict",
    mask_inset=0,
)
check("未知灵敏度回退默认档", count, 1)
_, count, _ = processor.process(
    src, detection, out, mode="blur", feather=99999, shape_mask=False, ruleset="strict",
    mask_inset=0,
)
check("feather 超上限被夹紧", count, 1)
weird = [
    {"label": "BELLY_EXPOSED", "score": 0.99, "box": None},
    {"label": "BELLY_EXPOSED", "score": 0.99, "box": [1, 2, 3]},
    {"label": "BELLY_EXPOSED", "score": 0.99, "box": ["a", "b", "c", "d"]},
    {"label": "BELLY_EXPOSED", "score": 0.99, "box": [0, 0, 0, 0]},
    {"label": "NOT_A_REAL_LABEL", "score": 0.99, "box": [10, 10, 50, 50]},
]
_, count, _ = processor.process(src, weird, out, mode="blur", intensity=51, shape_mask=False, ruleset="strict", mask_inset=0)
check("畸形检测框全部跳过", count, 0)
try:
    processor.process(src, detection, out, mode="../../etc/passwd", intensity=51)
    check("非法 mode 抛 ValueError", False, True)
except ValueError:
    check("非法 mode 抛 ValueError", True, True)

print()
print("=" * 66)
print("9. 并发安全：原版在这里会互相覆盖 intensity")
print("=" * 66)


def run_one(intensity: int) -> float:
    path = os.path.join(workdir, f"conc_{intensity}.png")
    ImageProcessor().process(
        src, detection, path, mode="blur", intensity=intensity, shape_mask=False, ruleset="strict",
        mask_inset=0,
    )
    return region_variance(cv2.imread(path), roi_box)


with ThreadPoolExecutor(max_workers=4) as pool:
    weak, strong = list(pool.map(run_one, [11, 151]))
check("小强度结果与大强度结果不同（未被串改）", weak > strong, True)

with ThreadPoolExecutor(max_workers=4) as pool:
    again = list(pool.map(run_one, [11, 151]))
check("并发重复执行结果稳定", abs(again[0] - weak) < 1e-6, True)

print()
print("=" * 66)
print("10. NudeNet 检测器（内置 320n 模型，无需联网）")
print("=" * 66)
detector = LocalNudeNetDetector()
result = detector.detect(src)
check("对噪声图返回列表", isinstance(result, list), True)
check("噪声图无敏感检出", len(result), 0)

big_img = os.path.join(workdir, "big.png")
make_noise_image(big_img, 1200, 900)
check("大图 Deep Scan 不报错", isinstance(detector.detect(big_img), list), True)

# 校验 Deep Scan 的内存推理路径与落盘路径等价（依赖库内部行为）
from nudenet.nudenet import _read_image  # noqa: E402

blob_a, *meta_a = _read_image(src)
blob_b, *meta_b = _read_image(cv2.imread(src))
check("内存图像与文件路径的预处理完全一致", bool(np.array_equal(blob_a, blob_b)), True)
check("尺寸元数据也一致", meta_a == meta_b, True)

print()
print("=" * 66)
if failures:
    print(f"结果：{len(failures)} 项失败")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("结果：全部通过")
