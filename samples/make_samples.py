"""Generate synthetic test artwork (transparent PNG) resembling the kind of
multi-object sticker sheet in the reference screenshot: a couple of blobby
shapes plus a 4-point sparkle star, so we can test that:
  1) individual disjoint shapes each get their own offset
  2) shapes placed close together correctly MERGE into one offset outline
  3) a shape with a hole (like a letter "O") keeps its interior hole
"""

import math

from PIL import Image, ImageDraw


def star_points(cx, cy, r_outer, r_inner, n_points=4, rot=0.0):
    pts = []
    for i in range(n_points * 2):
        angle = math.pi * i / n_points + rot
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.sin(angle), cy - r * math.cos(angle)))
    return pts


def blob_points(cx, cy, r, n=10, wobble=0.25, seed=0):
    import random

    rnd = random.Random(seed)
    pts = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        rr = r * (1 + rnd.uniform(-wobble, wobble))
        pts.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
    return pts


def make_scene(path, w=900, h=500):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Shape 1: blobby "chess piece"-like silhouette
    d.polygon(blob_points(150, 250, 90, n=12, seed=1), fill=(220, 30, 90, 255))

    # Shape 2 & 3: two blobs placed CLOSE together -> should merge once offset
    d.polygon(blob_points(380, 230, 55, n=10, seed=2), fill=(40, 120, 220, 255))
    d.polygon(blob_points(470, 240, 50, n=10, seed=3), fill=(40, 160, 220, 255))

    # Shape 4: 4-point sparkle star, far away -> stays separate
    star = star_points(700, 200, 110, 40, n_points=4)
    d.polygon(star, fill=(60, 200, 120, 255))
    small_star = star_points(790, 340, 35, 12, n_points=4, rot=0.4)
    d.polygon(small_star, fill=(60, 200, 120, 255))

    # Shape 5: a ring / donut shape to test hole-handling (like letter "O")
    d.ellipse((550, 60, 650, 160), fill=(250, 180, 20, 255))
    d.ellipse((575, 85, 625, 135), fill=(0, 0, 0, 0))  # punch a transparent hole

    img.save(path)


def make_ring(path, w=300, h=300):
    """A separate, clean example: outer disk with a real transparent hole."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    dm = ImageDraw.Draw(mask)
    dm.ellipse((40, 40, 260, 260), fill=255)
    dm.ellipse((100, 100, 200, 200), fill=0)
    color = Image.new("RGBA", (w, h), (250, 180, 20, 255))
    img.paste(color, (0, 0), mask)
    img.save(path)


if __name__ == "__main__":
    make_scene("/root/cutline_studio/samples/scene.png")
    make_ring("/root/cutline_studio/samples/ring.png")
    print("samples written")
