"""칼선끼리의 겹침·포함·간격 점검 (내보내기 직전 안전 검사).

2026-09-29(멍푸: "왜 중첩되면 안 되는지", 상용화 전 결함 해결): 테스트 폴더의
실제 손 칼선 8개 파일을 전부 측정해 보니, 칼선끼리 선이 교차하거나 한 칼선이
다른 칼선 안에 들어가 있는 경우는 한 번도 없었고, 서로 가장 가까운 두 칼선
사이도 약 2mm(1.98mm) 이상이었다(대부분 2.5~4.6mm).

겹치면 안 되는 이유(인쇄·후가공 기준):
  1. 칼날이 같은 자리를 두 번 지나가거나 선이 교차하면 그 부분의 스티커가
     조각나거나 찢어진다 -- 한 스티커를 떼면 옆 스티커 모서리가 같이 잘려
     나가 둘 다 불량이 된다.
  2. 칼선 안에 칼선이 있으면(이중 칼선) 스티커 가운데가 따로 떨어진다.
  3. 칼선 사이가 너무 좁으면 그 사이 여백(떼어내는 부분)이 가늘어 떼다가
     끊어지고, 도무송 목형은 칼날끼리 가까우면 만들 수 없다(인쇄소 가이드
     최소 2mm, core.cutline_core.MIN_DOMUSONG_GAP_MM).
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon
from shapely.strtree import STRtree

from .cutline_core import mm_to_px, px_to_mm


def _parts(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]


def check_cut_spacing(cut_geom, dpi: float, min_gap_mm: float = 2.0, tolerance_mm: float = 0.1):
    """칼선(외곽선) 쌍마다 교차/포함/너무 가까움을 찾는다.

    Returns: dict(crossing=[...], nested=[...], too_close=[...]) -- 각 항목은
    (x_mm, y_mm, gap_mm) 위치(두 칼선이 가장 가까운 곳 근처, 원본 이미지 기준 mm)."""
    polys = _parts(cut_geom)
    out = {"crossing": [], "nested": [], "too_close": []}
    # 칼선 안쪽 구멍(창처럼 뚫리는 안쪽 칼선)도 이중 칼선이다.
    for p in polys:
        for ring in p.interiors:
            q = Polygon(ring).representative_point()
            out["nested"].append((px_to_mm(q.x, dpi), px_to_mm(q.y, dpi), 0.0))
    if len(polys) < 2:
        return out
    limit_px = mm_to_px(max(0.0, min_gap_mm - tolerance_mm), dpi)
    tree = STRtree(polys)
    for i, a in enumerate(polys):
        for j in tree.query(a.buffer(limit_px + 1.0)):
            j = int(j)
            if j <= i:
                continue
            b = polys[j]
            if a.exterior.intersects(b.exterior):
                p = a.exterior.intersection(b.exterior).representative_point()
                out["crossing"].append((px_to_mm(p.x, dpi), px_to_mm(p.y, dpi), 0.0))
            elif a.contains(b) or b.contains(a):
                inner = b if a.contains(b) else a
                p = inner.representative_point()
                out["nested"].append((px_to_mm(p.x, dpi), px_to_mm(p.y, dpi), 0.0))
            else:
                d = a.distance(b)
                if d < limit_px:
                    p = a.exterior.interpolate(a.exterior.project(b.representative_point()))
                    out["too_close"].append((px_to_mm(p.x, dpi), px_to_mm(p.y, dpi), px_to_mm(d, dpi)))
    return out


def summarize_cut_spacing(report: dict, min_gap_mm: float = 2.0, max_items: int = 2) -> list:
    """사람이 읽는 안내 목록(비어 있으면 문제 없음) -- 종류마다 한 줄, 위치는 앞의 몇 곳만.
    (2026-09-29: 줄마다 번호가 붙는 확인 창에서 위치를 한 줄씩 늘어놓으면 작은 화면에서
    창이 화면 밖으로 넘쳐 버튼이 가려졌다.)"""
    def _where(items, with_gap=False):
        locs = []
        for x, y, g in items[:max_items]:
            locs.append(f"가로 {x:.0f}·세로 {y:.0f}mm" + (f"(간격 {g:.1f}mm)" if with_gap else ""))
        more = " 등" if len(items) > max_items else ""
        return f" -- {', '.join(locs)}{more}" if locs else ""

    lines = []
    if report["crossing"]:
        lines.append(f"칼선끼리 교차하는 곳 {len(report['crossing'])}군데(스티커가 서로 잘려 나감)"
                     + _where(report["crossing"]))
    if report["nested"]:
        lines.append(f"칼선 안에 또 칼선이 있는 곳 {len(report['nested'])}군데(이중 칼선, 가운데가 떨어짐)"
                     + _where(report["nested"]))
    if report["too_close"]:
        lines.append(f"칼선 사이가 {min_gap_mm:g}mm보다 가까운 곳 {len(report['too_close'])}군데(떼는 여백이 끊어짐)"
                     + _where(report["too_close"], with_gap=True))
    return lines
