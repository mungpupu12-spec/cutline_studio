"""
Public, fully-reproducible demo of the whole-sheet workflow: check the
vendor guide's slot boundaries FIRST, then place several designs into the
grid and generate cutlines for the whole sheet at once (not just one image).

Uses only synthetic fixtures (samples/guide_demo.pdf, samples/sheet_char_*.png)
so this runs the same for anyone who clones the repo -- no real client/print
files involved. See journal/2026-08-25.md for the same workflow validated
against a real print-vendor guide + real character art (kept private, not
committed, per the project's privacy rule).
"""

import os

from PIL import Image, ImageDraw

from core.cutline_core import generate_cutlines, OffsetSpec
from core.guide import load_guide_from_ai, pt_to_px
from core.sheet_layout import place_designs_into_sheet
from core.preview import render_preview

GUIDE_PATH = "samples/guide_demo.pdf"
CHAR_PATHS = [
    "samples/sheet_char_1.png",
    "samples/sheet_char_2.png",
    "samples/sheet_char_3.png",
    "samples/sheet_char_4.png",
]
DPI = 300.0


def draw_guide_reference(png_path, guide, dpi, out_path):
    img = Image.open(png_path).convert("RGB")
    d = ImageDraw.Draw(img)

    def rect_px(rect_pt):
        return (
            pt_to_px(rect_pt[0], dpi),
            pt_to_px(rect_pt[1], dpi),
            pt_to_px(rect_pt[2], dpi),
            pt_to_px(rect_pt[3], dpi),
        )

    d.rectangle(rect_px(guide.trim_rect_pt), outline=(0, 160, 230), width=2)
    d.rectangle(rect_px(guide.sheet_cut_rect_pt), outline=(230, 0, 120), width=2)
    for slot in guide.slot_rects_pt:
        d.rectangle(rect_px(slot), outline=(150, 150, 150), width=2)
    img.save(out_path)
    return out_path


def main():
    os.makedirs("output", exist_ok=True)
    guide = load_guide_from_ai(GUIDE_PATH)
    print(f"가이드 로드: {len(guide.slot_rects_pt)}칸, 슬롯 크기 {guide.slot_size_mm[0]:.1f}×{guide.slot_size_mm[1]:.1f}mm")

    # --- 1) intentionally-oversized check: confirm the violation path fires ---
    oversized = Image.open(CHAR_PATHS[0]).resize((2000, 2000))
    oversized_path = "output/_oversized_char.png"
    oversized.save(oversized_path)
    _, placements = place_designs_into_sheet(
        guide, [oversized_path], dpi=DPI, out_path="output/_violation_check.png"
    )
    assert placements[0].violation is not None, "violation check should have fired but didn't"
    print("[검증] 슬롯보다 훨씬 큰 도안 -> 위반 감지됨:", placements[0].violation)

    # --- 2) real demo: fit each character to the slot, generate the whole sheet ---
    slot_w_mm, slot_h_mm = guide.slot_size_mm
    fit_size = (slot_w_mm * 0.8, slot_h_mm * 0.8)
    composite_path, placements = place_designs_into_sheet(
        guide,
        CHAR_PATHS,
        dpi=DPI,
        out_path="output/sheet_demo_composite.png",
        target_sizes_mm=[fit_size] * len(CHAR_PATHS),
    )
    violations = [p.violation for p in placements if p.violation]
    assert not violations, f"unexpected violations: {violations}"
    print(f"[검증] {len(CHAR_PATHS)}개 도안 전부 슬롯 안에 배치, 위반 없음")

    result = generate_cutlines(
        image_path=composite_path,
        is_vector=False,
        dpi=DPI,
        offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
    )
    n_shapes = len(result.design.geoms) if result.design.geom_type != "Polygon" else 1
    assert n_shapes == len(CHAR_PATHS), f"expected {len(CHAR_PATHS)} separate regions, got {n_shapes}"
    print(f"[검증] 시트 전체에서 도안 {n_shapes}개 영역 개별 감지 (겹치지 않음)")

    preview_path = "output/sheet_demo_preview.png"
    render_preview(result, preview_path, original_image_path=composite_path, line_width=3)
    final_path = "output/sheet_demo_with_guide.png"
    draw_guide_reference(preview_path, guide, DPI, final_path)
    print(f"wrote {final_path}")


if __name__ == "__main__":
    main()
