"""
Pure algorithm-correctness test for
core.interactive_cutline.is_silhouette_undersized -- uses only invented,
synthetic area numbers (no image, no .ai file, nothing derived from any real
client file), purely to verify the FALLBACK DECISION MATH is correct. Same
spirit as test_fidelity.py's known-radius circle check: known, analytic
input -> known, checkable output.

2026-09-08 배경: 실제 파일(너구리+곰+꽃 반복 패턴 시트)에서, GrabCut 실루엣
추적이 캐릭터 전체가 아니라 몸 안의 대비가 강한 작은 부분(예: 햄스터의 배
무늬)만 잘못 잡는 경우가 확인됐다 -- 이 함수는 그 실패를 "선택 영역 대비
너무 작은 실루엣"이라는 신호로 감지해서 안전한 사각형 컷으로 대체할지
말지를 결정한다. 아래 테스트는 이 결정 로직 자체(비율 계산과 임계값
비교)가 정확한지만 확인한다 -- 실제 GrabCut이 어떻게 동작하는지, 실제
캐릭터의 진짜 실루엣 비율이 얼마인지는 다루지 않는다(그건 실제 파일로만
판단할 수 있는 영역이라 이 테스트의 범위 밖).
"""

import sys
sys.path.insert(0, "/root/cutline_studio")

from core.interactive_cutline import (
    DEFAULT_UNDERSIZED_SILHOUETTE_RATIO,
    is_silhouette_undersized,
)


def test_normal_silhouette_is_not_flagged():
    """정상적인 캐릭터라면 선택 영역의 상당 부분(예: 60%)을 채운다 --
    기본 임계값(35%)보다 훨씬 크므로 대체 대상이 아니어야 한다."""
    cell_area = 40_000.0  # e.g. 200x200px 셀
    design_area = 0.60 * cell_area
    assert not is_silhouette_undersized(design_area, cell_area)
    print("[OK] 선택 영역의 60%를 채운 정상 실루엣은 대체 대상이 아님")


def test_belly_only_silhouette_is_flagged():
    """햄스터 배 무늬처럼 몸 전체가 아니라 극히 일부(예: 7%)만 잡힌
    경우는 기본 임계값(35%) 밑이므로 대체 대상이어야 한다."""
    cell_area = 40_000.0
    design_area = 0.07 * cell_area
    assert is_silhouette_undersized(design_area, cell_area)
    print("[OK] 선택 영역의 7%만 잡힌 실루엣(배 무늬 사례)은 대체 대상으로 감지됨")


def test_boundary_exactly_at_ratio_is_not_flagged():
    """design_area가 정확히 min_ratio * reference_area와 같으면(경계값),
    '미만'일 때만 대체하므로 대체 대상이 아니어야 한다(엄격한 부등호 확인)."""
    cell_area = 10_000.0
    design_area = DEFAULT_UNDERSIZED_SILHOUETTE_RATIO * cell_area
    assert not is_silhouette_undersized(design_area, cell_area)
    print("[OK] 경계값(정확히 임계 비율)은 '미만'이 아니므로 대체 대상이 아님")


def test_just_below_boundary_is_flagged():
    """경계값보다 아주 살짝 작으면 대체 대상이어야 한다."""
    cell_area = 10_000.0
    design_area = DEFAULT_UNDERSIZED_SILHOUETTE_RATIO * cell_area - 1.0
    assert is_silhouette_undersized(design_area, cell_area)
    print("[OK] 경계값보다 아주 살짝 작은 경우는 대체 대상으로 감지됨")


def test_zero_or_negative_reference_area_never_flags():
    """비교할 기준(선택 영역 넓이)이 0 이하면(방어적 처리) 비교 자체가
    의미 없으므로 항상 False -- 이상한 값 때문에 잘못 대체되면 안 된다."""
    assert not is_silhouette_undersized(5.0, 0.0)
    assert not is_silhouette_undersized(0.0, -100.0)
    print("[OK] 기준 넓이가 0 이하인 방어적 경우는 항상 대체 대상이 아님")


def test_zero_design_area_with_normal_reference_is_flagged():
    """실루엣이 아예 안 잡힌 경우(design_area=0)도 당연히 대체 대상이어야
    한다."""
    assert is_silhouette_undersized(0.0, 40_000.0)
    print("[OK] 실루엣이 전혀 안 잡힌 경우(0)도 대체 대상으로 감지됨")


def main():
    test_normal_silhouette_is_not_flagged()
    test_belly_only_silhouette_is_flagged()
    test_boundary_exactly_at_ratio_is_not_flagged()
    test_just_below_boundary_is_flagged()
    test_zero_or_negative_reference_area_never_flags()
    test_zero_design_area_with_normal_reference_is_flagged()
    print("\nAll silhouette-fallback decision-logic checks passed.")


if __name__ == "__main__":
    main()
