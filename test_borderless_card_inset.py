"""
2026-09-28 무테 규칙("무테는 이미지 안쪽에 칼선 -- 배경이미지를 같이 자르면
상품 가치가 없어", 비교 이미지로 "칸 전체 한 장" 확정)과, 칸 사이 얇은 배경
틈이 요소로 잡히던 문제에 대한 알고리즘 정확성 테스트. 실제 파일이 아닌
알려진 기하 도형/합성 이미지만 쓴다(품질 판단이 아니라 규칙 준수만 확인).
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image
from shapely.geometry import box

from core.cutline_core import mm_to_px
from core.interactive_cutline import generate_card_inset_cutline
from core.multi_design import _is_uncuttable_sliver_box


def test_thin_gap_between_cells_is_not_an_element():
    """칸(945x888) 경계의 폭 7px x 높이 745px 틈은 요소가 아니다."""
    assert _is_uncuttable_sliver_box((3546, 100, 3553, 845), (3546, 0, 4491, 888))


def test_normal_and_ribbon_like_elements_are_kept():
    cell = (0, 0, 945, 888)
    assert not _is_uncuttable_sliver_box((100, 100, 160, 160), cell)   # 작은 정사각 요소
    assert not _is_uncuttable_sliver_box((100, 100, 130, 400), cell)   # 폭 30px 리본(충분히 두꺼움)
    assert not _is_uncuttable_sliver_box((100, 100, 110, 120), cell)   # 작은 점(가로세로비 조건 불충족)


def _blank_image(w=1000, h=800):
    path = os.path.join(tempfile.gettempdir(), "_test_card_inset.png")
    Image.new("RGB", (w, h), (200, 220, 240)).save(path)
    return path


def test_card_inset_is_exactly_margin_inside_the_card():
    path = _blank_image()
    card = (100, 50, 900, 750)
    dpi = 300.0
    try:
        res = generate_card_inset_cutline(path, card, dpi, margin_mm=1.2)
    finally:
        os.remove(path)
    inset = mm_to_px(1.2, dpi)
    minx, miny, maxx, maxy = res.offsets["cut"].bounds
    assert abs(minx - (100 + inset)) < 0.01 and abs(maxx - (900 - inset)) < 0.01
    assert abs(miny - (50 + inset)) < 0.01 and abs(maxy - (750 - inset)) < 0.01
    # 칸 밖(옆 칸 틈/배경)으로 한 점도 안 나감
    assert res.offsets["cut"].difference(box(*card)).area == 0
    # 모서리는 칸처럼 각짐(사각형 4꼭짓점)
    poly = res.offsets["cut"].geoms[0]
    assert len(poly.exterior.coords) == 5


def test_card_inset_margin_floor():
    path = _blank_image()
    try:
        res = generate_card_inset_cutline(path, (100, 50, 900, 750), 300.0, margin_mm=0.1)
    finally:
        os.remove(path)
    assert res.offset_mm.cut_mm == 0.5
    assert any("최소값" in n for n in res.adjustments)


def main():
    test_thin_gap_between_cells_is_not_an_element()
    test_normal_and_ribbon_like_elements_are_kept()
    test_card_inset_is_exactly_margin_inside_the_card()
    test_card_inset_margin_floor()
    print("All borderless card-inset checks passed.")


if __name__ == "__main__":
    main()
