"""
Pure algorithm-correctness test for core.ai_cutline_reader's guillotine grid
reconstruction (_reconstruct_grid_cells) -- uses only invented, synthetic
line-position numbers (no image, no .ai file, nothing derived from any real
client file), purely to verify the RECONSTRUCTION MATH is correct. This is
the same spirit as test_fidelity.py's known-radius circle check: known,
analytic input -> known, checkable output.

2026-09-07 배경: 실제 사용자 파일(눈송이 시트)의 '개별재단 레이어'가
단순한 균등 격자가 아니라, 왼쪽 구역은 가로선 3개로 4행, 오른쪽 구역은
별도로 가로선 1개로 2행만 나뉘는 식으로 "구역마다 다르게 나뉜" 불균등
격자였다 -- 그래서 재구성 알고리즘이 이런 불균등 구조도 정확히 풀어내는지
확인해야 한다. 아래 테스트는 그 실제 파일과 같은 '모양'(왼쪽 2칸을 세로선
하나로 나누고, 그 왼쪽 전체를 가로선 3개로 4행으로 나누는 것과 별개로
오른쪽 칸은 가로선 1개로 2행만 나누는 구조)을 흉내내되, 전부 지어낸
숫자로만 구성한다.
"""

import sys
sys.path.insert(0, "/root/cutline_studio")

from core.ai_cutline_reader import _reconstruct_grid_cells


def _approx_eq(a, b, tol=1e-6):
    return abs(a - b) < tol


def _assert_cells_match(cells, expected, label):
    assert len(cells) == len(expected), (
        f"{label}: expected {len(expected)} cells, got {len(cells)}: {cells}"
    )
    remaining = list(expected)
    for c in cells:
        match = next(
            (e for e in remaining if all(_approx_eq(a, b) for a, b in zip(c, e))), None
        )
        assert match is not None, f"{label}: unexpected cell {c} not in expected set {expected}"
        remaining.remove(match)
    print(f"[OK] {label}: {len(cells)} cells reconstructed exactly as expected")


def test_simple_uniform_grid():
    """2 vertical dividers x 1 horizontal divider, all full-span -> a plain
    3x2 uniform grid (6 cells) -- the simplest possible case."""
    bounds = (0, 0, 300, 200)
    v_lines = [(100, 0, 200), (200, 0, 200)]
    h_lines = [(100, 0, 300)]
    cells = _reconstruct_grid_cells(v_lines, h_lines, bounds)
    expected = [
        (0, 0, 100, 100), (100, 0, 200, 100), (200, 0, 300, 100),
        (0, 100, 100, 200), (100, 100, 200, 200), (200, 100, 300, 200),
    ]
    _assert_cells_match(cells, expected, "simple uniform 3x2 grid")


def test_no_lines_stays_one_cell():
    """No lines at all -> the whole bounds is one single cell (nothing to split)."""
    bounds = (0, 0, 400, 300)
    cells = _reconstruct_grid_cells([], [], bounds)
    assert cells == [bounds], f"expected the untouched bounds as a single cell, got {cells}"
    print("[OK] no lines at all -> stays a single whole-sheet cell")


def test_irregular_mixed_grid():
    """실제 파일 구조를 흉내낸 불균등 격자(지어낸 숫자):
    - 세로선 하나(x=600, 전체 높이)가 시트를 왼쪽(0-600)/오른쪽(600-900)으로 나눔
    - 왼쪽 구역만, 가로선 3개(y=100,200,300, 왼쪽 구역 폭 0-600만 가로지름)로
      4개 행으로 나뉨
    - 오른쪽 구역만, 별도로 가로선 1개(y=250, 오른쪽 구역 폭 600-900만
      가로지름)로 2개 행으로 나뉨
    -> 왼쪽 4칸 + 오른쪽 2칸 = 총 6칸, 왼쪽/오른쪽이 서로 다르게 나뉘어야 함."""
    bounds = (0, 0, 900, 400)
    v_lines = [(600, 0, 400)]
    h_lines = [
        (100, 0, 600), (200, 0, 600), (300, 0, 600),  # 왼쪽 구역 전용
        (250, 600, 900),  # 오른쪽 구역 전용
    ]
    cells = _reconstruct_grid_cells(v_lines, h_lines, bounds)
    expected = [
        (0, 0, 600, 100), (0, 100, 600, 200), (0, 200, 600, 300), (0, 300, 600, 400),
        (600, 0, 900, 250), (600, 250, 900, 400),
    ]
    _assert_cells_match(cells, expected, "irregular mixed grid (left 4 rows, right 2 rows)")


def test_lines_not_reaching_tolerance_are_ignored():
    """실제로 그 구역을 끝까지 가로지르지 않는 짧은 선(부분 선)은 나누는
    기준으로 쓰이지 않아야 한다 -- 안 그러면 실제로는 안 이어진 칸이
    잘못 나뉠 수 있음."""
    bounds = (0, 0, 300, 200)
    # h line only spans 0-150 out of the full 0-300 width -- must NOT split
    h_lines = [(100, 0, 150)]
    cells = _reconstruct_grid_cells([], h_lines, bounds)
    assert cells == [bounds], (
        f"a horizontal line that doesn't span the full width must not split "
        f"the region, got {cells}"
    )
    print("[OK] a line that doesn't fully span its region is correctly ignored")


def main():
    test_simple_uniform_grid()
    test_no_lines_stays_one_cell()
    test_irregular_mixed_grid()
    test_lines_not_reaching_tolerance_are_ignored()
    print("\nAll grid-reconstruction algorithm checks passed.")


if __name__ == "__main__":
    main()
