"""
2026-09-10(47차) 회귀 테스트였던 원본 -- core.accumulate.combine_results가 시트에
누적된 모든 도안의 칼선을 한 덩어리로 묶어 buffer(+)/buffer(-)("2mm 안이면
합치기" 규칙)를 한 번에 적용하던 문제를 다뤘었다.

2026-09-14(실제 파일로 재확인, "칼선이 방황한다" -- 멍푸님 피드백): 그 2mm
강제 병합 규칙을 서로 다른 누적 항목(도안)에 적용하는 것 자체가, 실제 시트
파일(캐릭터들이 인쇄 효율을 위해 아주 가깝게, 종종 거의 맞닿게 배치됨)에서는
무관한 캐릭터 여러 개를 하나의 구불구불한 윤곽으로 뭉개버리는 훨씬 더 심각한
문제였다. 멍푸님 확인: "개체를 둘러싸는 칼선이어야해... 각각의 요소에 개별
칼선이 있어야해"가 이 2mm 규칙보다 우선한다 -- 이제 combine_results는 서로
다른 항목끼리는 아무리 가깝거나 겹쳐도 절대 합치지 않고, 각 항목의 원래 모양을
그대로 보존한다(core/accumulate.py 모듈 docstring 참고).

이 파일은 그 새 정책(항상 개별 보존, 병합 없음)을 검증한다. `merge_close_elements`
라는 원시 함수 자체(한 도안 내부의 여러 조각을 잇는 용도로는 여전히 존재)는
core.cutline_core.compute_offsets가 단일 항목 안에서 쓰는 것과 별개로, 그
함수 자체의 동작만 직접 호출해 확인한다.

전부 합성 좌표(사각형/뾰족한 스파이크 도형)만 사용 -- 순수 기하 연산 검증.
"""

import sys

sys.path.insert(0, "/root/cutline_studio")

from shapely.geometry import MultiPolygon, Polygon

from core.accumulate import combine_results
from core.cutline_core import CutlineResult, merge_close_elements, mm_to_px

DPI = 300.0


def _item(poly):
    return CutlineResult(
        dpi=DPI, width_px=5000, height_px=5000, design=poly,
        offsets={"cut": poly}, offset_mm=None, adjustments=[],
    )


def test_close_elements_no_longer_merge_across_items():
    """2026-09-14 결정: 2mm보다 가까운 두 '서로 다른 항목'도 이제 합쳐지지
    않고, 각자 원본 모양 그대로 개별 도형으로 남아야 한다."""
    gap_px_1mm = mm_to_px(1.0, DPI)  # 옛 2mm 규칙보다 좁은 실제 간격
    r1 = Polygon([(0, 0), (300, 0), (300, 300), (0, 300)])
    r2 = Polygon([
        (300 + gap_px_1mm, 0), (600 + gap_px_1mm, 0),
        (600 + gap_px_1mm, 300), (300 + gap_px_1mm, 300),
    ])
    combined = combine_results([_item(r1), _item(r2)])
    cut = combined.offsets["cut"]
    assert len(cut.geoms) == 2, "가까운 두 항목도 이제 합쳐지지 않고 개별로 남아야 함"
    matches1 = [g for g in cut.geoms if g.equals(r1)]
    matches2 = [g for g in cut.geoms if g.equals(r2)]
    assert len(matches1) == 1 and len(matches2) == 1, (
        "각 항목의 도형은 buffer 연산 없이 원본과 완전히 동일하게 보존돼야 함"
    )
    print("[OK] 2mm 미만 간격인 두 항목도 더 이상 강제 병합되지 않고 개별 칼선으로 보존됨")


def test_overlapping_items_stay_separate_not_unioned():
    """멍푸님 지시(2번): '합치지 않고 겹치더라도 각자 선으로' -- 실제로
    겹치는 두 항목조차 하나로 union되지 않고 각자 개별 도형으로 남아야 한다."""
    r1 = Polygon([(0, 0), (300, 0), (300, 300), (0, 300)])
    r2 = Polygon([(150, 150), (450, 150), (450, 450), (150, 450)])  # r1과 겹침
    combined = combine_results([_item(r1), _item(r2)])
    cut = combined.offsets["cut"]
    assert len(cut.geoms) == 2, "겹치는 두 항목도 하나로 합쳐지면 안 되고 2개로 남아야 함"
    matches1 = [g for g in cut.geoms if g.equals(r1)]
    matches2 = [g for g in cut.geoms if g.equals(r2)]
    assert len(matches1) == 1 and len(matches2) == 1, (
        "겹치는 항목도 각자 원본 모양 그대로(unary_union 없이) 보존돼야 함"
    )
    print("[OK] 겹치는 두 항목도 하나로 합쳐지지 않고 각자 원본 그대로 개별 보존됨")


