"""배경 요소(칼선을 만들지 않아도 되는 것) 찾기.

2026-11-01(멍푸: 리무버블 스티커 AI 시트 -- "이건 칼선 설정 안 해도 돼? 선택 여부로 만들고", "배경요소는 칼선으로
지정하는거 선택할 수 있게 창을 띄워서 생성/미생성으로 구분지어"): 작가 원본 칼선과 대조하니 두 가지가 달랐다.
  - 장식선: 칸 네 모서리의 'Rec' 꺾쇠 표시(가는 선 + 작은 점·글자)는 작가는 자르지 않는데 프로그램은 잘랐다.
  - 배경 판: 강아지 뒤 초록 의자 등받이(한 색 사각 판)를 프로그램은 강아지와 한 덩어리로 잘랐다. 작가는 강아지만.
찾은 것은 화면에서 생성/미생성을 고르게 하고(gui), 미생성이면 장식선 칼선은 빼고 배경 판은 판을 뺀 그림 모양으로
같은 간격의 칼선을 다시 만든다.

판정은 칼선 하나(폴리곤)와 그 안의 원본 픽셀로 한다(작업 경로와 상관없이 같은 기준).
  - 장식선: 칼선이 16mm 안쪽으로 작고, 칼선 안에서 바탕색과 다른 부분이 '1.2mm보다 가늘고 7mm보다 긴 선'과
    작은 점(8mm² 이하)뿐이며, 그 부분이 칼선 넓이의 35% 이하.
  - 배경 판: 칼선 안 그림에서 한 색 영역이 거의 사각형(볼록 껍질이 최소 사각형의 85% 이상, 꼭짓점 4~6개)이고,
    그림의 25% 이상이 그 판 밖으로 나와 있으며, 판 안쪽에서도 판 밖 그림과 같은 색으로 이어진 부분이 판의 15%
    이상을 가린다(판이 그림 뒤에 있다). 판 밖 그림에서 시작해 같은 색으로 이어진 부분만 그림으로 남긴다.
"""
from __future__ import annotations

import cv2
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

DECO = "deco"
PANEL = "panel"


def _geoms(g) -> list:
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


