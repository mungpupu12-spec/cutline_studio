"""
Quantify + visualize how closely the generated cutline follows the artwork's
true silhouette, comparing the OLD parameters (native-resolution threshold,
simplify_tol_px=1.5, no supersampling) against the NEW ones (midpoint
threshold, supersample=4, simplify_tol_px=0.6).

Two ring samples are used on purpose:
  - samples/ring.png     : PIL's un-antialiased ellipse draw (hard 0/255
                            alpha edge, NO anti-aliasing at all).
  - samples/ring_aa.png  : the same circle drawn at 8x scale and downsampled
                            with a real filter, so its alpha channel has a
                            genuine smooth gradient at the edge -- this is
                            what a real Illustrator/Photoshop PNG export
                            actually looks like.

Both share known analytic geometry (center (150,150), outer radius 110px),
so real deviation from the true circle can be measured, not just eyeballed.

Honest finding from this test: on the hard-edged, non-anti-aliased sample,
supersampling has NO real sub-pixel information to recover (the native
threshold already collapsed the edge to a single pixel-grid decision) and
does not help. On the anti-aliased sample -- representative of real
production art -- the OLD pipeline's low alpha_threshold (20) systematically
counts anti-aliasing halo pixels as foreground, making every offset a bit
*larger* than the true silhouette (mean signed deviation +0.37px on the
cutline); the NEW pipeline's midpoint threshold + supersampling removes that
bias and roughly halves both mean and max deviation.
"""

import math

import numpy as np
from PIL import Image, ImageDraw

from core.cutline_core import (
    OffsetSpec,
    compute_offsets,
    load_raster_design,
    mm_to_px,
)

CENTER = (150.0, 150.0)
OUTER_R = 110.0
DPI = 300.0
OFFSET_MM = 2.0  # single "cut" offset distance used for this check

OLD_KW = dict(alpha_threshold=20, simplify_tol_px=1.5, supersample=1)
NEW_KW = dict(alpha_threshold=127, simplify_tol_px=0.6, supersample=4)


def make_aa_ring(path, w=300, h=300, ss=8):
    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    dm = ImageDraw.Draw(big)
    dm.ellipse((40 * ss, 40 * ss, 260 * ss, 260 * ss), fill=(250, 180, 20, 255))
    dm.ellipse((100 * ss, 100 * ss, 200 * ss, 200 * ss), fill=(0, 0, 0, 0))
    big.resize((w, h), Image.LANCZOS).save(path)


def deviation_stats(mp, center, ideal_r):
    cx, cy = center
    polys = [mp] if mp.geom_type == "Polygon" else list(mp.geoms)
    devs, signed = [], []
    for poly in polys:
        for x, y in poly.exterior.coords:
            r = math.hypot(x - cx, y - cy)
            devs.append(abs(r - ideal_r))
            signed.append(r - ideal_r)
    devs = np.array(devs)
    signed = np.array(signed)
    return devs.mean(), devs.max(), signed.mean(), len(devs)


def run(ring_path, label, **raster_kwargs):
    design, w, h = load_raster_design(ring_path, **raster_kwargs)
    offsets = compute_offsets(design, DPI, OffsetSpec(cut_mm=OFFSET_MM))
    ideal_r = OUTER_R + mm_to_px(OFFSET_MM, DPI)
    mean_dev, max_dev, mean_signed, n_pts = deviation_stats(offsets["cut"], CENTER, ideal_r)
    print(
        f"[{label:28s}] n={n_pts:5d}  mean|dev|={mean_dev:.3f}px  "
        f"max|dev|={max_dev:.3f}px  mean signed={mean_signed:+.3f}px"
    )
    return design, offsets


def zoomed_crop_overlay(design_old, off_old, design_new, off_new, out_path):
    def draw_panel(design, geom, title):
        crop = (150, 20, 290, 160)  # upper-right quarter arc: diagonal, not axis-aligned
        zoom = 4
        panel_w = (crop[2] - crop[0]) * zoom
        panel_h = (crop[3] - crop[1]) * zoom + 30
        img = Image.new("RGB", (panel_w, panel_h), (255, 255, 255))
        d = ImageDraw.Draw(img)

        def to_panel(x, y):
            return ((x - crop[0]) * zoom, (y - crop[1]) * zoom + 30)

        polys = [design] if design.geom_type == "Polygon" else list(design.geoms)
        for poly in polys:
            pts = [to_panel(x, y) for x, y in poly.exterior.coords]
            d.line(pts, fill=(210, 210, 210), width=2, joint="curve")

        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        for poly in polys:
            pts = [to_panel(x, y) for x, y in poly.exterior.coords]
            d.line(pts, fill=(255, 30, 30), width=2, joint="curve")
            for x, y in pts:
                d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(120, 0, 0))
        d.text((5, 5), title, fill=(0, 0, 0))
        return img

    img_old = draw_panel(design_old, off_old["cut"], "BEFORE (threshold 20, tol=1.5px)")
    img_new = draw_panel(design_new, off_new["cut"], "AFTER (midpoint + supersample x4)")

    gap = 20
    combined = Image.new(
        "RGB", (img_old.width + img_new.width + gap, max(img_old.height, img_new.height)), (255, 255, 255)
    )
    combined.paste(img_old, (0, 0))
    combined.paste(img_new, (img_old.width + gap, 0))
    combined.save(out_path)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    make_aa_ring("samples/ring_aa.png")

    print("--- hard-edged source (no anti-aliasing -- worst case for this technique) ---")
    run("samples/ring.png", "BEFORE (hard edge)", **OLD_KW)
    run("samples/ring.png", "AFTER (hard edge)", **NEW_KW)

    print()
    print("--- anti-aliased source (representative of real exported artwork) ---")
    d_old, o_old = run("samples/ring_aa.png", "BEFORE (anti-aliased)", **OLD_KW)
    d_new, o_new = run("samples/ring_aa.png", "AFTER (anti-aliased)", **NEW_KW)

    zoomed_crop_overlay(d_old, o_old, d_new, o_new, "output/fidelity_before_after.png")
