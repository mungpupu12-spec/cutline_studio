"""
Algorithm-correctness + speed check for core.segmentation.segment_design_in_
region's `max_grabcut_dim` optimization (2026-09-07(6차) 피드백: "자동인식
처리 속도가 너무 느려... 최소 5초 안에는 완성되어야"). Profiling showed
cv2.grabCut itself was over 80% of this function's total time, and its cost
scales with the pixel count of the crop it's given -- but the FINAL cutline's
precision actually comes from a separate supersample-upscale step that
always runs at the crop's real resolution, so grabCut itself doesn't need to
run at full resolution. The fix: run grabCut on a downscaled copy of the
crop when it's larger than `max_grabcut_dim`, then scale its binary mask
back up before the existing (unchanged) morphology/supersample pipeline.

This uses a single known-radius circle on a colored background -- pure
analytic geometry, same spirit as test_fidelity.py's known-radius ring --
purely to verify the downsizing optimization doesn't meaningfully change the
traced silhouette. NOT a real-art cutline-quality judgment.
"""

import math
import time

import sys
sys.path.insert(0, "/root/cutline_studio")

from PIL import Image, ImageDraw

from core.segmentation import segment_design_in_region


def _make_known_circle(path, W=900, H=900, cx=450, cy=450, R=250):
    img = Image.new("RGB", (W, H), (40, 140, 140))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(230, 80, 40))
    img.save(path)
    return (cx - R - 10, cy - R - 10, cx + R + 10, cy + R + 10), math.pi * R * R


def test_downsized_grabcut_stays_close_to_full_resolution():
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "_test_seg_speed_circle.png")
    rect, expected_area = _make_known_circle(tmp)
    try:
        t0 = time.time()
        fast = segment_design_in_region(tmp, rect, supersample=4, max_grabcut_dim=420)
        t_fast = time.time() - t0

        t0 = time.time()
        full = segment_design_in_region(tmp, rect, supersample=4, max_grabcut_dim=100_000)
        t_full = time.time() - t0
    finally:
        os.remove(tmp)

    err_fast = abs(fast.area - expected_area) / expected_area
    err_full = abs(full.area - expected_area) / expected_area
    print(f"expected area={expected_area:.0f}  fast_area={fast.area:.0f} (err {err_fast*100:.2f}%)"
          f"  full_area={full.area:.0f} (err {err_full*100:.2f}%)")
    assert err_fast < 0.03, f"downsized-grabcut area error too large: {err_fast*100:.2f}%"
    print("[OK] downsized grabCut area stays within 3% of the known analytic circle area")

    inter = fast.intersection(full).area
    union = fast.union(full).area
    iou = inter / union
    print(f"IoU(downsized, full-resolution) = {iou:.4f}")
    assert iou > 0.97, f"downsized-grabcut shape diverges too much from full-resolution (IoU={iou:.4f})"
    print("[OK] downsized grabCut traces essentially the same shape as full-resolution (IoU > 0.97)")

    print(f"timing: downsized={t_fast:.3f}s  full-resolution={t_full:.3f}s")
    assert t_fast <= t_full, "downsizing should never be slower than running at full resolution"
    print("[OK] downsized grabCut is not slower than full-resolution")


def main():
    test_downsized_grabcut_stays_close_to_full_resolution()
    print("\nAll segmentation-speed checks passed.")


if __name__ == "__main__":
    main()