def test_far_elements_stay_separate():
    gap_px_5mm = mm_to_px(5.0, DPI)
    r3 = Polygon([(0, 0), (300, 0), (300, 300), (0, 300)])
    r4 = Polygon([
        (300 + gap_px_5mm, 0), (600 + gap_px_5mm, 0),
        (600 + gap_px_5mm, 300), (300 + gap_px_5mm, 300),
    ])
    combined = combine_results([_item(r3), _item(r4)])
    cut = combined.offsets["cut"]
    assert len(cut.geoms) == 2, "멀리 떨어진 두 도안은 합쳐지면 안 됨"
    print("[OK] 멀리 떨어진 두 도안은 (이전과 동일하게) 합쳐지지 않고 유지됨")


def test_unrelated_far_design_is_pixel_exact_preserved():
    """세 항목(가까운 둘 + 먼 하나) 모두, buffer/union 연산 자체를 전혀
    거치지 않아 전부 원본과 완전히 동일해야 한다(뭉개짐 방지)."""
    gap_px_1mm = mm_to_px(1.0, DPI)
    r1 = Polygon([(0, 0), (300, 0), (300, 300), (0, 300)])
    r2 = Polygon([
        (300 + gap_px_1mm, 0), (600 + gap_px_1mm, 0),
        (600 + gap_px_1mm, 300), (300 + gap_px_1mm, 300),
    ])
    far_pts = [
        (3000, 0), (3200, 0), (3200, 200), (3130, 200),
        (3100, 260), (3070, 200), (3000, 200),
    ]
    far = Polygon(far_pts)
    combined = combine_results([_item(r1), _item(r2), _item(far)])
    cut = combined.offsets["cut"]
    assert len(cut.geoms) == 3, "세 항목 모두 합쳐지지 않고 개별로 남아야 함(총 3개)"
    for name, poly in (("r1", r1), ("r2", r2), ("far", far)):
        matches = [g for g in cut.geoms if g.equals(poly)]
        assert len(matches) == 1, f"{name} 도안은 buffer/union을 거치지 않아 원본과 완전히 같아야 함"
    print("[OK] 가깝든 멀든 모든 항목이 buffer/union 연산을 거치지 않아 원본과 정확히 동일하게 보존됨")


def test_merge_close_elements_direct_single_group_still_closes():
    """merge_close_elements라는 원시 함수 자체는 그대로 유지된다 -- 이 함수를
    직접 호출하면(combine_results를 거치지 않고) 가까운 조각끼리는 여전히
    하나로 닫힌다. (core.cutline_core.compute_offsets가 한 도안 내부의 여러
    조각을 이을 때 이 함수를 계속 쓰므로, 그 용도의 동작은 안 바뀌었음을 확인)"""
    gap_px = mm_to_px(2.0, DPI)
    a = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    b = Polygon([(100 + gap_px * 0.3, 0), (200 + gap_px * 0.3, 0),
                 (200 + gap_px * 0.3, 100), (100 + gap_px * 0.3, 100)])
    result = merge_close_elements(MultiPolygon([a, b]), gap_px)
    assert len(result.geoms) == 1
    print("[OK] merge_close_elements 직접 호출은 여전히 가까운 조각을 정상적으로 합침(단일 도안 내부 용도는 불변)")


def main():
    test_close_elements_no_longer_merge_across_items()
    test_overlapping_items_stay_separate_not_unioned()
    test_far_elements_stay_separate()
    test_unrelated_far_design_is_pixel_exact_preserved()
    test_merge_close_elements_direct_single_group_still_closes()
    print("\nAll merge-close-elements isolation checks passed.")


if __name__ == "__main__":
    main()
