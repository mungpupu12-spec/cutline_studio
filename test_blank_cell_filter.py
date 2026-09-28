"""
Regression test for 2026-09-10(44차) 피드백("아래의 두칸은 왜 인식이 되는거야",
"불필요한 부분이 있어" -- 실제 시트 캡처 두 번으로 재현 확인): 작가 본인이
그려 놓은 진짜 재단선 격자(self._real_grid_cells_px)를 그대로 믿고 쓰는
자동 인식 경로가, 아직 그림을 안 채운 빈 슬롯까지 "도안 하나"로 잘못 처리해
불필요한 칼선을 만들던 문제.

core.multi_design.cell_has_content_px(새 함수)가 실제로 "완전히 빈 칸"과
"진짜 도안이 있는 칸"을 올바르게 구분하는지, 그리고
gui.app._expand_grid_cells_into_sub_elements가 이 함수로 빈 칸을 걸러내는지
확인한다.

작은 합성 이미지(단색 배경 칸 vs 도형이 그려진 칸 -- 실제 도안 아님, 순수
알고리즘 검증용)만 사용.
"""

import ast
import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.multi_design import cell_has_content_px


def _make_sheet_with_one_blank_and_one_filled_cell(path):
    """세로로 나란한 두 칸(각 200x150px)짜리 시트: 위 칸은 완전히 빈 배경,
    아래 칸은 실제 캐릭터를 흉내낸 도형(원+사각형)이 그려져 있음."""
    img = Image.new("RGB", (200, 300), (250, 248, 240))  # 살짝 다른 톤의 배경(빈 슬롯도 완전한 흰색만은 아닐 수 있음)
    d = ImageDraw.Draw(img)
    # 아래 칸(150~300)에만 실제 내용(원+사각형)을 그려 넣음
    d.ellipse((40, 180, 160, 280), fill=(60, 120, 200))
    d.rectangle((70, 160, 130, 190), fill=(200, 60, 60))
    img.save(path)
    return {"blank_cell": (0, 0, 200, 150), "filled_cell": (0, 150, 200, 300)}


def test_blank_cell_detected_as_no_content():
    tmp = os.path.join(tempfile.gettempdir(), "_test_blank_cell_sheet.png")
    cells = _make_sheet_with_one_blank_and_one_filled_cell(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["blank_cell"])
    finally:
        os.remove(tmp)
    print(f"빈 칸 내용 판정: {has_content} (기대: False)")
    assert has_content is False, "완전히 빈 칸인데 '내용 있음'으로 잘못 판정됨"
    print("[OK] 빈 칸은 '내용 없음'으로 정확히 판정됨")


def test_filled_cell_detected_as_has_content():
    tmp = os.path.join(tempfile.gettempdir(), "_test_filled_cell_sheet.png")
    cells = _make_sheet_with_one_blank_and_one_filled_cell(tmp)
    try:
        has_content = cell_has_content_px(tmp, cells["filled_cell"])
    finally:
        os.remove(tmp)
    print(f"도안 있는 칸 내용 판정: {has_content} (기대: True)")
    assert has_content is True, "실제 도안(원+사각형)이 있는 칸인데 '내용 없음'으로 잘못 판정됨"
    print("[OK] 실제 도안이 있는 칸은 '내용 있음'으로 정확히 판정됨")


def test_gui_wiring_filters_blank_cells_using_cell_has_content_px():
    """2026-09-11(51차)로 빈 칸 거르기 책임이 gui.app._expand_grid_cells_
    into_sub_elements(삭제됨, 반복 패널 그룹핑과 충돌해 전체가
    core.multi_design.detect_repeat_aware_sub_element_boxes_px로 대체됨)에서
    그 함수 안으로 옮겨졌다. gui/app.py를 직접 import하지 않고(이 개발
    환경엔 tkinter가 없어 import 자체가 실패함) 소스 코드 정적 분석(ast)만
    으로, 새 함수가 실제로 cell_has_content_px를 호출해 빈 칸을 거르는지
    확인한다."""
    src = open("/root/cutline_studio/core/multi_design.py", encoding="utf-8").read()
    assert "cell_has_content_px" in src, "core/multi_design.py가 cell_has_content_px를 아예 안 씀"
    tree = ast.parse(src)

    def _calls(fn_name):
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        names.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", None))
        return names

    # 2026-09-28: 빈 칸 거르기 + 반복 칸 묶기를 group_content_cells_px로
    # 뽑아냈다(무테 자동 인식도 같은 함수를 씀) -- 직접 호출이든 그 함수를
    # 거치든, 빈 칸 거르기가 실제로 연결돼 있는지 확인.
    repeat_calls = _calls("detect_repeat_aware_sub_element_boxes_px")
    found_call_inside_fn = "cell_has_content_px" in repeat_calls or (
        "group_content_cells_px" in repeat_calls
        and "cell_has_content_px" in _calls("group_content_cells_px")
    )
    assert found_call_inside_fn, (
        "detect_repeat_aware_sub_element_boxes_px 안에서 cell_has_content_px를 호출하지 않음"
    )
    print("[OK] detect_repeat_aware_sub_element_boxes_px가 cell_has_content_px로 빈 칸을 거름(정적 분석 확인)")


def main():
    test_blank_cell_detected_as_no_content()
    test_filled_cell_detected_as_has_content()
    test_gui_wiring_filters_blank_cells_using_cell_has_content_px()
    print("\nAll blank-cell filter checks passed.")


if __name__ == "__main__":
    main()
