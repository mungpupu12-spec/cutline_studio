"""
Logic/mechanics smoke test for the new "trace once, replicate to repeats"
pipeline pieces -- NOT a cutline-quality test on real art. Uses small
script-generated images with KNOWN, analytic content (same spirit as
test_fidelity.py/test_multi_design.py) purely to verify:

  1. core.multi_design.group_identical_boxes_px correctly groups boxes that
     are literal pixel-identical repeats of the same stamped design, and
     correctly keeps a same-content-but-different-size box OUT of the group
     (safety: never replicate a traced cutline onto a differently-sized box).
  2. core.repeat_grid.translate_cutline_result correctly translates every
     part of a CutlineResult (design + every offset tier) by a known (dx,
     dy), leaving dpi/width_px/height_px/offset_mm untouched and appending
     a human-readable note to adjustments without discarding any existing
     note.

2026-09-07(6차) 피드백("반복되는 스티커는 한개의 칼선을 먼저 완성하고
나머지에 그대로 적용해") 대응 기능의 알고리즘 정확성 확인용.
"""

import sys
sys.path.insert(0, "/root/cutline_studio")

from PIL import Image, ImageDraw
from shapely.geometry import MultiPolygon, Polygon

from core.cutline_core import CutlineResult, OffsetSpec
from core.multi_design import group_identical_boxes_px
from core.repeat_grid import translate_cutline_result


def _make_stamp_sheet(path):
    """A 900x300 sheet with a 60x60 red-square 'design' stamped identically
    at 3 different locations, one visibly DIFFERENT (blue) design, and one
    more copy of the SAME red-square content but stretched to a different
    size (80x60) -- the size-mismatch safety guard must keep that one out
    of the group even though its pixel content (once resized) looks similar."""
    img = Image.new("RGB", (900, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 3 identical 60x60 red stamps, each with the same internal detail
    stamp_boxes = [(30, 30), (200, 30), (30, 150)]
    for (x, y) in stamp_boxes:
        d.rectangle([x, y, x + 60, y + 60], fill=(220, 40, 40))
        d.ellipse([x + 15, y + 15, x + 45, y + 45], fill=(255, 255, 255))
    # a genuinely different (blue) design, same size
    d.rectangle([370, 30, 430, 90], fill=(40, 60, 220))
    d.polygon([(400, 35), (425, 85), (375, 85)], fill=(255, 255, 0))
    # same red-stamp content, but stretched to a different size (80 wide)
    d.rectangle([540, 30, 620, 90], fill=(220, 40, 40))
    d.ellipse([555, 45, 605, 75], fill=(255, 255, 255))
    img.save(path)
    return {
        "identical": [(30, 30, 90, 90), (200, 30, 260, 90), (30, 150, 90, 210)],
        "different_content": (370, 30, 430, 90),
        "different_size": (540, 30, 620, 90),
    }


def test_identical_repeats_are_grouped_together():
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "_test_repeat_grid_sheet.png")
    boxes_info = _make_stamp_sheet(tmp)
    all_boxes = (
        boxes_info["identical"]
        + [boxes_info["different_content"], boxes_info["different_size"]]
    )
    try:
        groups = group_identical_boxes_px(tmp, all_boxes)
    finally:
        os.remove(tmp)

    # the 3 identical stamps (indices 0,1,2) must all land in ONE group
    identical_group = next(g for g in groups if 0 in g)
    assert sorted(identical_group) == [0, 1, 2], f"expected [0,1,2] grouped together, got {identical_group}"
    print("[OK] 3 pixel-identical repeated stamps grouped into one group:", identical_group)

    # the genuinely different design (index 3) must be its own group
    different_group = next(g for g in groups if 3 in g)
    assert different_group == [3], f"different-content box should be alone, got {different_group}"
    print("[OK] visibly different design kept in its own group")

    # the same-content-but-different-size box (index 4) must NOT be merged
    # into the identical group, despite similar content
    size_mismatch_group = next(g for g in groups if 4 in g)
    assert size_mismatch_group == [4], f"different-size box should not be merged, got {size_mismatch_group}"
    print("[OK] same-content-but-different-size box correctly excluded from the group (safety)")


def test_translate_cutline_result_shifts_every_tier():
    design = MultiPolygon([Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])])
    cut = MultiPolygon([Polygon([(-2, -2), (12, -2), (12, 12), (-2, 12)])])
    bleed = MultiPolygon([Polygon([(-4, -4), (14, -4), (14, 14), (-4, 14)])])
    original = CutlineResult(
        dpi=300.0,
        width_px=1000,
        height_px=800,
        design=design,
        offsets={"cut": cut, "bleed": bleed},
        offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
        adjustments=["기존 노트: 실루엣이 작아 무테로 대체됨"],
    )

    moved = translate_cutline_result(original, dx=100.0, dy=-50.0, note="복제됨")

    # metadata untouched
    assert moved.dpi == 300.0 and moved.width_px == 1000 and moved.height_px == 800
    assert moved.offset_mm is original.offset_mm

    # geometry shifted by exactly (dx, dy)
    assert moved.design.bounds == (100.0, -50.0, 110.0, -40.0), moved.design.bounds
    assert moved.offsets["cut"].bounds == (98.0, -52.0, 112.0, -38.0), moved.offsets["cut"].bounds
    assert moved.offsets["bleed"].bounds == (96.0, -54.0, 114.0, -36.0), moved.offsets["bleed"].bounds

    # original untouched (translate must not mutate in place)
    assert original.design.bounds == (0.0, 0.0, 10.0, 10.0)

    # existing note preserved, new note appended (not replaced)
    assert moved.adjustments == ["기존 노트: 실루엣이 작아 무테로 대체됨", "복제됨"]

    print("[OK] translate_cutline_result shifts design + every offset tier by exactly (dx, dy)")
    print("[OK] original CutlineResult left untouched (no in-place mutation)")
    print("[OK] existing adjustments note preserved, new note appended")


def main():
    test_identical_repeats_are_grouped_together()
    test_translate_cutline_result_shifts_every_tier()
    print("\nAll repeat-grid wiring checks passed.")


if __name__ == "__main__":
    main()
