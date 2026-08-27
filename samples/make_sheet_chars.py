"""Generate a handful of small, distinct synthetic "character" PNGs (each its
own transparent-background file) to stand in for individual sticker designs
when demoing the whole-sheet layout pipeline (test_sheet_demo.py)."""

import math

from PIL import Image, ImageDraw


def _blob(cx, cy, r, n=10, wobble=0.3, seed=0):
    import random

    rnd = random.Random(seed)
    pts = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        rr = r * (1 + rnd.uniform(-wobble, wobble))
        pts.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
    return pts


def _star(cx, cy, r_outer, r_inner, n_points=5, rot=0.0):
    pts = []
    for i in range(n_points * 2):
        angle = math.pi * i / n_points + rot
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.sin(angle), cy - r * math.cos(angle)))
    return pts


def make_char(path, kind, color, size=220, seed=0):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    if kind == "blob":
        d.polygon(_blob(c, c, size * 0.32, n=12, seed=seed), fill=color)
    elif kind == "star":
        d.polygon(_star(c, c, size * 0.38, size * 0.16, n_points=5), fill=color)
    elif kind == "ring":
        d.ellipse((c - size * 0.35, c - size * 0.35, c + size * 0.35, c + size * 0.35), fill=color)
        d.ellipse((c - size * 0.16, c - size * 0.16, c + size * 0.16, c + size * 0.16), fill=(0, 0, 0, 0))
    img.save(path)


if __name__ == "__main__":
    chars = [
        ("samples/sheet_char_1.png", "blob", (220, 30, 90, 255)),
        ("samples/sheet_char_2.png", "star", (60, 200, 120, 255)),
        ("samples/sheet_char_3.png", "ring", (250, 180, 20, 255)),
        ("samples/sheet_char_4.png", "blob", (40, 120, 220, 255)),
    ]
    for path, kind, color in chars:
        make_char(path, kind, color, seed=hash(path) % 100)
        print(f"wrote {path}")
