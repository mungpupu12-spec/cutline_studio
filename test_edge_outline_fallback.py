"""
Algorithm-correctness check for core.segmentation._edge_outline_mask and its
wiring into segment_design_in_region (2026-09-07(7차) 피드백: "요소 외곽에
라인이 들어가면 그 라인을 기준으로 칼선을 만들면 돼" -- after a real batch
run hit "GrabCut found no foreground inside the selection" on one design).

GrabCut needs a real COLOR difference between foreground and background to
work at all; it can fail outright when a design's fill color happens to
match its background (only a drawn outline stroke marks the real boundary).
_edge_outline_mask is the fallback for exactly that case: it finds the
design by its drawn line (Canny edge + gap-closing + hole-fill) instead of
by color, and segment_design_in_region now tries it automatically whenever
GrabCut's own mask comes back completely empty.

Uses a single known-radius circle drawn as an OUTLINE ONLY (fill == the
surrounding background color, so GrabCut has no color signal at all) --
pure analytic geometry, same spirit as test_fidelity.py's known-radius ring.
NOT a real-art cutline-quality judgment.
"""

import math
import os
import tempfile

import sys
sys.path.insert(0, "/root/cutline_studio")

import numpy as np
import cv2
from PIL import Image, ImageDraw

from core.segmentation import _edge_outline_mask, segment_design_in_region


def _outline_only_circle_crop(cx=250, cy=250, R=120, W=500, H=500, bg=(235, 235, 235)):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=bg, outline=(20, 20, 20), width=6)
    crop = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return crop, math.pi * R * R


def test_edge_outline_mask_recovers_known_circle_from_its_drawn_line():
    crop, expected_area = _outline_only_circle_crop()
    inner = (250 - 120 - 15, 250 - 120 - 15, 2 * (120 + 15), 2 * (120 + 15))
    mask = _edge_outline_mask(crop, inner)
    assert mask is not None, "expected a mask to be found from the drawn outline"
    area = int((mask > 0).sum())
    err = abs(area - expected_area) / expected_area
    print(f"expected~{expected_area:.0f} got={area} err={err*100:.1f}%")
    assert err < 0.10, f"edge-outline mask area error too large: {err*100:.1f}%"
    print("[OK] edge-outline fallback recovers the known circle's area within 10%")


def test_edge_outline_mask_returns_none_on_a_blank_crop():
    blank = np.full((300, 300, 3), 235, np.uint8)
    mask = _edge_outline_mask(blank, (50, 50, 200, 200))
    assert mask is None, "a blank crop with no lines at all must not fabricate a silhouette"
    print("[OK] a blank crop (no drawn lines anywhere) correctly returns None, not a fake shape")


def test_segment_design_in_region_falls_back_to_outline_when_grabcut_is_empty():
    """End-to-end: segment_design_in_region itself (not the helper directly)
    must still succeed and use the outline fallback when GrabCut's own
    color-based pass finds nothing."""
    crop, expected_area = _outline_only_circle_crop()
    tmp = os.path.join(tempfile.gettempdir(), "_test_edge_fallback_circle.png")
    cv2.imwrite(tmp, crop)
    rect = (250 - 120 - 15, 250 - 120 - 15, 250 + 120 + 15, 250 + 120 + 15)
    note_sink = []
    try:
        design = segment_design_in_region(tmp, rect, supersample=4, note_sink=note_sink)
    finally:
        os.remove(tmp)

    err = abs(design.area - expected_area) / expected_area
    print(f"end-to-end area={design.area:.0f} expected~{expected_area:.0f} err={err*100:.1f}% notes={note_sink}")
    assert err < 0.15, f"end-to-end fallback area error too large: {err*100:.1f}%"
    assert any("외곽선" in n for n in note_sink), "expected a note explaining the outline fallback was used"
    print("[OK] segment_design_in_region automatically falls back to the outline and reports it via note_sink")


def main():
    test_edge_outline_mask_recovers_known_circle_from_its_drawn_line()
    test_edge_outline_mask_returns_none_on_a_blank_crop()
    test_segment_design_in_region_falls_back_to_outline_when_grabcut_is_empty()
    print("\nAll edge-outline fallback checks passed.")


if __name__ == "__main__":
    main()