def _raster(geom, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    for p in _geoms(geom):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        cv2.fillPoly(m, [(np.asarray(p.exterior.coords) - (x0, y0)).round().astype(np.int32)], 1)
        for r in p.interiors:
            cv2.fillPoly(m, [(np.asarray(r.coords) - (x0, y0)).round().astype(np.int32)], 0)
    return m.astype(bool)


def _is_deco(sub: np.ndarray, inside: np.ndarray, mm: float) -> bool:
    ring = inside & ~cv2.erode(inside.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    if ring.sum() < 10:
        return False
    bg = np.median(sub[ring], axis=0)
    ink = (np.abs(sub - bg).sum(axis=2) > 60) & inside
    ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return False
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    has_thin, thin_a, all_a = False, 0.0, 0.0
    for i in range(1, n):
        th = 2 * float(dist[lab == i].max()) / mm
        span = max(st[i, 2], st[i, 3]) / mm
        a = st[i, 4] / mm / mm
        all_a += a
        if th <= 1.2 and span >= 7.0:
            has_thin = True
            thin_a += a
        elif a > 8.0:
            return False
    return has_thin and thin_a >= 0.4 * all_a and ink.sum() <= 0.35 * inside.sum()


def _panel_figure(sub: np.ndarray, art: np.ndarray, mm: float, x0: int, y0: int):
    """판을 뺀 그림 모양(원본 좌표 폴리곤) 또는 None."""
    q = (sub // 24).astype(np.int32)
    cls = q[:, :, 0] * 10000 + q[:, :, 1] * 100 + q[:, :, 2]
    A = int(art.sum())
    vals, cnts = np.unique(cls[art], return_counts=True)
    h, w = art.shape
    for k in vals[np.argsort(-cnts)][:4]:
        R = (cls == k) & art
        if R.sum() < 0.12 * A:
            continue
        R = cv2.morphologyEx(R.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(R, connectivity=8)
        if n <= 1:
            continue
        big = 1 + int(np.argmax(st[1:, 4]))
        if st[big, 4] < 0.12 * A:
            continue
        hull = cv2.convexHull(cv2.findNonZero((lab == big).astype(np.uint8)))
        harea = cv2.contourArea(hull)
        (_cx, _cy), (rw, rh), _ang = cv2.minAreaRect(hull)
        if rw * rh <= 0 or harea / (rw * rh) < 0.85:
            continue
        if not 4 <= len(cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)) <= 6:
            continue
        Hm = np.zeros((h, w), np.uint8)
        cv2.fillPoly(Hm, [hull], 1)
        Hm = Hm.astype(bool)
        S = art & ~Hm
        if S.sum() < 0.25 * A or (Hm & art).sum() < 0.15 * A:
            continue
        # 판 밖으로 나온 그림(S)에서 시작해, 판 안쪽은 같은 색으로 이어진 부분만 그림으로 본다
        F = S.copy()
        for c in np.unique(cls[Hm & art & (cls != k)]):
            cm = ((cls == c) & art).astype(np.uint8)
            _n2, lab2 = cv2.connectedComponents(cm, connectivity=8)
            touch = np.unique(lab2[S & (cm > 0)])
            touch = touch[touch > 0]
            if len(touch):
                F |= np.isin(lab2, touch) & art
        F = cv2.morphologyEx(F.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        kk = max(3, int(round(2.0 * mm)) | 1)       # 판 안 가는 돌기(커튼 끝 등) 떼기
        F = cv2.morphologyEx(F, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk)))
        cnts2, _ = cv2.findContours(F, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        filled = np.zeros_like(F)
        cv2.drawContours(filled, cnts2, -1, 1, -1)
        filled = filled.astype(bool) & (art | Hm)
        r = filled.sum() / A
        if not 0.35 <= r <= 0.92:
            continue
        if (filled & Hm & (cls != k)).sum() < 0.15 * Hm.sum():      # 판이 그림 뒤에 있어야 한다
            continue
        polys = []
        for c in cnts2:
            if cv2.contourArea(c) < (2 * mm) ** 2 or len(c) < 3:
                continue
            p = Polygon((c[:, 0, :] + (x0, y0)).astype(float))
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                polys.append(p)
        if polys:
            return unary_union(polys)
    return None


def classify_cut(cut_poly, design, rgb: np.ndarray, dpi: float):
    """칼선 하나 -> (DECO, None) | (PANEL, 판을 뺀 그림 모양) | (None, None)."""
    mm = float(dpi) / 25.4
    H, W = rgb.shape[:2]
    bx0, by0, bx1, by1 = cut_poly.bounds
    x0, y0 = max(0, int(bx0) - 2), max(0, int(by0) - 2)
    x1, y1 = min(W, int(bx1) + 3), min(H, int(by1) + 3)
    w, h = x1 - x0, y1 - y0
    if w < 3 or h < 3:
        return None, None
    inside = _raster(cut_poly, x0, y0, w, h)
    sub = np.asarray(rgb[y0:y1, x0:x1, :3]).astype(np.int32)
    if w <= 16 * mm and h <= 16 * mm:
        return (DECO, None) if _is_deco(sub, inside, mm) else (None, None)
    if w < 15 * mm or h < 15 * mm or design is None or design.is_empty:
        return None, None
    art = _raster(design.intersection(cut_poly.buffer(1)), x0, y0, w, h) & inside
    if art.sum() < 10:
        return None, None
    fig = _panel_figure(sub, art, mm, x0, y0)
    return (PANEL, fig) if fig is not None else (None, None)


def find(items, rgb: np.ndarray, dpi: float) -> list:
    """items: CutlineResult 목록. Returns [(항목 번호, 칼선 번호, 종류, 판을 뺀 그림 모양 또는 None)]."""
    found = []
    for i, it in enumerate(items):
        cut = it.offsets.get("cut") if it is not None else None
        for j, p in enumerate(_geoms(cut)):
            if p.geom_type != "Polygon":
                continue
            try:
                kind, fig = classify_cut(p, it.design, rgb, dpi)
            except Exception:  # noqa: BLE001 -- 판정이 안 되면 그대로 둔다
                kind, fig = None, None
            if kind:
                found.append((i, j, kind, fig))
    return found


def counts(found) -> tuple:
    return sum(1 for f in found if f[2] == DECO), sum(1 for f in found if f[2] == PANEL)


def _same_gap_cut(old_cut, design, figure):
    """판을 뺀 그림에 예전 칼선과 같은 간격(바깥 +, 안쪽 -)의 칼선."""
    part = design.intersection(old_cut) if design is not None else None
    if part is None or part.is_empty or part.length <= 0:
        return figure
    d = (old_cut.area - part.area) / part.length
    new = figure.buffer(d, join_style=1, quad_segs=16)
    new = new.intersection(old_cut) if d >= 0 else new        # 판을 빼서 작아지기만, 커지지는 않게
    polys = [g for g in _geoms(new) if g.geom_type == "Polygon" and g.area > 1]
    if not polys:
        return None
    return max(polys, key=lambda g: g.area)


def apply(items, found) -> int:
    """미생성으로 고른 결과를 items에 반영(제자리 수정). 바뀐 칼선 수."""
    by_item: dict = {}
    for i, j, kind, fig in found:
        by_item.setdefault(i, {})[j] = (kind, fig)
    changed = 0
    for i, marks in by_item.items():
        it = items[i]
        polys = []
        for j, p in enumerate(_geoms(it.offsets.get("cut"))):
            if j not in marks:
                polys.append(p)
                continue
            kind, fig = marks[j]
            changed += 1
            if kind == DECO:
                continue
            new = _same_gap_cut(p, it.design, fig)
            polys.append(new if new is not None else p)
        it.offsets = dict(it.offsets)
        it.offsets["cut"] = MultiPolygon([p for p in polys if p.geom_type == "Polygon"])
    return changed
