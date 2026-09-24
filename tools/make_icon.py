"""生成 NSFW Mask 的应用图标：favicon.svg / favicon.ico / app.ico。

设计：深色圆角底板 + 3x3 马赛克块（中央高亮）—— 「打码」的直观隐喻。
用法：python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

SIZE = 1024
BG_TOP = (26, 29, 41)        # 深蓝黑
BG_BOTTOM = (42, 47, 69)
TILE_COLORS = [              # 3x3 从青到紫，中心亮橙
    (56, 189, 248), (34, 211, 238), (45, 212, 191),
    (99, 102, 241), (251, 146, 60), (139, 92, 246),
    (167, 139, 250), (129, 140, 248), (96, 165, 250),
]


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def build_base() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景板：垂直渐变的圆角方形
    bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    for y in range(SIZE):
        t = y / SIZE
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,)
        bg_draw.line([(0, y), (SIZE, y)], fill=color)
    mask = Image.new("L", (SIZE, SIZE), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([16, 16, SIZE - 16, SIZE - 16], radius=200, fill=255)
    img.paste(bg, (0, 0), mask)

    # 3x3 马赛克块
    tile = 200
    gap = 26
    grid = tile * 3 + gap * 2
    ox = (SIZE - grid) // 2
    oy = (SIZE - grid) // 2
    for r in range(3):
        for c in range(3):
            x0 = ox + c * (tile + gap)
            y0 = oy + r * (tile + gap)
            rounded_rect(
                draw,
                [x0, y0, x0 + tile, y0 + tile],
                radius=52,
                fill=TILE_COLORS[r * 3 + c] + (255,),
            )
    return img


def main() -> None:
    base = build_base()

    # favicon.ico：浏览器多分辨率
    ico_path = FRONTEND / "favicon.ico"
    base.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成 {ico_path}")

    # app.ico：桌面快捷方式用（同一个文件内容，放项目根便于选取）
    app_ico = ROOT / "app.ico"
    base.save(
        app_ico,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成 {app_ico}")


if __name__ == "__main__":
    main()
