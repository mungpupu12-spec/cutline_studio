"""
Regression test for 2026-09-10(40차) 피드백("도무송 칼선은 다른 칼선보다
중심 기준을 두고 최소 15mm는 외부에서 안 쪽으로 들어와야해. 저렇게 얇게
칼선이 들어가면 무조건 파손돼"): 도무송(CIRCLE/ELLIPSE/SQUARE/RECTANGLE)은
실제 금형이 찍어내는 도형이라, 완칼보다 훨씬 큰 최소 여유(15mm,
core.cutline_core.MIN_DOMUSONG_GAP_MM)가 필요하다는 실무 기준을
core.interactive_cutline.generate_cutline_for_selection이 실제로 적용하는지
확인한다.

작게 입력한 safety/cut/bleed 값(1/2/3mm -- 15mm보다 훨씬 좁음)을 그대로
써도, 도무송이면 가장 안쪽 선(safety)이 실제로 15mm까지 밀려나야 하고,
완칼(FULL_CUT)은 기존 동작 그대로(2mm, MIN_GAP_MM) 유지돼야 한다(회귀 없음).

작은 스크립트 생성 이미지(알려진 사각형 하나)로 순수 알고리즘 동작만
검증 -- 실제 도안 파일로 칼선 품질을 판단하는 용도가 아님.
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.cutline_core import MIN_DOMUSONG_GAP_MM, MIN_GAP_MM, OffsetSpec, mm_to_px, px_to_mm
from core.cutline_types import CutlineType
from core.interactive_cutline import generate_cutline_for_selection


def _make_single_design_sheet(path):
    # 캔버스를 도안보다 충분히 크게 잡아야 한다 -- 15mm(300dpi 기준 약
    # 177px) 여유를 실제로 측정하려면, compute_offsets의 bounds_px 클립
    # (기본값: 이미지 전체 크기)이 그 여유보다 먼저 걸려서 결과를 잘라내지
    # 않을 만큼 이미지 가장자리까지 충분한 여백이 있어야 함(가장자리까지
    # 200px밖에 없으면 177px 요구를 200px로 착각해 통과해버릴 수 있어
    # 넉넉하게 400px 여백을 둠).
    img = Image.new("RGB", (1200, 1200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([400, 400, 800, 800], fill=(60, 120, 200))
    img.save(path)


def _innermost_gap_mm(result, design_bounds_px, dpi):
    """가장 안쪽 offset(보통 safety)이 design_bounds_px에서 실제로 얼마나
    떨어져 있는지(mm)를, 네 방향 중 가장 좁은 쪽 기준으로 측정."""
    x0, y0, x1, y1 = design_bounds_px
    gaps_px = []
    for mp in result.offsets.values():
        mx0, my0, mx1, my1 = mp.bounds
        gaps_px.extend([x0 - mx0, y0 - my0, mx1 - x1, my1 - y1])
    return px_to_mm(min(gaps_px), dpi)


def test_domusong_gets_15mm_floor_even_with_tiny_input():
    tmp = os.path.join(tempfile.gettempdir(), "_test_domusong_min_gap_sheet.png")
    _make_single_design_sheet(tmp)
    dpi = 300.0
    try:
        result = generate_cutline_for_selection(
            image_path=tmp,
            selection_px=(400, 400, 800, 800),
            cutline_type=CutlineType.SQUARE,
            dpi=dpi,
            offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
            use_grabcut=True,
        )
    finally:
        os.remove(tmp)

    gap_mm = _innermost_gap_mm(result, (400, 400, 800, 800), dpi)
    print(f"도무송(SQUARE) 안쪽 여유: {gap_mm:.2f}mm (기준: {MIN_DOMUSONG_GAP_MM:g}mm)")
    assert gap_mm >= MIN_DOMUSONG_GAP_MM - 0.05, (
        f"도무송은 최소 {MIN_DOMUSONG_GAP_MM:g}mm 여유가 있어야 하는데 {gap_mm:.2f}mm 뿐임"
    )
    print("[OK] 도무송: 작게 입력해도 최소 15mm 여유까지 자동으로 밀려남")


def test_full_cut_still_uses_the_smaller_2mm_floor():
    """완칼은 이번 변경 대상이 아니므로, 기존 MIN_GAP_MM(2.0mm)만 보장되고
    15mm까지 밀리지 않아야 한다(회귀 없음)."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_full_cut_min_gap_sheet.png")
    _make_single_design_sheet(tmp)
    dpi = 300.0
    try:
        result = generate_cutline_for_selection(
            image_path=tmp,
            selection_px=(400, 400, 800, 800),
            cutline_type=CutlineType.FULL_CUT,
            dpi=dpi,
            offset_mm=OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.0),
            use_grabcut=True,
        )
    finally:
        os.remove(tmp)

    gap_mm = _innermost_gap_mm(result, (400, 400, 800, 800), dpi)
    print(f"완칼(FULL_CUT) 안쪽 여유: {gap_mm:.2f}mm (기준: {MIN_GAP_MM:g}mm, 도무송 기준 아님)")
    assert gap_mm >= MIN_GAP_MM - 0.05, f"완칼은 최소 {MIN_GAP_MM:g}mm는 보장돼야 하는데 {gap_mm:.2f}mm 뿐임"
    assert gap_mm < MIN_DOMUSONG_GAP_MM - 1.0, (
        "완칼까지 도무송의 15mm 기준을 적용해버리면 회귀 -- 완칼은 그대로 2mm 근처여야 함"
    )
    print("[OK] 완칼: 도무송 전용 15mm 기준의 영향을 받지 않고 기존 2mm 기준 그대로 유지됨(회귀 없음)")


def main():
    test_domusong_gets_15mm_floor_even_with_tiny_input()
    test_full_cut_still_uses_the_smaller_2mm_floor()
    print("\nAll DOMUSONG min-gap checks passed.")


if __name__ == "__main__":
    main()
