"""
2026-09-10(48차) 회귀 테스트: "칼선이 겹치거나 캐릭터 머리 중간에 걸쳐져
있어" / "칼선 뭉침, 겹침" 피드백의 원인 하나 -- 반복되는 도안을 복제할 때
(gui.app의 그룹 복제 로직) 지금까지는 대표 인스턴스의 칼선을 순수
평행이동(dx, dy)만 해서 옮겨 붙였다. 그런데 core.multi_design.
group_identical_boxes_px는 실측으로 확인된 이유(max_size_diff_px=8.0,
2026-09-09/29차 주석 참고)로 칸 크기가 최대 8px까지 달라도 "같은 반복"으로
묶는다 -- 이 경우 순수 평행이동만 하면 대표 칼선이 원래 크기 그대로
옮겨져서, 대상 칸이 더 작으면 옆 칸을 침범하고(겹침), 크면 안쪽에 뜬다.

core.repeat_grid.fit_cutline_result_to_box가 대표 칸(from_box_px)을 대상 칸
(to_box_px)에 정확히 맞춰 늘이고 옮기는지, 순수 합성 좌표로 확인한다.
"""

import sys

sys.path.insert(0, "/root/cutline_studio")

from shapely.geometry import Polygon

from core.cutline_core import CutlineResult
from core.repeat_grid import fit_cutline_result_to_box, translate_cutline_result

DPI = 300.0


def _item(poly):
    return CutlineResult(dpi=DPI, width_px=5000, height_px=5000, design=poly,
                          offsets={"cut": poly}, offset_mm=None, adjustments=[])


def test_same_size_box_matches_plain_translate():
    """칸 크기가 완전히 같으면 기존 translate_cutline_result와 동일해야 함
    (회귀 없음 -- 대부분의 반복은 크기가 완전히 같으므로)."""
    poly = Polygon([(10, 10), (90, 10), (90, 90), (10, 90)])
    from_box = (0, 0, 100, 100)
    to_box = (500, 300, 600, 400)  # 같은 크기(100x100), 위치만 이동
    fitted = fit_cutline_result_to_box(_item(poly), from_box, to_box)
    plain = translate_cutline_result(_item(poly), 500, 300)
    assert fitted.offsets["cut"].equals_exact(plain.offsets["cut"], 1e-6)
    print("[OK] 칸 크기가 같으면 기존 평행이동과 결과가 동일함(회귀 없음)")


def test_smaller_target_box_no_longer_overflows():
    """대표 칸(100x100)보다 6px 작은 칸(94x100)에 옮겨 붙일 때, 옛 방식
    (순수 평행이동)이면 칼선이 대상 칸 오른쪽 경계를 넘어가 옆 칸을
    침범했을 상황 -- 새 방식은 대상 칸 안에 정확히 맞아야 한다."""
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])  # 칸에 꽉 찬 도안
    from_box = (0, 0, 100, 100)
    to_box = (1000, 0, 1094, 100)  # 폭이 6px 작음(실측된 최대 8px 이내)

    old = translate_cutline_result(_item(poly), 1000, 0)
    old_bounds = old.offsets["cut"].bounds
    assert old_bounds[2] > 1094, "구 버전(순수 평행이동)은 실제로 대상 칸 밖으로 넘쳤어야 함(대조군)"

    new = fit_cutline_result_to_box(_item(poly), from_box, to_box)
    new_bounds = new.offsets["cut"].bounds
    assert abs(new_bounds[0] - 1000) < 1e-6 and abs(new_bounds[2] - 1094) < 1e-6, (
        f"새 방식은 대상 칸(1000~1094)에 정확히 맞아야 하는데 {new_bounds}"
    )
    print("[OK] 대상 칸이 더 작아도(구 버전이면 겹쳤을 상황) 정확히 그 크기에 맞춰짐")


def test_larger_target_box_no_longer_leaves_gap():
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    from_box = (0, 0, 100, 100)
    to_box = (2000, 0, 2106, 100)  # 폭이 6px 큼
    new = fit_cutline_result_to_box(_item(poly), from_box, to_box)
    new_bounds = new.offsets["cut"].bounds
    assert abs(new_bounds[2] - 2106) < 1e-6, f"대상 칸이 더 커도 그 경계까지 정확히 맞춰져야 함: {new_bounds}"
    print("[OK] 대상 칸이 더 커도(구 버전이면 틈이 남았을 상황) 정확히 그 크기에 맞춰짐")


def test_gui_wiring_uses_fit_not_plain_translate_for_repeats():
    src = open("/root/cutline_studio/gui/app.py", encoding="utf-8").read()
    assert src.count("fit_cutline_result_to_box(") >= 2, "반복 복제 두 경로 모두 새 함수를 써야 함"
    print("[OK] gui/app.py의 반복 복제 두 경로 모두 fit_cutline_result_to_box를 사용함")


def main():
    test_same_size_box_matches_plain_translate()
    test_smaller_target_box_no_longer_overflows()
    test_larger_target_box_no_longer_leaves_gap()
    test_gui_wiring_uses_fit_not_plain_translate_for_repeats()
    print("\nAll repeat size-jitter fit checks passed.")


if __name__ == "__main__":
    main()
