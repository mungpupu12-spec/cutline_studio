"""
2026-09-10(48차) 회귀 테스트: "아래의 두칸은 왜 인식이 되는거야" 재발생
피드백 -- 44차 필터(cell_has_content_px)는 칸 안 전체 엣지 비율만 봐서,
시트 전체가 공유하는 배경 무늬(줄무늬, 칸 구분선 등)가 있으면 실제
캐릭터가 없는 "진짜 빈 칸"도 그 무늬 자체의 색 경계 때문에 "내용 있음"으로
잘못 판정되는 한계가 새로 확인됨.

연결 요소 분석으로 "칸을 가로지르는 얇은 띠(줄무늬/구분선)"는 내용 판정에서
제외하도록 수정했다 -- 이 테스트는 그 구분이 실제로 되는지, 순수 합성
이미지(줄무늬 배경 + 실제 캐릭터 도형)로 확인한다.
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.multi_design import cell_has_content_px


def _make_sheet_with_striped_background(path):
    """가로 줄무늬 배경(시트 전체 공통 패턴)을 가진 두 칸짜리 시트: 위 칸은
    줄무늬만 있고 실제 캐릭터 없음(빈 슬롯), 아래 칸은 같은 줄무늬 배경
    위에 실제 캐릭터(원+사각형)가 그려져 있음."""
    img = Image.new("RGB", (300, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 시트 전체를 가로지르는 얇은 줄무늬(각 칸에도 그대로 이어짐)
    for y in range(0, 400, 20):
        d.line([(0, y), (300, y)], fill=(180, 200, 220), width=3)
    # 아래 칸(200~400)에만 실제 캐릭터 그려 넣음
    d.ellipse((60, 260, 240, 380), fill=(60, 120, 200))
    d.rectangle((110, 220, 190, 265), fill=(200, 60, 60))
    img.save(path)
    return {"blank_cell": (0, 0, 300, 200), "filled_cell": (0, 200, 300, 400)}


def test_striped_blank_cell_detected_as_no_content():
    tmp = os.path.join(tempfile.gettempdir(), "_test_striped_blank_sheet.png")
    cells = _make_sheet_with_striped_background(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["blank_cell"])
    finally:
        os.remove(tmp)
    print(f"줄무늬 배경만 있는 빈 칸 내용 판정: {has_content} (기대: False)")
    assert has_content is False, "배경 줄무늬만 있는 빈 칸이 '내용 있음'으로 잘못 판정됨(48차 재발생 사례)"
    print("[OK] 배경 줄무늬만 있는 빈 칸은 '내용 없음'으로 정확히 판정됨")


def test_striped_filled_cell_still_detected_as_has_content():
    tmp = os.path.join(tempfile.gettempdir(), "_test_striped_filled_sheet.png")
    cells = _make_sheet_with_striped_background(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["filled_cell"])
    finally:
        os.remove(tmp)
    print(f"줄무늬 배경 + 실제 캐릭터가 있는 칸 내용 판정: {has_content} (기대: True)")
    assert has_content is True, "같은 배경 줄무늬 위에 실제 캐릭터가 있는 칸을 빈 칸으로 잘못 판정함"
    print("[OK] 배경 줄무늬가 있어도 실제 캐릭터가 있으면 '내용 있음'으로 정확히 판정됨")


def main():
    test_striped_blank_cell_detected_as_no_content()
    test_striped_filled_cell_still_detected_as_has_content()
    print("\nAll background-pattern blank-cell checks passed.")


if __name__ == "__main__":
    main()
