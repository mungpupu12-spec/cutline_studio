"""
2026-09-11(52차) 회귀 테스트: "도무송 칼선이 많이 밀리고 자리잡지 못 했어",
"중심을 벗어나" 피드백.

51차가 boxes를 칸(패널) 단위에서 그 안의 낱개 조각 단위로 더 잘게 만들면서,
도무송의 "옆 도안 침범 금지" 이웃 계산(core.multi_design.
expand_box_to_neighbor_midpoint_px, 46차)이 같은 패널 안의 다른 조각(예:
바로 옆 하트 스티커)까지 "이웃"으로 잡아버리는 회귀가 생겼다. 원래 46차
의도는 "다음 패널을 침범하지 말라"는 것이었는데, 조각 단위 boxes를 그대로
넘기면 훨씬 가까운 거리에서 서로를 이웃으로 인식해 도무송 여유 계산이
한쪽으로 심하게 치우친다(비대칭) -- 그 비대칭 경계에서 칼선이 잘려나가
중심을 벗어난 것처럼 보인다.

gui.app을 import하지 않고(tkinter 없음) core.multi_design.
expand_box_to_neighbor_midpoint_px만으로, "조각 단위 boxes를 이웃으로 쓰면
비대칭이 심해지고, 칸(패널) 단위 boxes를 쓰면 원래 46차처럼 대칭적으로
확장되는지"를 순수 좌표로 확인한다."""

import os
import sys

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from core.multi_design import expand_box_to_neighbor_midpoint_px

IMAGE_SIZE = (2000, 1000)

# 패널 두 개가 나란히 놓인 시트: 패널 A(0..900), 패널 B(1000..1900) --
# 사이에 100px 여백. 패널 A 안에는 조각 두 개(주 캐릭터 400..600, 그
# 바로 옆(20px 틈) 작은 하트 620..700)가 들어있다.
CELL_A = (0, 0, 900, 1000)
CELL_B = (1000, 0, 1900, 1000)
CHAR_BOX = (400, 300, 600, 700)  # 패널 A 안의 주 캐릭터
HEART_BOX = (620, 400, 700, 600)  # 패널 A 안, 캐릭터 바로 옆(20px 틈) 하트


def test_fine_grained_neighbor_boxes_cause_asymmetric_tight_expansion():
    """51차 이후처럼 조각 단위 boxes(주 캐릭터 자신 + 바로 옆 하트 + 다른
    패널 B)를 그대로 이웃으로 넘기면, 캐릭터의 오른쪽 확장이 하트에 막혀
    거의 넓어지지 못한다(20px 틈의 중간인 10px 정도) -- 원래 패널 경계
    (900px)까지 넓어져야 하는 것과 비교하면 훨씬 비대칭적이고 좁다."""
    fine_boxes = [CHAR_BOX, HEART_BOX, CELL_B]
    expanded = expand_box_to_neighbor_midpoint_px(CHAR_BOX, fine_boxes, IMAGE_SIZE)
    left, top, right, bottom = expanded
    print(f"조각 단위 이웃으로 확장한 결과: {expanded} (오른쪽이 하트에 막혀 매우 좁아야 함)")
    # 오른쪽은 캐릭터(600)와 하트(620) 중간인 610 근처까지만 넓어짐 -- 패널
    # 경계(900)에는 전혀 못 미침.
    assert right < 650, f"조각 단위 이웃 때문에 오른쪽이 하트에 막혀 좁아지는 회귀가 재현되어야 하는데 {right}"
    print("[OK] 조각 단위 boxes를 이웃으로 쓰면 바로 옆 조각에 막혀 비대칭적으로 좁게 확장됨(회귀 재현)")


def test_cell_level_neighbor_boxes_ignore_same_panel_sibling():
    """52차로 고친 대로, 도무송 이웃 계산에 칸(패널) 단위 boxes(CELL_A,
    CELL_B)만 넘기면, 같은 패널 안의 하트(620px)는 완전히 무시되고 실제
    다음 패널(B, 1000px)까지의 중간 지점(캐릭터 자신의 오른쪽 끝 600과
    패널 B의 왼쪽 끝 1000의 중간인 800px)까지 넓어져야 한다 -- 조각 단위
    boxes를 썼을 때의 610px(하트에 막힘)보다 훨씬 넓다. 그리고 반대쪽
    (왼쪽/위/아래)은 이웃이 없으니 이미지 가장자리까지 자유롭게 넓어진다."""
    cell_level_boxes = [CELL_A, CELL_B]
    expanded = expand_box_to_neighbor_midpoint_px(CHAR_BOX, cell_level_boxes, IMAGE_SIZE)
    left, top, right, bottom = expanded
    print(f"칸 단위 이웃으로 확장한 결과: {expanded} (오른쪽은 패널 B와의 중간인 800 근처까지)")
    assert right >= 750, (
        f"칸 단위 이웃을 쓰면 같은 패널 안 하트를 무시하고 다음 패널 쪽으로 넓게 확장돼야 하는데 {right}"
    )
    assert left == 0 and top == 0 and bottom == IMAGE_SIZE[1], (
        f"이웃이 없는 방향(왼쪽/위/아래)은 이미지 가장자리까지 자유롭게 넓어져야 하는데 {expanded}"
    )
    print("[OK] 칸 단위 boxes를 이웃으로 쓰면 같은 패널 안 조각을 무시하고 실제 다음 패널까지 넓게 확장됨")


def test_gui_wiring_uses_cell_level_boxes_for_domusong_neighbor():
    """gui/app.py를 직접 import하지 않고(tkinter 없음) 소스 코드로, 도무송
    이웃-중간지점 확장 두 호출 지점이 실제로 칸(패널) 단위 목록을 쓰도록
    바뀌었는지 확인한다:
    - `_run_auto_detect_and_add_all`: `self._auto_detect_all_boxes_px`에
      조각 단위 `boxes`가 아니라 칸 단위 `cell_boxes`를 대입해야 함.
    - `_run_mixed_generate_subset`의 도무송 분기: `expand_box_to_neighbor_
      midpoint_px` 호출에 `self._mixed_cell_boxes_px`를 넘겨야 함(조각
      단위 `boxes`를 그대로 넘기면 52차 버그 재발)."""
    src = open("/root/cutline_studio/gui/app.py", encoding="utf-8").read()
    assert "self._auto_detect_all_boxes_px = cell_boxes" in src, (
        "_run_auto_detect_and_add_all이 도무송 이웃 목록에 조각 단위 boxes를 쓰고 있음(52차 회귀)"
    )
    assert "_mixed_cell_boxes_px" in src, "gui/app.py에 _mixed_cell_boxes_px 배선이 없음"
    idx = src.index("def _run_mixed_generate_subset")
    subset_src = src[idx:idx + 4000]
    assert "getattr(self, \"_mixed_cell_boxes_px\", None) or boxes" in subset_src, (
        "_run_mixed_generate_subset의 도무송 이웃 확장이 여전히 조각 단위 boxes를 직접 씀(52차 회귀)"
    )
    print("[OK] gui.app 두 호출 지점 모두 도무송 이웃 계산에 칸 단위 boxes를 씀(정적 분석 확인)")


def main():
    test_fine_grained_neighbor_boxes_cause_asymmetric_tight_expansion()
    test_cell_level_neighbor_boxes_ignore_same_panel_sibling()
    test_gui_wiring_uses_cell_level_boxes_for_domusong_neighbor()
    print("\nAll domusong neighbor cell-vs-fragment checks passed.")


if __name__ == "__main__":
    main()
