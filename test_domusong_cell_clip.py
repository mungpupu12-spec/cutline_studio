"""
Regression test for 2026-09-07(7차) 피드백("도무송은 모두 이미지 안쪽으로
칼선이 들어가야해"): a DOMUSONG (or FULL_CUT) primitive shape generated for
one grid cell must never bleed past that cell's own bounds into a
neighboring cell -- even though `core.interactive_cutline.
generate_cutline_for_selection` already supported a `bounds_px` clip
parameter, nothing in gui/app.py was ever passing it for the auto-detect
batch path, so a DOMUSONG circle sized to a wide/short design's content
could freely spill into whatever was next to it on the sheet.

Uses a small synthetic two-cell sheet (a wide flat rectangle "design" next
to a neighboring cell) -- pure algorithm-correctness/wiring check, not a
real-art quality judgment.
"""

import os
import sys

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.cutline_core import OffsetSpec
from core.cutline_types import CutlineType
from core.interactive_cutline import generate_cutline_for_selection


def _make_two_cell_sheet(path):
    img = Image.new("RGB", (600, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 100, 280, 200], fill=(200, 60, 60))  # wide, short design -- cell 0
    d.rectangle([320, 100, 580, 200], fill=(60, 120, 200))  # neighboring cell 1
    img.save(path)


def test_unclipped_domusong_circle_overflows_its_own_cell():
    """Establishes the bug: without bounds_px, a CIRCLE sized to a wide/
    short design's content overflows its own cell significantly."""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_test_domusong_clip_sheet.png")
    _make_two_cell_sheet(tmp)
    try:
        result = generate_cutline_for_selection(
            image_path=tmp,
            selection_px=(20, 100, 280, 200),
            cutline_type=CutlineType.CIRCLE,
            dpi=300.0,
            offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
            use_grabcut=True,
            bounds_px=None,
        )
    finally:
        os.remove(tmp)
    _minx, _miny, maxx, _maxy = result.offsets["bleed"].bounds
    assert maxx > 280, f"expected the unclipped case to actually overflow past x=280, got maxx={maxx}"
    print(f"[OK] confirmed baseline: unclipped DOMUSONG circle overflows its cell (bleed maxx={maxx:.1f} > 280)")


def test_bounds_px_clips_domusong_circle_to_its_own_cell():
    """The actual fix: passing bounds_px=the cell itself must clamp every
    offset tier to exactly that cell, regardless of how much the raw
    primitive shape would otherwise want to overflow."""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_test_domusong_clip_sheet2.png")
    _make_two_cell_sheet(tmp)
    cell = (20, 100, 280, 200)
    try:
        result = generate_cutline_for_selection(
            image_path=tmp,
            selection_px=cell,
            cutline_type=CutlineType.CIRCLE,
            dpi=300.0,
            offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
            use_grabcut=True,
            bounds_px=cell,
        )
    finally:
        os.remove(tmp)

    x0, y0, x1, y1 = cell
    for name, mp in result.offsets.items():
        minx, miny, maxx, maxy = mp.bounds
        print(f"{name}: bounds=({minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f})  cell={cell}")
        assert minx >= x0 - 0.5, f"{name} tier crosses the cell's left edge"
        assert maxx <= x1 + 0.5, f"{name} tier crosses the cell's right edge into the neighbor"
        assert miny >= y0 - 0.5, f"{name} tier crosses the cell's top edge"
        assert maxy <= y1 + 0.5, f"{name} tier crosses the cell's bottom edge"
    print("[OK] bounds_px=cell clamps every offset tier (safety/cut/bleed) to exactly that cell")


def main():
    test_unclipped_domusong_circle_overflows_its_own_cell()
    test_bounds_px_clips_domusong_circle_to_its_own_cell()
    print("\nAll DOMUSONG cell-clip checks passed.")


if __name__ == "__main__":
    main()
