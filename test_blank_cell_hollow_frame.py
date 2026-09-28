"""
2026-09-10(49차) 회귀 테스트: "두번째/세번째 아래 공백에 칼선 생김" 재발
피드백 -- 48차(배경 줄무늬 제외) 수정 후에도, "아직 안 채운 자리"임을
표시하려고 테두리/프레임만 얇게 그려 둔 빈 슬롯은 여전히 "내용 있음"으로
잘못 판정되고 있었다. `_content_mask_from_gray`의 hole-fill이 닫힌 테두리의
안쪽을 캐릭터든 빈 테두리든 구분 없이 채우기 때문 -- 처음 시도한 erode(안쪽
깎아내기) 방식은 이미 꽉 채워진 덩어리라 거의 안 줄어들어 효과가 없었고,
최종적으로 "그 덩어리의 원래 색이 배경색과 같은가"로 판단하도록 바꿨다
(빈 테두리 안쪽은 배경과 색이 같고, 실제 캐릭터는 배경과 색이 다름).

전부 합성 이미지만 사용(순수 로직 검증).
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.multi_design import cell_has_content_px, detect_design_bboxes_px


def _make_sheet_with_placeholder_frame_and_real_character(path):
    """위 칸: 배경과 같은 흰 바탕에 "자리 표시용" 테두리만 얇게 그려짐(안은
    완전히 빈 채 그대로). 아래 칸: 실제 캐릭터(테두리+안쪽 채색)."""
    img = Image.new("RGB", (300, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 290, 190), outline=(200, 150, 150), width=3)
    d.ellipse((60, 260, 240, 380), fill=(60, 120, 200), outline=(200, 60, 60), width=3)
    img.save(path)
    return {"placeholder_cell": (0, 0, 300, 200), "filled_cell": (0, 200, 300, 400)}


def test_placeholder_frame_detected_as_no_content():
    tmp = os.path.join(tempfile.gettempdir(), "_test_placeholder_frame_sheet.png")
    cells = _make_sheet_with_placeholder_frame_and_real_character(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["placeholder_cell"])
    finally:
        os.remove(tmp)
    print(f"자리 표시용 빈 테두리 칸 내용 판정: {has_content} (기대: False)")
    assert has_content is False, "안이 완전히 빈 테두리(자리 표시용)가 '내용 있음'으로 잘못 판정됨(49차 재발 사례)"
    print("[OK] 자리 표시용 빈 테두리 칸은 '내용 없음'으로 정확히 판정됨")


def test_real_character_with_outline_still_detected():
    tmp = os.path.join(tempfile.gettempdir(), "_test_placeholder_frame_sheet2.png")
    cells = _make_sheet_with_placeholder_frame_and_real_character(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["filled_cell"])
    finally:
        os.remove(tmp)
    print(f"테두리+채색된 실제 캐릭터 칸 내용 판정: {has_content} (기대: True)")
    assert has_content is True, "테두리 안쪽이 실제로 채색된 캐릭터를 '내용 없음'으로 잘못 판정함"
    print("[OK] 테두리 안쪽이 실제로 채색된 캐릭터는 '내용 있음'으로 정확히 판정됨")


def test_detect_design_bboxes_skips_placeholder_frame():
    """실제 재단선 격자가 없는 파일(detect_design_bboxes_px 대체 경로)에서도
    같은 빈 테두리가 박스로 잘못 잡히지 않는지 확인 -- cell_has_content_px가
    아예 호출되지 않는 경로라 별도로 확인 필요."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_placeholder_frame_sheet3.png")
    _make_sheet_with_placeholder_frame_and_real_character(tmp)
    try:
        boxes = detect_design_bboxes_px(tmp)
    finally:
        os.remove(tmp)
    print(f"detect_design_bboxes_px가 찾은 박스 개수: {len(boxes)} (기대: 1 -- 실제 캐릭터만)")
    assert len(boxes) == 1, "빈 테두리(자리 표시용)가 detect_design_bboxes_px에서도 박스로 잘못 잡힘"
    x0, y0, x1, y1 = boxes[0]
    assert y0 >= 190, f"찾은 유일한 박스는 아래쪽 실제 캐릭터 칸이어야 하는데 {boxes[0]}"
    print("[OK] detect_design_bboxes_px도 빈 테두리를 박스로 잡지 않고 실제 캐릭터만 찾음")


def main():
    test_placeholder_frame_detected_as_no_content()
    test_real_character_with_outline_still_detected()
    test_detect_design_bboxes_skips_placeholder_frame()
    print("\nAll hollow-frame blank-cell checks passed.")


if __name__ == "__main__":
    main()
