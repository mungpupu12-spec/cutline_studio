"""2026-11-01: 누적 카드의 "(칼선 N개)"는 그림 조각 수가 아니라 실제 칼선 수(키스컷은 가까운 조각을 하나로 묶음)."""
from shapely.geometry import MultiPolygon, Polygon, box

from core.accumulate import count_cut_lines
from core.cutline_core import CutlineResult, OffsetSpec


def _res(design, cut):
    offsets = {} if cut is None else {"cut": cut}
    return CutlineResult(dpi=300.0, width_px=100, height_px=100, design=design, offsets=offsets,
                         offset_mm=OffsetSpec(1, 1, 1))


def test_counts_cut_not_design_parts():
    parts = MultiPolygon([box(0, 0, 5, 5), box(6, 0, 11, 5), box(30, 0, 35, 5)])
    merged = MultiPolygon([box(-1, -1, 12, 6), box(29, -1, 36, 6)])   # 가까운 둘은 칼선 하나
    assert count_cut_lines(_res(parts, merged)) == 2


def test_falls_back_to_design_without_cut():
    parts = MultiPolygon([box(0, 0, 5, 5), box(10, 0, 15, 5)])
    assert count_cut_lines(_res(parts, None)) == 2
    assert count_cut_lines(_res(parts, MultiPolygon())) == 2
    assert count_cut_lines(_res(MultiPolygon(), None)) == 0
    assert count_cut_lines(_res(parts, Polygon([(0, 0), (1, 0), (1, 1)]))) == 1
