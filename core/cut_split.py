"""칼선 하나가 아주 가는 "목"으로 이어진 두 덩어리이면 목을 끊어 두 칼선으로 나눈다.

2026-09-30(멍푸 실제 사용 피드백 -- 흰 테두리 스티커 시트에서 말풍선 꼬리가 옆 원형 스티커
테두리에 닿아, 두 스티커 칼선이 0.1~0.5mm 폭의 가는 목으로 이어진 한 칼선이 됐다. "연결
끊어내기만 하면 합격"): 원본 손 칼선은 두 스티커를 따로 돌았다.

목 = 칼선을 목 폭의 절반만큼 줄였다 되돌리면 사라지는 부분 중, 남은 큰 덩어리 두 개 이상에
동시에 닿는 부분. 한 덩어리에만 붙은 가는 부분(초승달 끝, 꼬리 등)은 끊지 않는다.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


def _parts(g):
    if g is None or g.is_empty:
        return []
    return [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and not p.is_empty]


def split_narrow_necks(geom, dpi: float, neck_mm: float = 0.7, min_lobe_mm2: float = 16.0):
    """geom(Polygon/MultiPolygon)의 각 조각을 가는 목에서 끊은 조각 목록. 끊을 곳이 없으면
    원래 조각 그대로. 끊긴 조각끼리는 목 길이만큼 떨어진다."""
    px = dpi / 25.4
    r = 0.5 * neck_mm * px
    min_lobe = min_lobe_mm2 * px * px
    out = []
    for p in _parts(geom):
        if p.area < 2 * min_lobe:
            out.append(p)
            continue
        opened = p.buffer(-r, join_style=1).buffer(r, join_style=1)
        lobes = [q for q in _parts(opened) if q.area >= min_lobe]
        if len(lobes) < 2:
            out.append(p)
            continue
        rest = p.difference(unary_union(lobes).buffer(0.5))
        necks = [q for q in _parts(rest) if sum(1 for L in lobes if q.distance(L) < 1.5) >= 2]
        if not necks:
            out.append(p)
            continue
        cut = p.difference(unary_union(necks).buffer(1.0))
        pieces = []
        for q in _parts(cut):
            if q.area < 0.25 * min_lobe:
                continue
            s = q.buffer(-0.3 * px, join_style=1).buffer(0.3 * px, join_style=1)
            s = max(_parts(s), key=lambda z: z.area) if _parts(s) else q
            pieces.append(Polygon(s.exterior))
        if len(pieces) >= 2:
            out.extend(pieces)
        else:
            out.append(p)
    return out


def split_result_at_necks(result, dpi: float):
    """CutlineResult의 칼선을 목에서 끊어, 끊긴 조각이 있으면 조각별 결과 목록(첫 번째는
    원래 결과를 고친 것)을, 없으면 None."""
    import dataclasses

    cut = result.offsets.get("cut") if result is not None else None
    if cut is None or cut.is_empty:
        return None
    before = _parts(cut)
    pieces = split_narrow_necks(cut, dpi)
    if len(pieces) <= len(before):
        return None
    note = "두 요소가 아주 가는 목으로 이어진 칼선은 목을 끊어 요소마다 따로 잘랐습니다."
    outs = []
    for q in pieces:
        mp = MultiPolygon([q])
        outs.append(dataclasses.replace(
            result, design=mp, offsets={**result.offsets, "cut": mp},
            adjustments=list(result.adjustments or []) + [note],
        ))
    return outs
