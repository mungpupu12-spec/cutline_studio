"""
Generate the CutLine Studio app icon -- a brand asset for the exe/window
(2026-08-26: "exe 앱 디자인도 세련되고 고급지게" + "컬러도 흑백과 코발트
블루로 변경"), not a design-generation input into the cutline pipeline
itself. Produces:

  assets/app_icon.ico   Windows .exe / title-bar icon (multi-size)
  assets/app_icon.png   cross-platform window icon (Tk iconphoto), also
                        used to sanity-check the design in this sandbox

Design: a rounded cobalt-blue (#0047AB) square with a white dashed diagonal
"cut line" through it and a small solid cut mark at one end -- reads as a
distinct brand mark down to 16x16, and matches the app's own black/white +
cobalt-blue palette (gui/app.py's ACCENT).
"""

import os

from PIL import Image, ImageDraw

COBALT = (0, 71, 171, 255)  # #0047AB
WHITE = (255, 255, 255, 255)

HERE = os.path.dirname(os.path.abspath(__file__))


def _rounded_square(size, color, radius_ratio=0.22):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=color)
    return im


def make_icon(out_dir=HERE, base=512):
    im = _rounded_square(base, COBALT)
    d = ImageDraw.Draw(im)

    # 흰색 점선 대각선("칼선") -- 왼쪽 위 -> 오른쪽 아래, 여백을 둔 안쪽 구간에만.
    margin = base * 0.20
    x0, y0 = margin, margin
    x1, y1 = base - margin, base - margin
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    dash = base * 0.055
    gap = base * 0.045
    stroke = max(2, int(base * 0.045))

    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        sx, sy = x0 + ux * pos, y0 + uy * pos
        ex, ey = x0 + ux * seg_end, y0 + uy * seg_end
        d.line([sx, sy, ex, ey], fill=WHITE, width=stroke)
        pos = seg_end + gap

    # 점선 양 끝에 작은 원("가위/커팅 포인트") -- 브랜드 마크를 더 구체적으로.
    dot_r = base * 0.035
    for cx, cy in ((x0, y0), (x1, y1)):
        d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=WHITE)

    png_path = os.path.join(out_dir, "app_icon.png")
    im.save(png_path)

    ico_path = os.path.join(out_dir, "app_icon.ico")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    im.save(ico_path, format="ICO", sizes=sizes)

    return png_path, ico_path


if __name__ == "__main__":
    png_path, ico_path = make_icon()
    print(f"wrote {png_path}")
    print(f"wrote {ico_path}")
