"""
2026-09-10(46차) 회귀 테스트: "도무송 칼선이 자꾸 사라져... 안쪽에 생성 안
됬어" 피드백의 근본 원인 -- 도무송은 (a) 옆 칸을 침범하면 안 된다(7차)와
(b) 도안에서 최소 15mm는 떨어져야 한다(40차, MIN_DOMUSONG_GAP_MM)는 두
규칙을 동시에 지켜야 하는데, 지금까지는 (a)를 "지금 칸 자체"로 구현해서
칸들이 서로 붙어 있는(실제 스티커 시트에서 흔한) 경우 (b)가 만들려는 여유가
그 자리에서 바로 잘려나가 칼선이 도안 경계와 겹쳐 사라져 보였다.

core.multi_design.expand_box_to_neighbor_midpoint_px가 "지금 칸"을 실제
이웃까지의 중간 지점으로 넓혀, 이웃을 침범하지 않으면서도 여유 공간을
확보하는지 순수 기하 계산으로 확인한다. (실제 그림 없이 좌표만으로 검증
가능한 알고리즘이라 합성 좌표만 사용.)
"""

import os
import sys

sys.path.insert(0, "/root/cutline_studio")

from core.multi_design import expand_box_to_neighbor_midpoint_px


def test_expands_to_midpoint_when_neighbor_on_right():
    box = (100, 0, 200, 100)
    neighbor = (220, 0, 320, 100)  # 오른쪽에 20px 간격을 두고 붙어있음
    result = expand_box_to_neighbor_midpoint_px(box, [neighbor], (1000, 1000))
    x0, y0, x1, y1 = result
    assert x1 == 210, f"오른쪽 중간 지점(210)이어야 하는데 {x1}"
    assert x0 == 0 and y0 == 0 and y1 == 1000, "이웃 없는 방향은 시트 가장자리까지 넓어져야 함"
    print("[OK] 오른쪽 이웃까지의 중간 지점으로 정확히 넓어짐")


def test_touching_neighbor_gives_no_expansion():
    box = (100, 0, 200, 100)
    neighbor = (200, 0, 300, 100)  # 완전히 맞닿음(간격 0)
    result = expand_box_to_neighbor_midpoint_px(box, [neighbor], (1000, 1000))
    x0, y0, x1, y1 = result
    assert x1 == 200, "맞닿은 이웃이면 중간 지점도 원래 경계와 같아야 함(넘어가면 침범)"
    print("[OK] 완전히 맞닿은 이웃이 있으면 기존과 동일하게 침범 없이 그대로 유지됨")


def test_no_neighbors_expands_to_full_sheet():
    box = (400, 300, 500, 400)
    result = expand_box_to_neighbor_midpoint_px(box, [], (1000, 1000))
    assert result == (0.0, 0.0, 1000.0, 1000.0)
    print("[OK] 이웃이 전혀 없으면 시트 전체 가장자리까지 자유롭게 넓어짐")


def test_never_shrinks_below_original_box():
    # 방어적 안전장치: 잘못된 좌표가 섞여도 원래 칸보다 좁아지진 않아야 함.
    box = (100, 100, 200, 200)
    weird_neighbor = (150, 150, 160, 160)  # box 안에 완전히 들어있는 비정상 값
    result = expand_box_to_neighbor_midpoint_px(box, [weird_neighbor], (1000, 1000))
    x0, y0, x1, y1 = result
    assert x0 <= 100 and y0 <= 100 and x1 >= 200 and y1 >= 200
    print("[OK] 비정상 이웃 좌표가 있어도 원래 칸보다 좁아지지 않음")


def test_gui_wiring_uses_expanded_bounds_for_domusong():
    """gui/app.py를 직접 import할 수 없으므로(tkinter 없음) 소스 코드에
    expand_box_to_neighbor_midpoint_px가 실제로 쓰이는지 정적으로 확인."""
    src = open("/root/cutline_studio/gui/app.py", encoding="utf-8").read()
    assert src.count("expand_box_to_neighbor_midpoint_px") >= 3, (
        "import 1회 + 실사용 최소 2곳(수동 도무송 배치/자동 인식 경로) 확인 실패"
    )
    print("[OK] gui/app.py의 도무송 두 경로 모두 expand_box_to_neighbor_midpoint_px를 사용함")


def main():
    test_expands_to_midpoint_when_neighbor_on_right()
    test_touching_neighbor_gives_no_expansion()
    test_no_neighbors_expands_to_full_sheet()
    test_never_shrinks_below_original_box()
    test_gui_wiring_uses_expanded_bounds_for_domusong()
    print("\nAll domusong bounds-expansion checks passed.")


if __name__ == "__main__":
    main()
