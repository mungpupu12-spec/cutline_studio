"""
The two real, everyday categories of sticker artwork this tool needs to
tell apart -- distinct from the CutlineType (도무송/완칼) shape choice,
though each style has a natural default shape:

  유테 (literally "has a frame/outline" -- renamed from this project's
  earlier "선화" label on 2026-08-26 for a cleaner, self-explanatory
  opposite pair against 무테; same underlying LINE_ART logic, unchanged):
  a character illustration that already has a drawn border/outline stroke
  baked into the artwork itself (the "commercial sticker" look -- a
  consistent-width white or black ring around the shape, e.g. typical
  Sanrio-style goods: each character sits as its own discrete cutout with
  open background space around it). Because that outline is already part
  of the printed pixels, GrabCut segments the WHOLE outlined shape as one
  silhouette; the cutline follows that silhouette, buffered further
  OUTWARD by a small margin. Real standard margin: 1.5mm (reduced from
  this project's earlier general-purpose 2.0mm minimum, which was too
  generous once background color -- not transparency -- is the norm).

  무테 (literally "no frame/outline"): the artist's OWN everyday style --
  painted line art with NO drawn border stroke, usually laid out as a
  continuous pattern that fills its entire printed cell edge-to-edge
  (e.g. a repeating background scene with characters woven through it, no
  individual white-space margin around each one -- see the real "공주토끼와
  딸기" sheet: bunnies distributed across a full plaid background with no
  outline and no gaps). There is no single character silhouette to trace
  at all; the cutline is simply the cell/image's own outer rectangle,
  shrunk INWARD by a small margin -- the opposite direction from 유테.
  Real standard margin: also 1.5mm.

  Quick visual test the artist gave directly: does the artwork have a
  drawn outline ring around each shape? Yes -> 유테. No (her own style) ->
  무테. This is a property of the SOURCE ART, decided before any cutline
  work starts -- core.style_classify's rectangularity check is a
  best-effort automation of this same call from the segmented shape alone,
  not a redefinition of it, and can be wrong exactly when that visual cue
  is present but GrabCut's segmentation doesn't fully capture it (see its
  module docstring for a measured failure case).

Both numbers are real production standards the artist specified directly,
not derived from any particular file's measurements -- treat 1.5mm as the
default for both, but keep it as a parameter since a different vendor/paper
stock could call for a different number (see core.margin_inspector for
measuring what a given vendor's own sample file actually uses).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional

import cv2
import numpy as np
from shapely.affinity import scale as shapely_scale, translate as shapely_translate
from shapely.geometry import MultiPolygon, Polygon, box as shapely_box
from shapely.ops import unary_union

from .cutline_core import (
    _contours_to_polygons,
    auto_supersample,
    mm_to_px,
    px_to_mm,
    smooth_design_naturally,
)
from .multi_design import _content_mask_from_gray, _sever_thin_bridges_for_split
from .segmentation import segment_design_in_region


class ImageStyle(Enum):
    LINE_ART = "유테"     # outlined character on a colored background -- cut OUTWARD around the silhouette
    BORDERLESS = "무테"   # fills its cell edge-to-edge -- cut INWARD from the cell's own rectangle


DEFAULT_STYLE_MARGIN_MM = 1.2

# 2026-09-12(57차, 아래 generate_style_cutline의 halo 판정 참고): content.area
# / rect(사각형 전체).area 비율의 허용 범위. 실제 8개 파일로 직접 측정한
# "지금 이미 정상적으로 추적되고 있지만 예전 gap 기준 때문에 버려지던" 값이
# 0.288~0.822 사이였다(스크래치패드 qa_trace_area_ratio_all8.py로 검증, 커밋
# 안 함) -- 그 범위를 확실히 덮으면서 양쪽 끝에 안전 여유를 둔 값.
MIN_TRUSTED_TRACE_RATIO = 0.12
MAX_TRUSTED_TRACE_RATIO = 0.92
# 2026-09-07 "칼선 좋은 예/나쁜 예" 비교(키스컷 마테 실제 파일 2개, 스크래치
# 패드에서만 분석 후 즉시 삭제): 실루엣 가장자리부터 칼선까지의 실제
# 간격을(이미지 처리로 픽셀 단위 측정 -> mm 환산) 재봤더니
#   - 나쁜 예: 평균 1.72mm, 표준편차 0.64mm (0.29mm~3.79mm까지 들쭉날쭉,
#     특히 목처럼 좁은 오목한 부분에서 간격이 크게 벌어져 값이 크고 흔들림)
#   - 좋은 예: 평균 1.19mm, 표준편차 0.11mm (0.71mm~1.58mm, 좁은 오목한
#     부분까지 포함해서 거의 일정하게 얇고 촘촘하게 실루엣을 따라감)
# 즉 "좋은 칼선"의 기준은 (1) 간격이 작고 (2) 그 무엇보다 실루엣 전체에서
# 얼마나 "일정한가"(변동이 적은가)임. 이 프로젝트의 buffer() 기반 오프셋
# 알고리즘 자체는 합성 도형(다이아몬드/땅콩 모양, 알려진 목 폭)으로 재검증한
# 결과 margin_mm 값과 무관하게 표준편차가 항상 0.03mm 수준으로 이미 매우
# 일정했다(= 나쁜 예처럼 흔들리는 문제는 알고리즘 자체엔 없었음) -- 그래서
# 여기서 바꿀 것은 알고리즘이 아니라 "기본값 숫자" 하나였다: 실제 좋은 예의
# 평균(1.19mm)에 맞춰 기존 1.5mm -> 1.2mm로 낮춤.

# A hard floor under `margin_mm` for this single-margin style path -- direct
# feedback (2026-08-26): the safety/cut/bleed multi-tier path already has a
# floor (core.cutline_core.MIN_GAP_MM) that stops a too-small typed value
# from silently producing an unsafe cutline that print/cut registration slip
# could clip into, but this simpler style path (선화/무테's single margin_mm)
# had no such floor at all -- a free-typed 0 (or near-0) passed straight
# through. This is deliberately NOT the same 2.0mm as MIN_GAP_MM: this
# project has repeatedly measured real, legitimate production margins in
# this exact 1.5mm-and-nearby range (median 1.0-1.7mm across several real
# files, see journal/2026-08-25.md), so reusing MIN_GAP_MM here would
# silently override an already-validated real convention every single time
# the default is used, not just when someone types something dangerously
# small. Instead this floor is set from the smallest margin ever measured
# as a REAL, legitimate value across every file this project has checked --
# text-label elements at 0.55mm -- rounded down slightly for headroom, since
# nothing genuine has ever come in under that. A typed value below this is
# almost certainly a mistake (or 0), not a real, considered choice.
MIN_STYLE_MARGIN_MM = 0.5


def _rect_from_bounds(bounds_px) -> Polygon:
    x0, y0, x1, y1 = bounds_px
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _drop_tiny_parts(geom, min_frac: float = 0.02):
    """가장 큰 조각 대비 min_frac 미만인 극미세 조각(buffer/intersection이
    만드는 잔부스러기)을 버린다 -- 이 모듈의 무테 경로가 원래 쓰던 것과
    같은 기준(2%)."""
    if geom is None or geom.is_empty:
        return geom
    if isinstance(geom, Polygon):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]
    if not parts:
        return Polygon()
    largest = max(g.area for g in parts)
    kept = [g for g in parts if g.area >= max(1e-6, min_frac * largest)]
    return kept[0] if len(kept) == 1 else MultiPolygon(kept)


def _drop_uncuttable_slivers(geom, dpi: float, min_width_mm: float = 0.5, keep_largest: bool = True):
    """여러 조각 중 평균 폭(2x넓이/둘레)이 min_width_mm 미만인 실 같은 조각을
    버린다. keep_largest면 가장 큰 조각은 항상 남기고, 아니면 전부 가늘 때
    빈 Polygon을 돌려준다."""
    if geom is None or geom.is_empty:
        return geom
    if isinstance(geom, Polygon):
        parts = [geom]
    else:
        parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]
    if not parts:
        return geom
    largest = max(parts, key=lambda g: g.area)
    min_w = mm_to_px(min_width_mm, dpi)
    kept = [
        g for g in parts
        if (keep_largest and g is largest) or (g.length > 0 and 2.0 * g.area / g.length >= min_w)
    ]
    if not kept:
        return Polygon()
    return kept[0] if len(kept) == 1 else MultiPolygon(kept)


def _inset_inside_art(content, inset_px: float, open_factor: float = 2.5, cap_factor: float = 0.4):
    """무테 칼선 = "항상 그림 안쪽"을 수학적으로 보장하면서 매끄럽게 만드는
    안쪽 오프셋(2026-09-28, 멍푸: "무테는 이미지 안쪽에 칼선이 들어간다",
    "배경색이 칼선으로 잡히면 안 됨" -- 실제 테스트 파일의 손 칼선도 요소
    실루엣 안쪽 약 0.7~1.5mm로 실측됨).

    매끄럽게 하는 데 "닫기"(buffer(+R)->(-R), 오목한 곳을 원호로 메움)를
    쓰면 R이 여백보다 클 때 원호가 요소 밖 배경 위로 지나간다(실제 파일로
    확인). 안쪽 칼선에는 반대인 "열기"(buffer(-R)->(+R))가 맞다: 톱니·털끝
    같은 작은 요철의 끝만 깎아서 결과가 항상 원래 그림 안에 있다(열기 ⊆
    원본). R은 여백의 2.5배(폭 약 6mm 미만의 뾰족한 끝만 둥글게)로 하되
    조각 크기의 0.4배를 넘지 않게 해 작은 요소가 사라지지 않게 한다. 그다음
    여백만큼 줄이고, 오목한 모서리는 여백 이하 반지름으로만 둥글게 한다
    (이 크기의 닫기는 그림 밖으로 나갈 수 없다)."""
    if content is None or content.is_empty:
        return content
    parts = list(content.geoms) if hasattr(content, "geoms") else [content]
    opened = []
    for g in parts:
        if g is None or g.is_empty:
            continue
        equiv_radius = (g.area / 3.141592653589793) ** 0.5 if g.area > 0 else 0.0
        R = min(open_factor * inset_px, cap_factor * equiv_radius)
        o = g
        if R > 0:
            o = g.buffer(-R, join_style=1).buffer(R, join_style=1)
            if o.is_empty:
                o = g
        opened.append(o)
    if not opened:
        return content
    line = unary_union(opened).buffer(-inset_px, join_style=1)
    r = 0.95 * inset_px
    if not line.is_empty and r > 0:
        line = line.buffer(r, join_style=1).buffer(-r, join_style=1)
    return _drop_tiny_parts(line)


def _fill_art_pockets_from_background_flood(
    image_path: str, design, selection_px, sibling_boxes_px=None, art_region_px=None,
    dpi: float = 300.0,
):
    """GrabCut 실루엣이 요소 안쪽 그림 일부를 배경으로 빼먹은 경우(실측: 매트 위에
    누운 캐릭터 -- 캐릭터 몸이 빠져 U자 모양이 되고, 안쪽으로 줄이면 칼선이 두
    조각으로 갈라짐)를 메운다. 요소 박스 둘레의 배경색에서 채워 들어가 "배경이
    아닌 곳"을 구하고(detect_elements_by_background_flood_px), 그중 실루엣의
    볼록 껍질 안에 있으면서 실루엣과 붙어 있는 부분만 더한다 -- 배경은 절대
    더하지 않고, 옆 요소도 볼록 껍질 밖이면 들어오지 않는다."""
    from .multi_design import detect_elements_by_background_flood_px

    try:
        x0, y0, x1, y1 = selection_px
        m = max(20.0, 0.08 * min(x1 - x0, y1 - y0))
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if img is None:
            return design
        h, w = img.shape[:2]
        if art_region_px is not None:
            # 요소가 들어 있는 칸 전체에서 배경을 채워야 요소 박스 밖으로 나간
            # 연한 테두리 띠도 "그림"으로 잡힌다(박스 둘레만 보면 띠가 배경 취급됨).
            region = tuple(float(v) for v in art_region_px)
        else:
            region = (max(0.0, x0 - m), max(0.0, y0 - m), min(float(w), x1 + m), min(float(h), y1 + m))
        objs = detect_elements_by_background_flood_px(image_path, region, _img=img)
    except Exception:  # noqa: BLE001
        return design
    if not objs:
        return design
    region_edge = shapely_box(*region).exterior.buffer(3.0)

    def _edge_frac(g):
        ext = g.exterior if isinstance(g, Polygon) else unary_union(
            [q.exterior for q in getattr(g, "geoms", []) if isinstance(q, Polygon)]
        )
        return ext.intersection(region_edge).length / max(1e-6, ext.length)

    base_edge = _edge_frac(design) if isinstance(design, Polygon) or hasattr(design, "geoms") else 0.0
    # 2026-09-29(실측): 90도 돌린 칸에서 한쪽 가장자리를 따라 흐르는 배경 띠가
    # 캐릭터에 가려 토막 나면 "배경이 아닌 덩어리"로 잡혀, 캐릭터 칼선에 배경
    # 띠가 통째로 들어갔다. 메운 결과가 칸 가장자리에 더 많이 닿으면(= 가장자리
    # 까지 이어진 배경) 메우지 않는다.
    # 1) 실루엣을 품는 "배경이 아닌 덩어리"가 옆 요소를 건드리지 않고 실루엣보다
    #    조금만 크면(연한 테두리 띠 등 GrabCut이 배경으로 빼먹은 그림 가장자리),
    #    그 덩어리 전체를 그림으로 본다. 실측: 파스텔 시트의 큰 캐릭터 -- 실제
    #    칼선은 연회색 테두리 띠 안에 있는데 GrabCut은 띠를 빼고 몸통만 잡았다.
    # 2026-09-29(멍푸 실제 사용 피드백 "칼선이 부드럽지 않고 너무 좁은 영역"): 작은
    # 소품은 크림색 테두리 띠가 몸통에 비해 넓어(넓이 2배 안팎) 예전 "1.8배 이하"
    # 조건에 걸려 띠를 못 넣고 그림 속 좁은 곳을 잘랐다. 크기 비율 대신 "실루엣에서
    # 4mm 넘게 벗어나지 않는가"로 판단한다(배경이 새어 들어오면 훨씬 멀리 퍼짐).
    comp = max(objs, key=lambda o: o.intersection(design).area)
    reach = 4.0 * dpi / 25.4
    if comp.intersection(design).area > 0.6 * design.area and (
        comp.area <= 1.8 * design.area
        or comp.difference(design.buffer(reach)).area <= 0.02 * comp.area
    ):
        clash = False
        for b in sibling_boxes_px or []:
            bb = shapely_box(*b)
            if bb.area > 0 and comp.intersection(bb).area > 0.1 * bb.area:
                clash = True
                break
        if not clash:
            cand = unary_union([design, comp]).buffer(0)
            if _edge_frac(cand) - base_edge <= 0.03:
                return cand
    hull = design.convex_hull
    fill = unary_union(objs).intersection(hull)
    if fill.is_empty:
        return design
    merged = unary_union([design, fill.buffer(0)])
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    keep = [p for p in parts if p.intersects(design) and p.intersection(design).area > 0.2 * min(p.area, design.area)]
    if not keep:
        return design
    out = unary_union(keep)
    # 메우는 양이 실루엣보다 커지면(배경 판정 실패 의심) 원래대로
    if out.area > 1.6 * design.area:
        return design
    # 볼록 껍질로 잘린 직선 변이 외곽선을 많이 차지하게 되면(실측: 은은한 빛
    # 번짐이 있는 카드 속 캐릭터 -- 칼선이 각진 다각형이 됨) 메우지 않는다.
    def _on_hull(g):
        ring = hull.exterior.buffer(1.5)
        ext = g.exterior if isinstance(g, Polygon) else unary_union([q.exterior for q in g.geoms])
        return ext.intersection(ring).length / max(1e-6, ext.length)

    try:
        if _on_hull(out) - _on_hull(design) > 0.2:
            return design
        if _edge_frac(out) - base_edge > 0.03:
            return design
    except Exception:  # noqa: BLE001
        return design
    return out


def _extend_with_border_band(image_path: str, design, dpi: float, max_w_mm: float = 3.0,
                             tol: float = 8.0):
    """요소 몸통을 둘러싼 한 가지 색의 "테두리 띠"(크림색 번짐·연한 테두리)를
    실루엣에 넣는다.

    2026-09-29(멍푸 실제 사용 피드백 -- 초콜릿·체크무늬 소품·씨앗·꽃에 체크, "칼선이
    부드럽지 않고 너무 좁은 영역을 칼선을 생성했어"): 이런 시트는 요소마다 크림색 띠가
    있고 원본 손 칼선은 몸통에서 약 0.5~1mm 바깥(띠 안)에 있다. 분할 단계가 몸통만
    잡으면 거기서 안쪽으로 줄여 칼선이 그림 속 좁은 곳에 들어갔다.

    몸통 바로 바깥 0.6mm가 거의 한 가지 밝은 색이고, 그 색이 몸통 가장자리 색과
    다르며, max_w_mm 바깥에서는 *다른 색*(= 띠가 끝나고 배경이 시작)일 때만 그 색이
    이어지는 곳까지 넣는다. 실루엣이 이미 띠를 포함하고 있으면 바로 바깥이 배경이고
    멀리까지 같은 색이라 아무것도 하지 않는다(배경으로 번지지 않음)."""
    if design is None or design.is_empty:
        return design
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return design
    H, W = img.shape[:2]
    px = dpi / 25.4
    wmax = max_w_mm * px
    x0, y0, x1, y1 = design.bounds
    pad = wmax + 0.8 * px + 4
    bx0, by0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    bx1, by1 = min(W, int(x1 + pad)), min(H, int(y1 + pad))
    if bx1 - bx0 < 8 or by1 - by0 < 8:
        return design
    lab = cv2.cvtColor(cv2.GaussianBlur(img[by0:by1, bx0:bx1], (3, 3), 0), cv2.COLOR_BGR2LAB).astype(np.float32)

    def _mask(geom):
        m = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
        for p in (geom.geoms if hasattr(geom, "geoms") else [geom]):
            if p.is_empty or p.geom_type != "Polygon":
                continue
            cv2.fillPoly(m, [np.int32([(x - bx0, y - by0) for x, y in p.exterior.coords])], 255)
            for hole in p.interiors:
                cv2.fillPoly(m, [np.int32([(x - bx0, y - by0) for x, y in hole.coords])], 0)
        return m > 0

    body = _mask(design)
    ring0 = _mask(design.buffer(0.6 * px)) & ~body
    edge = body & ~_mask(design.buffer(-0.6 * px))
    far = _mask(design.buffer(wmax + 0.8 * px)) & ~_mask(design.buffer(wmax))
    if ring0.sum() < 30 or edge.sum() < 30 or far.sum() < 30:
        return design
    c_h = np.median(lab[ring0], axis=0)
    if c_h[0] < 190:  # 밝은 띠만(어두운 배경·그림은 띠로 보지 않음)
        return design
    near = np.linalg.norm(lab - c_h, axis=2) <= tol
    if near[ring0].mean() < 0.6:
        return design  # 둘레 대부분이 한 가지 색 띠가 아님
    if near[far].mean() > 0.35:
        return design  # 띠가 끝나지 않음 = 사실상 배경색(이미 띠가 실루엣 안)
    zone = _mask(design.buffer(wmax)) & ~body
    cand = (near & zone).astype(np.uint8)
    seed = cv2.dilate(body.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    n, labels = cv2.connectedComponents(cand, connectivity=8)
    keep = np.zeros(cand.shape, bool)
    for k in range(1, n):
        comp = labels == k
        if (comp & seed).any():
            keep |= comp
    if not keep.any():
        return design
    full = (keep | body).astype(np.uint8) * 255
    full = cv2.morphologyEx(full, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    polys = []
    for c in cs:
        if len(c) >= 3:
            p = Polygon([(float(q[0][0]) + bx0, float(q[0][1]) + by0) for q in c]).buffer(0)
            if not p.is_empty:
                polys.append(p)
    if not polys:
        return design
    out = unary_union(polys + [design])
    return out if out.area > design.area else design


def _light_border_band_width_px(image_path: str, design, dpi: float, tol: float = 10.0) -> float:
    """실루엣 가장자리 안쪽에 한 가지 밝은 색 "테두리 띠"(크림색 번짐 등)가 있으면 그
    평균 폭(px), 없으면 0. 띠 = 가장자리 0.4mm 안쪽 색과 같은 밝은 색이 가장자리에서
    이어지는 부분(둘레의 60% 이상), 그 안쪽 몸통 색과는 뚜렷이 다름."""
    if design is None or design.is_empty:
        return 0.0
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return 0.0
    H, W = img.shape[:2]
    px = dpi / 25.4
    x0, y0, x1, y1 = [int(v) for v in design.bounds]
    bx0, by0, bx1, by1 = max(0, x0 - 4), max(0, y0 - 4), min(W, x1 + 4), min(H, y1 + 4)
    if bx1 - bx0 < 8 or by1 - by0 < 8:
        return 0.0
    lab = cv2.cvtColor(cv2.GaussianBlur(img[by0:by1, bx0:bx1], (3, 3), 0), cv2.COLOR_BGR2LAB).astype(np.float32)

    def _mask(geom):
        m = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
        for p in (geom.geoms if hasattr(geom, "geoms") else [geom]):
            if p.is_empty or p.geom_type != "Polygon":
                continue
            cv2.fillPoly(m, [np.int32([(x - bx0, y - by0) for x, y in p.exterior.coords])], 255)
        return m > 0

    body = _mask(design)
    inner = design.buffer(-0.4 * px)
    if inner.is_empty:
        return 0.0
    edge = body & ~_mask(inner)
    if edge.sum() < 30:
        return 0.0
    c_e = np.median(lab[edge], axis=0)
    if c_e[0] < 190:
        return 0.0
    near = (np.linalg.norm(lab - c_e, axis=2) <= tol) & body
    if near[edge].mean() < 0.6:
        return 0.0
    n, labels = cv2.connectedComponents(near.astype(np.uint8), connectivity=8)
    band = np.zeros_like(near)
    for k in range(1, n):
        comp = labels == k
        if (comp & edge).sum() > 0.05 * edge.sum():
            band |= comp
    perim = design.length if isinstance(design, Polygon) else sum(g.length for g in design.geoms)
    w = float(band.sum()) / max(1.0, perim)
    # 띠가 몸통 대부분을 차지하면(흰 요소 자체 등) 띠가 아님
    if band.sum() > 0.6 * body.sum():
        return 0.0
    return w


def _borderless_inward_from_silhouette(
    image_path: str,
    selection_px: tuple,
    inset_px: float,
    grabcut_margin_px: int,
    supersample: int,
    note_sink: Optional[list],
    sibling_boxes_px: Optional[list],
    precomputed_content_px=None,
    art_region_px=None,
    dpi: float = 300.0,
):
    """자동 인식으로 찾은 낱개 요소(또는 힌트로 보정한 실루엣)의 무테 칼선.

    실제 테스트 파일(작가 손 칼선)과 대조한 결과: 무테 시트는 칸 안의
    캐릭터/소품마다 칼선이 따로 있고, 그 칼선은 요소 실루엣에서 약 0.7~1.5mm
    *안쪽*에 있다(배경은 스티커에 안 들어감). 요소 실루엣은 유테가 이미
    실제 파일로 다듬어 온 GrabCut 경로(옆 요소 침범 조각 제거 + 저대비 번짐
    편입)를 그대로 쓰고 방향만 안쪽(`_inset_inside_art`)으로 한다. Canny 색
    경계 추적은 줄무늬 같은 배경 색 경계까지 그림으로 잡아 쓰지 않는다.

    요소가 영역을 거의 꽉 채우면(카드형) 사각형을 안쪽으로 줄인다. 안쪽으로
    줄이면 사라질 만큼 가는 요소(선 장식 등)는 배경을 자르지 않도록 칼선을
    만들지 않고(빈 도형) 알린다. GrabCut 자체가 실패하면 None(기존 경로로)."""
    rect = _rect_from_bounds(selection_px)
    if precomputed_content_px is not None:
        design = precomputed_content_px
        if note_sink is not None:
            note_sink.append(
                "사람이 직접 확인/보정한 실루엣(트라이맵 힌트)을 기준으로 안쪽으로 줄였습니다."
            )
    else:
        try:
            design = segment_design_in_region(
                image_path, selection_px, margin_px=grabcut_margin_px,
                supersample=supersample, note_sink=note_sink,
            )
        except Exception:  # noqa: BLE001
            return None
        design = _drop_sibling_spillover_fragments(design, sibling_boxes_px)
        design = _grow_design_into_low_contrast_halo_px(
            image_path, design, note_sink=note_sink, sibling_boxes_px=sibling_boxes_px,
            own_box_px=selection_px,
        )
    if design is None or design.is_empty:
        return None
    if precomputed_content_px is None:
        filled = _fill_art_pockets_from_background_flood(
            image_path, design, selection_px, sibling_boxes_px, art_region_px, dpi
        )
        if filled is not design and not filled.is_empty:
            # 메워진 그림이 요소 박스 밖으로 조금 나가면(테두리 띠) 박스로 자르지
            # 않도록 자르는 사각형을 넓힌다(옆 요소와 안 겹침은 위 함수가 확인).
            fx0, fy0, fx1, fy1 = filled.bounds
            rx0, ry0, rx1, ry1 = rect.bounds
            rect = _rect_from_bounds((min(fx0, rx0), min(fy0, ry0), max(fx1, rx1), max(fy1, ry1)))
            if art_region_px is not None:
                rect = rect.intersection(_rect_from_bounds(art_region_px))
        design = filled
        design = _extend_with_border_band(image_path, design, dpi)
    # 요소 박스 가장자리로 실루엣을 자르면 칼선 한쪽이 일자로 잘린다(실측: 모자 쓴
    # 캐릭터 아래쪽 테두리 띠) -- 박스를 3mm 넓혀 자른다(칸 밖으로는 안 나감).
    _m3 = 3.0 * dpi / 25.4
    rb = rect.bounds
    rect = _rect_from_bounds((rb[0] - _m3, rb[1] - _m3, rb[2] + _m3, rb[3] + _m3))
    if art_region_px is not None:
        rect = rect.intersection(_rect_from_bounds(art_region_px))
    design = design.intersection(rect)
    ratio = design.area / rect.area if rect.area > 0 else 0.0
    if ratio >= MAX_TRUSTED_TRACE_RATIO and precomputed_content_px is None:
        if note_sink is not None:
            note_sink.append(
                "무테 칼선: 요소가 영역을 거의 꽉 채워(카드형) 사각형 자체를 기준으로 안쪽으로 줄였습니다."
            )
        return rect.buffer(-inset_px, join_style=2)
    # 2026-09-29(멍푸 실제 사용 피드백 "너무 좁은 영역"): 요소 가장자리에 밝은 테두리
    # 띠(번짐)가 있으면 칼선을 띠 한가운데에 둔다 -- 띠가 좁은데 1.2mm를 그대로 줄이면
    # 칼선이 그림 몸통에 붙어 버렸다(원본 손 칼선은 몸통 약 0.5~1mm 바깥, 띠 안).
    band_w = _light_border_band_width_px(image_path, design, dpi)
    use_inset = inset_px
    if band_w > 0:
        use_inset = max(0.5 * dpi / 25.4, min(inset_px, 0.5 * band_w))
    line = _inset_inside_art(design, use_inset)
    if band_w > 0 and line is not None and not line.is_empty:
        # 테두리 띠가 있는 요소는 꽃잎 사이 같은 오목한 홈을 2mm 반경으로 메워 칼선을
        # 부드럽게 한다 -- 띠(그림) 안에서만(실루엣 0.3mm 안쪽까지) 메우므로 배경은
        # 여전히 자르지 않는다(멍푸: "칼선이 부드럽지 않고").
        R = 2.0 * dpi / 25.4
        smooth = line.buffer(R, join_style=1).buffer(-R, join_style=1)
        smooth = smooth.intersection(design.buffer(-0.3 * dpi / 25.4, join_style=1))
        smooth = _drop_tiny_parts(smooth)
        if smooth is not None and not smooth.is_empty and smooth.area >= line.area:
            line = unary_union([line, smooth])
    if line is None or line.is_empty:
        if note_sink is not None:
            note_sink.append(
                "⚠ 무테: 너무 가는 요소(선·테두리 장식 등)라 그림 안쪽으로 칼선을 넣을 "
                "수 없어, 배경을 자르지 않도록 이 요소는 칼선을 만들지 않았습니다. "
                "필요하면 유테로 따로 추가하세요."
            )
        return Polygon()
    if note_sink is not None:
        note_sink.append(
            "무테 칼선: 요소 실루엣을 따라 그림 안쪽으로 줄였습니다(배경은 자르지 않음)."
        )
    return line


def _drop_sibling_spillover_fragments(design, sibling_boxes_px, min_spillover_ratio: float = 0.3):
    """2026-09-26 피드백("포들은 얼굴에 칼선이 여러개 중첩되어 있고 이
    문제는 지금 한달째 못 고치고 있어"): 실제 파일(너구리+꽃+푸들 시트)로
    직접 재현해서 처음으로 정확한 원인을 찾음.

    segment_design_in_region(GrabCut)은 이 요소 하나만 보고 크롭을 넓게
    (margin_px만큼) 잡아 돌리는데, 크롭이 넓다 보니 바로 옆에 있는 다른
    요소(예: 옆에 딱 붙은 또 다른 흰 꽃)가 우연히 이 요소와 색이
    비슷하면(둘 다 흰 꽃잎) GrabCut이 그 옆 요소의 일부까지 "내 전경"으로
    같이 집어 먹어서, design이 진짜 몸체(큰 조각)와 그 몸체와는 픽셀로
    전혀 안 이어진 작은 조각(옆 요소 영역에 뚝 떨어져 있는 섬 하나)으로
    이루어진 MultiPolygon이 돼버린다(실측: 실제 파일에서 이 작은 조각의
    넓이 2417px^2, 옆 요소의 박스와 겹치는 비율 100%).

    문제는 그 다음 -- 기존에 이미 있던 "sibling_boxes_px 영역은 뺀다"
    안전장치(아래 _grow_design_into_low_contrast_halo_px, 그리고 이 함수를
    부르는 쪽 마지막 단계)가 전부 "내 자신의 selection_px 안쪽만큼은
    sibling과 겹쳐도 절대 안 뺀다"는 반대 안전장치(2026-09-14, 다른 실제
    파일에서 자기 자신의 정당한 가장자리가 잘못 잘려나가는 회귀를 막으려고
    추가됨)를 같이 갖고 있는데, 이번처럼 두 요소의 selection_px 자체가
    서로 겹치는 경우(요소별 bbox는 서로 겹칠 수 있음 -- core.multi_design.
    _merge_overlapping_boxes 문서 참고, 실제 캐릭터 삐죽한 윤곽 때문에
    흔함) 이 작은 섬이 "내 selection_px 안"에도 있고 "옆 요소의 selection_px
    안"에도 동시에 있어서, 그 반대 안전장치가 걸려 절대 안 지워졌다 -- 그
    결과 이 요소도, 옆 요소도 각자 자기 칼선에 이 자리를 포함시켜 같은
    자리에 두 개의 칼선이 겹쳐 보이는 "이중 칼선"이 됨.

    이 함수는 그 특정 사각지대만 정확히 겨냥한다: 한 요소의 결과(design)가
    여러 개의 서로 안 붙은 조각(포함 관계가 아니라 진짜 픽셀로 안 이어진
    섬)으로 이루어져 있을 때만 관여하고(단일 조각이면 완전히 그대로 반환 --
    회귀 위험 없음), 그 중 가장 큰 조각(진짜 몸체)은 절대 건드리지 않으며,
    나머지(더 작은) 조각들 중 옆 요소(sibling)의 박스와 상당 부분(기본
    30% 이상, 실측 수치 100%에 여유를 넉넉히 둔 값) 겹치는 것만 "옆 요소
    것을 잘못 집어온 것"으로 보고 버린다. "내 selection_px 안이라 무조건
    보존"이라는 기존 안전장치보다 더 좁고 구체적인 조건(진짜로 서로 안
    붙은 별개 조각 + 그 조각이 옆 요소 영역과 실제로 크게 겹침)만 보므로,
    한 덩어리로 이어진 내 실루엣이 선택 영역 가장자리에서 sibling과 살짝
    겹치는(2026-09-14가 막으려던) 정상적인 경우는 전혀 건드리지 않는다
    (그 경우는애초에 조각이 1개뿐이라 이 함수의 대상이 아님)."""
    if not sibling_boxes_px:
        return design
    try:
        geoms = list(design.geoms) if hasattr(design, "geoms") else [design]
    except Exception:  # noqa: BLE001
        return design
    if len(geoms) <= 1:
        return design
    try:
        sibling_union = unary_union([shapely_box(*b) for b in sibling_boxes_px])
    except Exception:  # noqa: BLE001
        return design
    geoms_sorted = sorted(geoms, key=lambda g: g.area, reverse=True)
    kept = [geoms_sorted[0]]  # 가장 큰 조각(진짜 몸체)은 무조건 보존
    dropped_any = False
    for g in geoms_sorted[1:]:
        try:
            if g.area <= 0:
                kept.append(g)
                continue
            overlap_ratio = g.intersection(sibling_union).area / g.area
        except Exception:  # noqa: BLE001
            kept.append(g)
            continue
        if overlap_ratio >= min_spillover_ratio:
            dropped_any = True
            continue
        kept.append(g)
    if not dropped_any:
        return design
    if len(kept) == 1:
        return kept[0]
    return unary_union(kept)


def _grow_design_into_low_contrast_halo_px(
    image_path: str, design, note_sink: Optional[list] = None, sibling_boxes_px: Optional[list] = None,
    own_box_px: Optional[tuple] = None,
):
    """유테(LINE_ART) 실루엣(GrabCut 결과, `design`)을, 배경과 색 차이가
    작아 GrabCut이 놓치는 저대비 "헤일로/번짐" 테두리까지 포함하도록
    바깥으로 넓혀준다.

    2026-09-14(좋은 칼선 기준 맞추기, 실제 파일로 발견): 진한 색 캐릭터
    몸통 둘레에 살짝 다른 톤의 부드러운 번짐(halo)이 있고, 그 번짐이 다시
    배경과 아주 비슷한 색으로 이어지는 그림(실측: 실제 시트 파일의 캐릭터,
    배경 크림색(그레이스케일 243) -> 번짐(226) -> 몸통(137))에서, GrabCut이
    번짐을 배경으로 오인해 몸통(코어)까지만 실루엣으로 잡는 경우가 실제로
    확인됐다(실측: 왼쪽 경계가 번짐 시작(약 464px)이 아니라 번짐이 끝나는
    지점(약 504px)에서 잡힘). 그 결과 margin_mm(기본 1.2mm)을 그 위에
    그대로 씌우면, 번짐이 있는 자리는 실제 그림(번짐 포함) 바로 위나 번짐
    한중간에 칼선이 지나가 버려(실측: 칼선 위 점의 배경 대비 색 차이가
    배경(diff~0)이 아니라 번짐 자체(diff~14~18)와 일치) 여백이 실질적으로
    사라지거나 극히 불균일해진다.

    고치는 방법: `core.multi_design`의 박스 확장(`_expand_boxes_for_halo`)과
    같은 원리 -- "이미 확정된 실루엣 바로 주변의 좁은 띠 안에서만, 배경색과
    뚜렷이 다른 픽셀은 전부 실루엣에 편입시킨다". 이렇게 하면 (a) 실루엣과
    멀리 떨어진 순수 배경은 편입 후보에 들지 않고(다른 캐릭터를 잘못
    끌어들일 위험이 없음 -- 이 함수는 이미 GrabCut이 낱개로 분리해 놓은
    캐릭터 하나의 `design` 위에서만 동작하므로 개수/분리 판정에도 전혀
    관여하지 않는다), (b) 실루엣에 바로 붙어있고 배경과는 색이 다른
    픽셀만 편입되므로 실제 배경(그라데이션 등)까지 잘못 편입될 여지가
    거의 없다.

    배경색은 실루엣 bbox 주변의 얇은 테두리에서 "중앙값(median)"으로
    추정한다 -- 평균+표준편차 대신 중앙값+MAD(중앙값 절대편차)를 쓰는 게
    핵심 안전장치: 실제 파일로 확인된 실패 사례(테두리 일부가 이웃 캐릭터의
    장식 무늬(짙은 녹색 체크무늬)에 걸쳐 있어, 평균 기반 추정은 배경색을
    232(진짜 243이어야 함)로, 표준편차를 38(진짜 1~2 수준이어야 함)로
    오염시켜 임계값이 115까지 치솟아 번짐(diff 17)을 전혀 못 잡았음)에서,
    중앙값/MAD는 테두리의 절반 이상이 진짜 배경이기만 하면 소수의 오염
    픽셀에 흔들리지 않고 정확한 배경값을 돌려준다(실측: 같은 자리에서
    중앙값 243, MAD 기반 표준편차 0으로 정확).

    배경 추정 근거(테두리 픽셀 수)가 너무 부족하면(크롭이 이미지 경계에
    바짝 붙어 테두리가 거의 없는 경우 등) 안전하게 확장을 포기하고 원래
    `design`을 그대로 돌려준다.

    2026-09-14(멍푸님 실제 파일 재확인, "칼선이 개체를 안 둘러싸고 여러
    개체를 휘감는다"): 위 색상 상한(halo_upper_bound)만으로는 막지 못하는
    경우가 실측으로 확인됐다 -- 같은 칸 안에 색이 서로 충분히 비슷한 캐릭터
    2개가 나란히 있으면(예: 둘 다 같은 크림색 계열 배경/번짐을 두른 경우),
    이 함수가 "배경과 다르고 halo 범위 안"이라고 본 픽셀이 실제로는 옆
    캐릭터의 halo였다 -- 그 결과 폴리곤 하나가 옆 캐릭터의 박스 영역까지
    통째로 삼켜버렸다(실측: 세트5 파일, 곰+고양이+체크무늬 장식 3개가
    윤곽 1개로 뭉침). `sibling_boxes_px`(같은 칸 안에서 이미 낱개로 인식된
    다른 요소들의 박스, 원본 이미지 절대 좌표)가 주어지면, 그 박스들의
    영역은 아무리 색이 halo처럼 보여도 절대 편입 후보에 넣지 않는다 --
    "내 실루엣 주변의 진짜 배경 번짐"과 "이미 다른 개체로 확정된 영역"을
    색이 아니라 실제 인식 결과로 확실히 구분하는 안전장치.

    2026-09-14(5차, 실제 세트5 파일의 실제 칼선레이어와 대조해 발견 --
    "칼선 모양 자체가 부정확하다", 꽃 장식 IoU 0.358): sibling_boxes_px로
    거르는 게 과했던 사례가 실측으로 확인됐다. 박스 검출이 완벽하게
    타이트하지 않아서(실측: 옆 큰 캐릭터의 박스가 실제 실루엣보다 넓게
    잡혀 이 도안 자신의 박스 한쪽 구석과 겹침) sibling_boxes_px 영역을
    무조건 통째로 제외하면, 그 겹치는 구석은 사실 "내 도안 자신의 정당한
    영역"인데도 잘려나가 칼선이 네모나게 움푹 파인 모양이 됐다(실측:
    도안 폴리곤과 실제 칼선레이어를 겹쳐보니 직선/직각 노치가 뚜렷).
    그래서 `own_box_px`(이 도안 자신의 확정된 박스, 있으면)가 주어지면,
    아무리 sibling_boxes_px와 겹치더라도 own_box_px 안쪽만큼은 절대
    제외 후보에서 뺀다 -- "내 박스 안은 무조건 내 것"이라는 한 방향
    안전장치라서, 이미 검증된 "옆 요소로 안 번진다"는 보장은 그대로
    유지하면서(내 박스 바깥으로는 여전히 못 나감) 내 박스 안쪽을 잘못
    깎아내는 것만 막는다."""
    if design is None or design.is_empty:
        return design
    if sibling_boxes_px:
        # 2026-09-14(실제 파일로 발견): 문제의 진짜 근원은 이 함수의 헤일로
        # 확장이 아니라, 그보다 먼저 실행되는 core.segmentation.segment_
        # design_in_region(GrabCut) 자체가 자기 문맥 여백(grabcut_margin_px)
        # 안에서 옆 요소 쪽으로 이미 침범해 들어간 `design`을 넘겨주는
        # 경우였다(실측: 세트5 파일에서 selection_px=(3943,30,4463,562)인데
        # GrabCut 결과 bounds가 (3903,4)-(4460.75,601.75)로, 이미 옆 칸(고양이,
        # y=464부터) 쪽으로 40px 넘게 들어가 있었음 -- 아래 halo 확장을 아무리
        # 막아도 이미 들어온 `design` 자체는 그대로 반환되므로 소용없었음).
        # 그래서 이 함수에 들어오자마자, 헤일로 판정을 시작하기도 전에 먼저
        # sibling_boxes_px 영역을 design에서 제거한다 -- 어디서 침범이
        # 생겼든(GrabCut 자체든, 아래 헤일로 확장이든) 최종적으로 다른
        # 요소의 박스를 침범한 채로 나가는 일이 없도록 하는 최종 안전장치.
        try:
            forbidden = unary_union([shapely_box(*b) for b in sibling_boxes_px])
            if own_box_px is not None:
                forbidden = forbidden.difference(shapely_box(*own_box_px))
            if not forbidden.is_empty:
                design = design.difference(forbidden)
        except Exception:  # noqa: BLE001
            pass
        if design is None or design.is_empty:
            return design
    # 2026-09-29(멍푸 실제 사용 피드백: 작은 소품 칼선이 "너무 좁은 영역"): 큰 이웃
    # 캐릭터의 박스가 작은 소품의 테두리 띠를 덮고 있으면, 위 규칙 때문에 띠가 박스
    # 경계에서 잘려 칼선이 그림 속으로 들어갔다. 내 실루엣에서 2.5mm 안쪽 띠까지는
    # 이웃 박스 안이어도 넓힐 수 있게 한다(이웃 그림 자체는 색이 달라 안 들어옴 --
    # 겹치는 칼선은 이후 core.cut_check 기준 정리 단계에서 떼어냄).
    allow_zone = design.buffer(30.0)
    try:
        minx, miny, maxx, maxy = design.bounds
        gray_full = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if gray_full is None:
            return design
        H, W = gray_full.shape[:2]

        short_side = min(maxx - minx, maxy - miny)
        band_radius = max(15, min(60, int(round(short_side * 0.1))))
        pad = band_radius + 10

        x0, y0 = max(0, int(minx) - pad), max(0, int(miny) - pad)
        x1, y1 = min(W, int(maxx) + pad), min(H, int(maxy) + pad)
        if x1 <= x0 or y1 <= y0:
            return design
        crop = gray_full[y0:y1, x0:x1]
        ch, cw = crop.shape[:2]

        mask = np.zeros((ch, cw), dtype=np.uint8)
        geoms = list(design.geoms) if hasattr(design, "geoms") else [design]
        for g in geoms:
            if g.is_empty:
                continue
            pts = np.array(
                [[int(round(px - x0)), int(round(py - y0))] for px, py in g.exterior.coords],
                dtype=np.int32,
            )
            cv2.fillPoly(mask, [pts], 255)

        ring = 4
        border = np.zeros((ch, cw), dtype=bool)
        border[:ring, :] = True
        border[-ring:, :] = True
        border[:, :ring] = True
        border[:, -ring:] = True
        bg_pixels = crop[border & (mask == 0)]
        if bg_pixels.size < 20:
            return design

        bg_median = float(np.median(bg_pixels))
        mad = float(np.median(np.abs(bg_pixels.astype(np.float32) - bg_median)))
        thresh = max(8.0, 3.0 * mad * 1.4826)  # 1.4826: MAD -> 정규분포 표준편차 환산 상수
        diff_from_bg = np.abs(crop.astype(np.float32) - bg_median)
        # 2026-09-14(실제 파일로 발견/추가): "배경과 다르기만 하면" 편입시키면,
        # 바로 옆(띠 반경 안)에 있는 완전히 무관한 진한 색 장식(실측: 다른
        # 캐릭터 옆의 짙은 녹색 체크무늬 마스킹테이프)까지 편입해버려 칼선이
        # 그 장식까지 크게 에둘러 감싸는 "선이 방황하는" 회귀가 재현됐다.
        # 번짐(halo)은 배경과 살짝만 다른 저대비 영역이라는 전제 자체를
        # 이용해 위쪽 한계도 둔다 -- 실측: halo diff~17, 캐릭터 몸통(코어,
        # 이미 GrabCut이 잡음) diff~106, 무관한 진한 장식 diff~180 이상.
        # 상한을 넉넉히(80) 잡아 저대비 halo는 전부 포함하면서 확실히 다른
        # 색의 무관한 객체는 배제한다.
        halo_upper_bound = 80.0
        color_looks_like_halo = (diff_from_bg > thresh) & (diff_from_bg < halo_upper_bound)
        color_looks_like_halo = color_looks_like_halo.astype(np.uint8) * 255

        band_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (band_radius * 2 + 1, band_radius * 2 + 1)
        )
        growth_band = cv2.dilate(mask, band_kernel, iterations=1)
        candidate = cv2.bitwise_and(color_looks_like_halo, growth_band)

        # 2026-09-14: sibling_boxes_px(이미 낱개로 확정된 다른 요소들의
        # 박스)가 있으면, 그 영역은 색이 아무리 halo처럼 보여도 편입 후보에서
        # 뺀다 -- 위 함수 docstring의 "sibling_boxes_px" 문단 참고.
        if sibling_boxes_px:
            forbidden = np.zeros((ch, cw), dtype=np.uint8)
            for sb in sibling_boxes_px:
                sx0, sy0, sx1, sy1 = sb
                fx0 = max(0, int(round(sx0)) - x0)
                fy0 = max(0, int(round(sy0)) - y0)
                fx1 = min(cw, int(round(sx1)) - x0)
                fy1 = min(ch, int(round(sy1)) - y0)
                if fx1 > fx0 and fy1 > fy0:
                    forbidden[fy0:fy1, fx0:fx1] = 255
            if own_box_px is not None:
                # 위 docstring 5차 설명 참고 -- 내 박스 안쪽은 sibling과
                # 겹치더라도 후보에서 빼지 않는다(무조건 내 것으로 인정).
                ox0, oy0, ox1, oy1 = own_box_px
                gx0 = max(0, int(round(ox0)) - x0)
                gy0 = max(0, int(round(oy0)) - y0)
                gx1 = min(cw, int(round(ox1)) - x0)
                gy1 = min(ch, int(round(oy1)) - y0)
                if gx1 > gx0 and gy1 > gy0:
                    forbidden[gy0:gy1, gx0:gx1] = 0
            # 내 실루엣 2.5mm 띠 안은 이웃 박스와 겹쳐도 번짐으로 인정(위 9/29 주석)
            az = np.zeros((ch, cw), dtype=np.uint8)
            for g in (allow_zone.geoms if hasattr(allow_zone, "geoms") else [allow_zone]):
                if g.is_empty or g.geom_type != "Polygon":
                    continue
                cv2.fillPoly(az, [np.int32([(px - x0, py - y0) for px, py in g.exterior.coords])], 255)
            forbidden[az > 0] = 0
            candidate = cv2.bitwise_and(candidate, cv2.bitwise_not(forbidden))

        grown = cv2.bitwise_or(mask, candidate)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        grown = cv2.morphologyEx(grown, cv2.MORPH_CLOSE, close_kernel)

        # 2차 안전장치: 위 색 상한을 넣었어도 무관한 장식이 halo와 색이
        # 우연히 비슷하면서 진짜로 맞닿아 있으면(배경 틈이 전혀 없음) 여전히
        # 하나로 이어질 수 있다 -- 그런 극단적인 경우에 대비해, 이번에
        # 넓어진 면적이 원래 GrabCut 실루엣의 3배를 넘으면(정상적인 halo는
        # 원래 실루엣 둘레 * 번짐 폭 정도라 이 정도로 크게 불어나지 않음)
        # 넓히기 자체를 포기하고 원래 design을 그대로 쓴다.
        if int((grown > 0).sum()) > int((mask > 0).sum()) * 3:
            return design

        grown_polys = _contours_to_polygons(grown, simplify_tol_px=0.6)
        grown_polys = [shapely_translate(p, xoff=x0, yoff=y0) for p in grown_polys]
        if not grown_polys:
            return design
        grown_shape = unary_union(grown_polys + [design])  # 원래 design보다 작아지는 일은 없도록 합집합
        if grown_shape.is_empty:
            return design
        if grown_shape.area > design.area * 1.01 and note_sink is not None:
            note_sink.append(
                "유테 칼선: 배경과 색 차이가 작은 저대비 번짐 테두리를 실루엣에 포함해 넓혔습니다."
            )
        return grown_shape
    except Exception:  # noqa: BLE001
        return design


def _detect_inside_outer_ring_px(
    image_path: str, design, band_step_px: float = 4.0,
    sibling_boxes_px: Optional[list] = None, own_box_px: Optional[tuple] = None,
):
    """2026-09-26(멍푸 피드백, 실제 파일의 햄스터 도안으로 확인):
    "칼선 작업이 어려우니까 아웃라인을 만들어 칼선을 쉽게 제작하게 하는
    가이드"로, 작가가 캐릭터/장면 바깥에 일부러 두껍고 단색인(흰색/베이지
    등) 둥근 테두리를 그려 넣어둔 실제 도안이 있음이 실측으로 확인됐다
    (질문/답변으로 직접 확인: "흰색 테두리 안쪽 경계에서 자름 -- 테두리
    자체는 잘려나감"). 이런 테두리는 손으로 칼선을 쉽게 따라 그리라고
    넣어둔 것뿐이라, 지금처럼 그 테두리까지 통째로 감싸고 margin_mm만큼
    또 바깥으로 미는 기존 유테 기본 동작을 그대로 적용하면 정확히 그녀가
    싫어하는 "가이드를 그대로 칼선으로 쓰는" 결과가 나온다.

    2026-09-26(1차 구현, 실제 파일로 검증하다가 폐기): 처음에는 GrabCut이
    돌려준 `design`의 경계 자체를 테두리 바깥선으로 믿고, 그 경계에서 안쪽
    으로 들어가며 두께만 재는 방식(`_detect_outer_ring_inset_px`, 삭제됨)
    으로 만들었다. 실제 햄스터 도안으로 돌려보니 항상 0을 돌려줬다 --
    원인을 직접 마스크/오버레이로 찍어보니(스크래치패드 `_ring_debug_
    overlay.png`/`_ring_debug_overlay2.png`) GrabCut의 `design` 경계가
    선택 영역(selection_px)이나 halo 확장 여부에 따라 진짜 테두리보다 안쪽
    (캐릭터 자신의 털 경계)이나 바깥쪽(테두리를 지나 배경까지)으로 들쭉날쭉
    벗어나 있었다 -- 채도가 강한 배경 위의 두꺼운 단색 테두리라는 이 장면
    자체가 GrabCut에게 어려운 케이스였다.

    그래서 이번에는 `design`을 경계의 "위치 힌트(hint)"로만 쓰고, 진짜
    바깥 경계는 이 함수가 직접 다시 구한다 -- 크롭 테두리 픽셀의 중앙값을
    배경색으로 잡고, 그 배경색과의 색 거리(threshold)로 전경/배경을 가른
    뒤 `cv2.connectedComponentsWithStats`로 성분을 나누고 `design`의
    중심점이 속한 성분(없으면 가장 큰 성분)을 고른다 -- 이 방식으로 구한
    경계는 실측으로 확인된 진짜 테두리 바깥선과 정확히 일치했다(스크래치
    패드 `_ring_colorbased.png`: 노치 없는 단일 깨끗한 윤곽, 진짜 테두리
    자리와 정확히 겹침). 그 위에서 `_detect_outer_ring_inset_px`와 같은
    "경계에서 안쪽으로 들어가며 얼마나 두껍게 단색이 이어지는가"를 재는
    로직을 그대로 적용한다.

    보통의 유테 캐릭터는 경계 바로 안쪽이 (귀/무늬 등 때문에) 색이 금방
    바뀌거나 애초에 배경과 다른 뚜렷한 색 경계 하나뿐이라, 그게 뚜렷하게
    두꺼운 경우에만(=의도적으로 그려 넣은 가이드 테두리일 가능성이 높을
    때만) 안쪽 경계 폴리곤을 돌려준다 -- 평범한 캐릭터는 첫 밴드부터
    단색이 아니므로 None을 돌려줘 기존 동작이 전혀 안 바뀐다(회귀 없음).

    스칼라 두께 대신 최종 폴리곤을 직접 돌려주는 이유: 두께 하나만 넘겨서
    호출부가 `design.buffer(-두께)`를 다시 계산하게 하면, 애초에 부정확한
    `design` 경계 위에 또 오차를 쌓는 셈이 된다 -- 이 함수가 직접 구한
    정확한 경계 마스크를 그 두께만큼 침식(erode)한 뒤 그 결과를 그대로
    돌려주는 게 오차를 한 번만 겪는다.

    2026-09-26(2차, 실제 8칸 시트 파일 전체로 재확인하다가 발견 -- "여러
    도안이 다닥다닥 붙어있으면 배경색 추정 자체가 오염된다"): 위 배경색
    추정(크롭 테두리 픽셀의 중앙값)은 도안들이 서로 충분히 떨어져 있다는
    전제였는데, 실제 이 8칸 시트는 이웃 도안 사이 간격이 실측 10px 수준
    으로 매우 좁아서, 테두리까지 확보하려는 pad(구조상 수십~백px)가 옆
    도안의 몸통 전체를 크롭 안에 끌어들였다(실측: bg_color가 순수 배경
    (BGR 대략 (170,170,60) 계열의 청록색)이 아니라 옆 도안 색과 섞인 값으로,
    MAD가 0~수준이 아니라 80까지 치솟음 -- 그 결과 threshold가 지나치게
    커져 fg가 통째로 0이 되거나, 반대로 옆 도안까지 한 성분으로 잡혀 버림).
    `_grow_design_into_low_contrast_halo_px`가 이미 같은 문제를 `sibling_
    boxes_px`(같은 칸에서 이미 낱개로 확정된 다른 요소들의 박스)로 풀어둔
    것과 같은 방식을 그대로 재사용한다 -- 배경색 추정용 테두리 샘플과 최종
    전경(fg) 마스크 양쪽 모두에서 sibling_boxes_px 영역(단, own_box_px
    안쪽은 항상 예외)을 미리 제외해, 옆 도안이 내 테두리 판정에 섞여
    들어오지 못하게 한다.

    Returns a shapely Polygon/MultiPolygon for the ring's inner boundary
    (already in the source image's own pixel coordinates) if a solid-color
    band of meaningful width is found running all the way around the
    design's real outer edge, else None (meaning: no ring, caller should
    keep its existing behavior)."""
    if design is None or design.is_empty:
        return None
    try:
        minx, miny, maxx, maxy = design.bounds
        short_side = min(maxx - minx, maxy - miny)
        # 안전장치(실제 파일 전체를 훑다가 발견 -- 다른 원인으로 이미 비정상
        # 인/퇴화된(degenerate) 아주 얇은 조각(실측: 폭 33px짜리 가늘고 긴
        # 선 하나가 박스 인식 단계의 다른 문제로 하나의 "도안"인 것처럼
        # 들어온 경우)이 들어오면, 2D 고리 모양을 가정하는 이 함수의 계산이
        # 의미 없는(진짜 가이드 테두리와 무관한) 결과를 만들어낼 수 있다.
        # 진짜 가이드 테두리는 캐릭터/장면 하나를 통째로 두르는 것이라
        # 이 정도로 작을 수 없으므로, 최소 크기 미달이면 애초에 시도하지
        # 않는다.
        MIN_DESIGN_SHORT_SIDE_PX = 60.0
        if short_side < MIN_DESIGN_SHORT_SIDE_PX:
            return None
        max_depth = min(short_side * 0.35, 80.0)  # 도안을 통째로 먹어버리지 않도록 상한
        if max_depth < band_step_px * 2:
            return None

        bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        H, W = bgr.shape[:2]
        # design bounds는 위치 힌트일 뿐 진짜 테두리보다 안쪽일 수 있으므로,
        # 진짜 테두리 바깥의 순수 배경까지 크롭 안에 확실히 들어오도록
        # max_depth보다 넉넉히 더 여유를 둔다.
        SEARCH_MAX_PX = max_depth + 30.0  # 배경을 확실히 지나칠 때까지 바깥으로 걸어나갈 한계
        pad = int(SEARCH_MAX_PX) + 20
        x0, y0 = max(0, int(minx) - pad), max(0, int(miny) - pad)
        x1, y1 = min(W, int(maxx) + pad), min(H, int(maxy) + pad)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = bgr[y0:y1, x0:x1].astype(np.float32)
        ch, cw = crop.shape[:2]

        # 옆 도안이 이 크롭 안까지 들어와 있으면(실제 촘촘한 시트에서 흔함)
        # 배경색 추정과 전경(fg) 판정 양쪽에서 그 영역을 미리 제외한다 --
        # own_box_px(내 것으로 이미 확정된 영역) 안쪽만은 항상 예외로 남긴다
        # (위 docstring "2026-09-26(2차)" 문단 참고).
        excluded = np.zeros((ch, cw), dtype=bool)
        if sibling_boxes_px:
            for sb in sibling_boxes_px:
                sx0, sy0, sx1, sy1 = sb
                fx0 = max(0, int(round(sx0)) - x0)
                fy0 = max(0, int(round(sy0)) - y0)
                fx1 = min(cw, int(round(sx1)) - x0)
                fy1 = min(ch, int(round(sy1)) - y0)
                if fx1 > fx0 and fy1 > fy0:
                    excluded[fy0:fy1, fx0:fx1] = True
            if own_box_px is not None:
                ox0, oy0, ox1, oy1 = own_box_px
                gx0 = max(0, int(round(ox0)) - x0)
                gy0 = max(0, int(round(oy0)) - y0)
                gx1 = min(cw, int(round(ox1)) - x0)
                gy1 = min(ch, int(round(oy1)) - y0)
                if gx1 > gx0 and gy1 > gy0:
                    excluded[gy0:gy1, gx0:gx1] = False

        own_mask = np.zeros((ch, cw), dtype=np.uint8)
        geoms = list(design.geoms) if hasattr(design, "geoms") else [design]
        for g in geoms:
            if g.is_empty or not isinstance(g, Polygon):
                continue
            pts = np.array(
                [[int(round(px - x0)), int(round(py - y0))] for px, py in g.exterior.coords],
                dtype=np.int32,
            )
            cv2.fillPoly(own_mask, [pts], 255)
        if int((own_mask > 0).sum()) == 0:
            return None

        # 2026-09-26(3차, 실제 8칸 시트 전체로 재확인하다가 발견 -- "크롭
        # 가장자리를 배경 표본으로 쓰면 인쇄 영역 바깥의 검은 여백까지
        # 오염됨"): 도안이 인쇄판 모서리에 가까이 있을 때(실측: 시트 맨 위
        # 줄 첫 도안) 크롭 가장자리 전체가 실제 이웃 배경이 아니라 인쇄
        # 영역 바깥의 새까만 여백에 더 가깝게 닿아버려(그 여백 픽셀이 배경
        # 표본의 절반 이상을 차지해 배경색 추정치가 청록색이 아니라 새까맣게
        # (0,0,0) 나옴) 전경 판정 임계값이 완전히 어긋났다.
        #
        # 4차(실측으로 폐기): `design` 경계에서 바깥으로 걸어나가며 "몇 단계
        # 연속 단색이면 그 겹을 배경으로 인정"하는 방식도 시도했으나,
        # `design`의 경계 자체가 GrabCut 기반이라 원래 계단식으로 들쭉날쭉해
        # (이 문서 "1차 구현" 문단 참고) 어느 반경에서도 밴드 안에 테두리
        # 색과 배경색이 섞여 들어가 "단색" 판정 자체가 거의 항상 실패했다
        # (실측: 여러 skip 값으로도 frac_uniform이 0.6~0.8대에 머물러 절대
        # 0.82를 못 넘김).
        #
        # 그래서 5차: 정밀한 "밴드가 단색인가"가 아니라, 더 단순하고 훨씬
        # 잡음에 강한 "이 영역에서 제일 흔한 색이 뭔가"(최빈값)로 바꿨다 --
        # `design` 주변 좁지 않은 고리(20px~110px, 실측으로 확인된 실제
        # 테두리 두께 범위를 충분히 덮으면서도 크롭 가장자리처럼 멀리
        # 있는 인쇄판 여백/이웃까지는 닿지 않는 폭)를 통째로 표본으로 삼고,
        # 그 안에서 8단계로 양자화한 색 히스토그램의 최빈 구간을 배경으로
        # 본다. 진짜 배경은 이 고리 전체에서 "한없이 넓은 바깥쪽 영역"을
        # 차지하는 반면, 테두리(있다면)는 자기 두께만큼만, 여백/이웃 오염은
        # 고리의 일부 방향에서만 나타나므로, 어느 쪽도 진짜 배경의 압도적인
        # 픽셀 수를 이기지 못한다(실측: 실제 8칸 전부에서 -- 여백에 가장
        # 가까운 경우까지 포함해 -- 최빈값이 항상 진짜 청록 배경과 정확히
        # 일치했음, 점유율 0.39~0.99).
        # 2026-09-26(6차, 실제 파일을 여러 번 다시 돌려보다가 발견 -- "고정
        # 반경 20px~110px 고리가 어떤 실행에서는 진짜 배경 대신 테두리
        # 자체를 최빈값으로 잡음"): `design`(GrabCut+halo 결과) 자체가 실행
        # 마다 살짝 다르게 나올 수 있어(예: 같은 입력인데도 GrabCut 내부
        # 난수 등으로 폭/높이가 366x348 -> 377x393처럼 달라짐), 테두리가
        # 유난히 두꺼운 경우 고정된 20px~110px 고리가 거의 전부 테두리
        # 안쪽에 걸려버려 최빈값이 흰 테두리 색 자체가 되는 사례가 실측으로
        # 재현됐다(실측: 물고기어항 도안, bg_color가 청록 대신 순백(255,255,
        # 255)으로 나옴).
        #
        # 그래서 안쪽 반경을 하나로 고정하지 않고 여러 지점(20, 50, 80px)
        # 에서 각각 최빈값을 구해, 마지막 두 지점(50, 80px)의 결과가 서로
        # 일치할 때만 그 값을 진짜 배경으로 믿는다 -- 진짜 배경은 고리를
        # 얼마나 안쪽에서 시작하든 항상 같은 색으로 나오는 반면, 테두리
        # 안쪽에 걸린 잘못된 표본은 안쪽 반경을 조금만 더 밀어도(테두리를
        # 벗어나면) 다른 색으로 바뀌므로, 이 불일치 자체가 신호가 된다.
        # 2026-09-26(9차, 실제 파일을 "진짜" 최종 해상도로 다시 검증하다가
        # 발견 -- "그동안 검증에 쓴 래스터가 실제 앱이 쓰는 최종 해상도가
        # 아니었음"): .ai 파일을 열 때 실제 앱은 load_ai_as_raster로 1차
        # 래스터를 만든 뒤, 반복 패턴 시트(이 햄스터 파일 포함)는 곧바로
        # load_real_grid_cells가 같은 경로를 자기 방식대로(PyMuPDF 페이지
        # 픽스맵) 다시 렌더링해 덮어쓴다(gui/app.py `_run_load_ai` 참고) --
        # 이번 세션 중간에 이 사실을 모르고 "1146x1571이 진짜"라고 잘못
        # 결론 내려 파일을 그 크기로 되돌렸었는데, 실제 코드 경로를 그대로
        # 재현해보니 최종적으로는 항상 5494x3730로 다시 덮어써진다(재현성
        # 확인됨, 2회 실행 결과 바이트까지 동일) -- 즉 5494x3730 쪽이 진짜다.
        # 이 진짜 해상도로 다시 돌려보니 8개 중 4개(빵도그/해변/쪽지/어항)가
        # 배경색 추정 실패로 걸렸다 -- 원인을 직접 크롭으로 찍어보니(스크래치
        # 패드 `_true_res_box0_wide_crop.png`) 시트 가장자리에 가까운 도안은
        # 진짜 해상도에서 인쇄판 바깥 새까만(0,0,0) 여백까지의 실제 거리가
        # (도안 크기와 무관하게 인쇄 레이아웃 자체가 정하는) 고정 여백이라,
        # 이 함수의 탐색 반경(SEARCH_MAX_PX)이 도안 크기에 비례해 커지면
        # 오히려 그 여백을 더 쉽게 건드리게 된다. 그 결과 최빈값 표본에 여백
        # 픽셀이 섞여 들어와 배경색이 청록 대신 새까맣게 나오거나(box0/2/4),
        # 반경별로 다른 색이 나와(box1) "여러 반경이 서로 일치해야 믿는다"는
        # 기존 안전장치(6차)에 걸려 통째로 포기해버렸다.
        #
        # 근본 원인은 "탐색 반경"이 아니라 "여백 자체가 표본에 섞이는 것"이므로,
        # 반경을 이리저리 바꾸는 대신 표본에서 여백 픽셀 자체를 미리 걸러낸다 --
        # 이 시트의 실제 배경/테두리/캐릭터 색은 전부 밝고 채도 있는 파스텔
        # 톤이고, 인쇄판 바깥 여백만 순수에 가까운 검정(0,0,0)이므로(문서
        # "3차" 문단 참고, 실측으로 이미 확인된 특성) "채널 전부가 아주 어두운"
        # 픽셀만 배경 후보에서 제외해도 실제 배경/테두리 색은 전혀 안 다치면서
        # 여백만 걸러진다. 이렇게 하고 나니 6차의 "반경별 일치 확인"이 잡으려던
        # 문제 자체가 사라져(그 안전장치가 실은 이 오염을 우회한 것일 뿐,
        # 진짜 원인은 아니었음) 여러 반경 대신 단일 넓은 고리로 되돌려도(5차
        # 원안) 8개 전부 안정적으로 재현됨을 실측으로 확인했다.
        VOID_BLACK_MAX_CHANNEL = 30.0

        def _mode_bg_color(inner_r: int):
            k_in_ = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (inner_r * 2 + 1, inner_r * 2 + 1))
            d_in_ = cv2.dilate(own_mask, k_in_, iterations=1)
            annulus_ = (d_out > 0) & (d_in_ == 0) & (~excluded)
            pix = crop[annulus_]
            not_void = pix.max(axis=1) > VOID_BLACK_MAX_CHANNEL if pix.shape[0] else pix
            pix = pix[not_void] if pix.shape[0] else pix
            if pix.shape[0] < 50:
                return None
            q_ = np.floor(pix / QUANT_STEP).astype(np.int64)
            keys_ = q_[:, 0] * 100000 + q_[:, 1] * 1000 + q_[:, 2]
            uk_, cnt_ = np.unique(keys_, return_counts=True)
            tk_ = uk_[int(np.argmax(cnt_))]
            tb_ = (tk_ // 100000) * QUANT_STEP + QUANT_STEP / 2.0
            tg_ = ((tk_ // 1000) % 100) * QUANT_STEP + QUANT_STEP / 2.0
            tr_ = (tk_ % 1000) * QUANT_STEP + QUANT_STEP / 2.0
            center_ = np.array([tb_, tg_, tr_], dtype=np.float32)
            close_ = np.linalg.norm(pix - center_, axis=1) < 20.0
            if int(close_.sum()) < 20:
                return None
            return np.median(pix[close_], axis=0)

        QUANT_STEP = 8.0
        ANNULUS_OUTER_PX = SEARCH_MAX_PX
        k_out = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (int(ANNULUS_OUTER_PX) * 2 + 1, int(ANNULUS_OUTER_PX) * 2 + 1)
        )
        d_out = cv2.dilate(own_mask, k_out, iterations=1)
        ANNULUS_INNER_PX = 20
        bg_color = _mode_bg_color(ANNULUS_INNER_PX)
        if bg_color is None:
            return None
        bg_pixels_for_mad = crop[(d_out > 0) & (cv2.dilate(own_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ANNULUS_INNER_PX * 2 + 1, ANNULUS_INNER_PX * 2 + 1)), iterations=1) == 0) & (~excluded)]
        not_void_mad = bg_pixels_for_mad.max(axis=1) > VOID_BLACK_MAX_CHANNEL if bg_pixels_for_mad.shape[0] else bg_pixels_for_mad
        bg_pixels_for_mad = bg_pixels_for_mad[not_void_mad] if bg_pixels_for_mad.shape[0] else bg_pixels_for_mad
        close_to_mode = np.linalg.norm(bg_pixels_for_mad - bg_color, axis=1) < 20.0
        mad = float(np.median(np.abs(bg_pixels_for_mad[close_to_mode] - bg_color))) if close_to_mode.any() else 0.0
        thresh = max(25.0, mad * 6.0)

        dist = np.linalg.norm(crop - bg_color, axis=2)
        fg = (dist > thresh).astype(np.uint8) * 255
        if excluded.any():
            fg[excluded] = 0
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, close_kernel)
        # 2026-09-26(6차, 실제 8칸 시트 전체로 재확인하다가 발견 -- "인쇄판
        # 위쪽 여백과 도안이 좁은 목으로 이어져 하나의 거대한 성분으로
        # 합쳐짐"): 시트 위쪽 줄처럼 도안이 인쇄 영역 가장자리에 아주
        # 가까우면, 여백(배경과도 다른 색)과 도안의 테두리가 진짜로 아주
        # 좁은 목(실측: 폭 몇 px)으로 맞닿아 하나의 연결 성분이 돼버렸다.
        # `core.multi_design._sever_thin_bridges_for_split`가 이미 같은
        # 모양의 문제(장식선이 여러 캐릭터를 얇은 다리로 잇는 경우)를 풀려고
        # 만든 함수라 그대로 재사용한다 -- 실제 두께가 있는 도안/테두리
        # 자체는 오프닝에도 거의 그대로 남고, 그보다 훨씬 얇은 다리만
        # 끊어지며, 내부 안전장치(끊은 뒤 90% 미만으로 줄면 되돌림, 성분
        # 개수가 그대로면 되돌림)가 있어 다리가 없는 평범한 경우엔 아무
        # 영향이 없다.
        fg = _sever_thin_bridges_for_split(fg)

        n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(fg, connectivity=8)
        if n_labels <= 1:
            return None
        cxi = int(round(float(design.centroid.x) - x0))
        cyi = int(round(float(design.centroid.y) - y0))
        chosen = 0
        if 0 <= cyi < ch and 0 <= cxi < cw:
            chosen = int(labels[cyi, cxi])
        if chosen == 0:
            # 힌트 지점이 배경으로 판정된 경우(도안 중심 근처가 저채도 등)
            # 가장 큰 성분을 대신 사용.
            areas = stats[1:, cv2.CC_STAT_AREA]
            chosen = 1 + int(np.argmax(areas))
        mask = (labels == chosen).astype(np.uint8) * 255
        mask_area0 = int((mask > 0).sum())
        if mask_area0 == 0:
            return None
        # 안전장치(7차, 실측으로 발견 -- "인쇄판 여백과 좁은 목으로 이어져
        # 하나의 거대한 성분이 됨"): 시트 가장자리에 아주 가까운 도안은
        # 여백과 진짜로 넓게 맞닿을 수 있어(디자인 자체가 인쇄 영역 경계를
        # 따라 잘린 경우 -- 얇은 다리가 아니라 굵게 이어짐) 위 severing으로도
        # 못 끊는다. 이런 경우 선택된 성분이 "도안+테두리가 차지할 수 있는
        # 최대 넓이"(자기 bbox를 max_depth만큼 사방으로 넉넉히 부풀린 넓이)
        # 를 훌쩍 넘어서므로, 그 상한을 넘으면 뭔가 다른 영역과 섞였다는
        # 뜻으로 보고 조용히 포기한다(기존 동작 유지) -- 잘못된 큰 도형을
        # 만드는 것보다 안전하다.
        expected_w = (maxx - minx) + 2.0 * max_depth
        expected_h = (maxy - miny) + 2.0 * max_depth
        expected_max_area = expected_w * expected_h * 1.5
        if mask_area0 > expected_max_area:
            return None
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)

        # 2026-09-26: 실제 파일로 검증하다가 발견 -- 순수 표준편차(std)는 이
        # 둘레 밴드 중 극히 일부만(예: 경계 한쪽 모서리의 국소적 잡음) 배경/
        # 다른 색이 섞여도 전체 평균이 확 튀어서, 진짜로는 90% 넘게 단색인
        # 밴드까지 "단색 아님"으로 오판했다. 그래서 "밴드 전체가 완벽히
        # 단색인가"가 아니라 "밴드 픽셀의 절대다수가 서로 비슷한 색인가"
        # (중앙값 기준 최빈 색과의 거리)로 바꿔, 그런 국소적 잡음 한두 군데는
        # 무시하고 판단한다.
        UNIFORM_DIFF_PX = 12.0  # 이 안이면 "중앙값과 같은 색"으로 간주
        UNIFORM_FRAC_MIN = 0.82  # 밴드 픽셀의 이 비율 이상이 같은 색이어야 "단색 밴드"
        COLOR_DRIFT_MAX = 16.0  # 첫 밴드 대표색 대비 이만큼 벌어지면 "진짜 내용물 시작"
        MIN_RING_PX = 8.0  # 이보다 얇으면 그냥 보통 잉크 테두리/안티앨리어싱으로 보고 무시
        # 2026-09-26: 실제 8칸 시트 전체로 돌려보다가 발견 -- 배경색 거리
        # 임계값으로 만든 `mask`는 진짜 테두리 위치 자체는 정확했지만(스크래치
        # 패드 오버레이로 직접 픽셀 단위 확인), 이진화 특성상 경계선 자체가
        # 계단식으로 살짝 들쭉날쭉하다 -- 그 결과 경계에서 곧바로 시작하는
        # 첫 밴드(0~4px)는 진짜 단색 테두리인데도 이 계단 잡음 때문에
        # frac_uniform이 낮게(실측: 0.61) 나와 "테두리 없음"으로 오판했다.
        # 안쪽으로 SKIP_PX만큼 먼저 침식해 이 경계 잡음 구간을 건너뛰고 나면
        # 같은 자리가 즉시 거의 완벽히 단색으로 측정됐다(실측: skip 2px에서
        # frac_uniform 0.997). 이 SKIP_PX 구간도 실제로는 테두리의 일부이므로
        # (테두리보다 얇을 리 없는 잡음 구간일 뿐) 최종 두께에는 그대로
        # 포함시킨다.
        SKIP_PX = 2
        if SKIP_PX > 0:
            skip_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SKIP_PX * 2 + 1, SKIP_PX * 2 + 1))
            start_mask = cv2.erode(mask, skip_kernel, iterations=1)
        else:
            start_mask = mask
        if int((start_mask > 0).sum()) == 0:
            return None

        n_bands = max(2, int(max_depth // band_step_px))
        prev_mask = start_mask
        ref_color = None
        thickness = 0.0  # SKIP_PX 이후로 측정된 두께 -- 최종 합산 시 SKIP_PX를 더함
        for k in range(1, n_bands + 1):
            depth = k * band_step_px
            kernel_px = int(round(depth))
            if kernel_px < 1:
                continue
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px * 2 + 1, kernel_px * 2 + 1))
            eroded = cv2.erode(start_mask, kernel, iterations=1)
            band = (prev_mask > 0) & (eroded == 0)
            pixels = crop[band]
            if pixels.shape[0] < 20:
                break
            band_median = np.median(pixels, axis=0)
            diffs = np.abs(pixels - band_median).mean(axis=1)
            frac_uniform = float((diffs < UNIFORM_DIFF_PX).mean())
            is_uniform = frac_uniform >= UNIFORM_FRAC_MIN
            if ref_color is None:
                if not is_uniform:
                    return None  # 경계 바로 안쪽부터 단색이 아님 -- 보통 캐릭터, 그대로 종료
                # 2026-09-26(8차, 실제 파일 전수 스트레스 테스트 중 발견 -- "안내용
                # 테두리가 아닌데도 걸림(오탐)"): 한 실제 파일(라쿤+꽃+푸들) 시트의 라쿤
                # 캐릭터는 자기 몸(진회색) 바깥에 부드러운 연회색 헤일로/그림자를
                # 두르고 있는데, 이 헤일로도 "배경과 다른 단색 고리"라는 조건은
                # 그대로 만족해 위 로직이 안내용 테두리로 오판했다 -- 실측 결과
                # 실제 결과 이미지는 라쿤의 귀까지 잘라내는(실제 내용을 잘라먹는)
                # 심각한 오류였다. 처음엔 "경계 선명도"(부드러운 헤일로 vs 벡터
                # 하드엣지)로 구분하려 했으나, 깊이 침식한 기준색이 캐릭터 자기
                # 몸통 색으로 섞여버려 판별이 안 됐다(라쿤 mid_frac 0.06~0.11,
                # 정상 케이스와 구분 안 됨).
                #
                # 대신 그녀가 이 기능을 처음 설명할 때 직접 쓴 표현 자체("흰색
                # 테두리 안쪽 경계에서 자름")에 이미 답이 있었다 -- 실측으로 직접
                # 픽셀을 찍어보니 실제 8칸 시트의 진짜 안내용 테두리 7개는 전부
                # (255,255,255)/(243,250,253)류의 순백에 가까운 색인 반면, 라쿤의
                # 헤일로는 (222,222,223) -- 눈에는 밝아 보여도 순백보다 뚜렷하게
                # 어둡다. 그래서 "이 첫 밴드의 대표색이 흰색에 가까운가"를 안내용
                # 테두리 여부의 필수 조건으로 추가한다 -- 그녀의 실제 작업 관례상
                # 이 가이드 테두리는 항상 흰색이므로, 이 조건 하나로 정상 케이스
                # 7개는 전혀 안 바뀌고 라쿤 같은 유색 헤일로만 걸러진다(회귀 없음,
                # 실측 재확인 완료).
                WHITE_MIN_CHANNEL = 230.0
                if float(np.min(band_median)) < WHITE_MIN_CHANNEL:
                    return None
                ref_color = band_median
                thickness = depth
            else:
                drift = float(np.abs(band_median - ref_color).mean())
                if not is_uniform or drift > COLOR_DRIFT_MAX:
                    break
                thickness = depth
            if int((eroded > 0).sum()) == 0:
                break
            prev_mask = eroded

        total_thickness = SKIP_PX + thickness
        if total_thickness < MIN_RING_PX:
            return None

        erode_px = int(round(total_thickness))
        erode_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
        inner_mask = cv2.erode(mask, erode_kernel, iterations=1)
        inner_area = int((inner_mask > 0).sum())
        if inner_area == 0:
            return None
        # 안전장치: 뭔가 어긋나서(예: 잘못된 성분 선택, 계산 오차) 안쪽 결과가
        # 원래 성분에 비해 터무니없이 작아지면(정상적인 가이드 테두리라면
        # 테두리 두께가 짧은 변의 35%/80px을 넘지 않도록 이미 위에서
        # max_depth로 막아뒀으므로 안쪽이 이 정도로 작아질 수 없음) 조용히
        # 실패로 처리하고 기존 동작(caller의 margin_mm 바깥 버퍼)으로
        # 넘긴다.
        mask_area = int((mask > 0).sum())
        if mask_area <= 0 or inner_area < mask_area * 0.15 or inner_area < 400:
            return None

        inner_polys = _contours_to_polygons(inner_mask, simplify_tol_px=0.8)
        inner_polys = [p for p in inner_polys if isinstance(p, Polygon) and not p.is_empty]
        inner_polys = [shapely_translate(p, xoff=x0, yoff=y0) for p in inner_polys]
        if not inner_polys:
            return None
        shape = unary_union(inner_polys)
        if hasattr(shape, "geoms"):
            polys = [g for g in shape.geoms if isinstance(g, Polygon) and not g.is_empty]
            if not polys:
                return None
            shape = polys[0] if len(polys) == 1 else unary_union(polys)
        elif not isinstance(shape, Polygon):
            return None
        if shape.is_empty:
            return None
        return shape
    except Exception:  # noqa: BLE001
        if os.environ.get("DEBUG_RING"):
            import traceback
            traceback.print_exc()
        return None


def _measure_content_bbox_px(image_path: str, rect_px: tuple):
    """
    Cheap, best-effort bounding box of whatever isn't background WITHIN
    `rect_px` -- deliberately NOT a full GrabCut segmentation (BORDERLESS is
    defined to skip that entirely, see this module's docstring: it trusts
    the artist's own selection/cell rectangle as ground truth, not a traced
    silhouette). This is only used as a cheap SAFETY CHECK -- alpha if the
    source has real transparency, otherwise a simple distance-from-background
    color threshold (same spirit as the Otsu fallback in
    core.cutline_core.load_raster_design, just without the extra denoise/
    contour machinery this check doesn't need). Returns None if the crop is
    degenerate or nothing looks like content (e.g. an empty background-only
    region), in which case the caller should skip the check rather than
    treat "None" as "content fills the whole rect".

    2026-09-08(9차) 실제 라쿤 패턴 시트 파일로 발견/수정한 버그: 배경색을
    "네 귀퉁이 4개 점의 중앙값"만으로 추정했었는데, 실제 파일의 한 격자
    칸(완전히 빈 여백 칸)은 위쪽 가로 전체에 얇은 색 줄(인쇄 경계선/여백
    아티팩트로 보임)이 지나가서 위쪽 두 귀퉁이가 우연히 그 줄 위에 걸려
    있었다 -- 그 결과 "귀퉁이 4점"의 중앙값이 진짜 배경(순백)과는 거리가
    있는 애매한 색이 되어, dist>20 기준으로 그 칸의 순백 배경 픽셀 100%가
    "내용물"로 오판됐다(실제로 재현/확인: 완전히 빈 칸이 통째로 "칼선
    대상"으로 잡혀, 완성도가 낮아 보이는 칼선이 그 칸 전체 크기로 생성됨).
    수정: 귀퉁이 4점이 아니라 전체 테두리(위/아래 행 + 좌/우 열) 픽셀의
    최빈값(mode)을 배경으로 쓴다 -- 테두리 전체에서 실제 배경이 차지하는
    비율이 얼토당토않게 작지 않은 한(그런 경우는 애초에 이 셀이 진짜
    내용으로 가득 찬 것), 한두 귀퉁이가 우연히 얇은 선/아티팩트에 걸려도
    나머지 테두리 픽셀들이 압도적으로 많아 최빈값이 실제 배경색을 정확히
    잡아낸다(같은 실제 파일로 검증: 오판 비율이 100% -> 0.4%로 줄고, 남는
    것도 그 얇은 줄 자체뿐이라는 것까지 확인됨).
    """
    from collections import Counter

    from PIL import Image

    with Image.open(image_path) as im:
        im = im.convert("RGBA")
        w, h = im.size
        x0, y0, x1, y1 = rect_px
        cx0, cy0 = max(0, int(x0)), max(0, int(y0))
        cx1, cy1 = min(w, int(np.ceil(x1))), min(h, int(np.ceil(y1)))
        if cx1 - cx0 < 2 or cy1 - cy0 < 2:
            return None
        crop = im.crop((cx0, cy0, cx1, cy1))

    arr = np.array(crop)
    alpha = arr[:, :, 3]
    if alpha.min() < 250:
        # real alpha channel is meaningfully in use in this crop
        mask = alpha > 10
    else:
        rgb = arr[:, :, :3].astype(np.int32)
        perimeter = np.concatenate(
            [rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]], axis=0
        )
        # 안티앨리어싱 노이즈 때문에 완전히 똑같은 배경색이 드물 수 있으니,
        # 4단위로 뭉쳐서(라운딩) 최빈값을 구함 -- 진짜 배경이 테두리 대다수를
        # 차지하는 한 여전히 정확히 그 배경색 버킷이 1위로 나온다.
        rounded = (perimeter // 4 * 4)
        bg_mode, _freq = Counter(map(tuple, rounded.tolist())).most_common(1)[0]
        bg = np.array(bg_mode, dtype=np.int32)
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
        mask = dist > 20

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return (cx0 + int(xs.min()), cy0 + int(ys.min()), cx0 + int(xs.max()) + 1, cy0 + int(ys.max()) + 1)


def _bridge_into_one_shape(polygons: list, min_bridge_px: float = 4.0) -> Optional[Polygon]:
    """실제 파일로 검증 중 발견된 문제(2026-09-08(11차)) 때문에 추가된 안전
    장치: rect_px 안에 서로 배경 틈으로 떨어진 조각이 여러 개 나오면(예:
    카드형 디자인 안의 작은 소품/글자박스/캐릭터 여러 개가 각각 별도
    색 경계로 잡히는 경우), 그걸 그냥 unary_union만 해서 서로 안 붙은
    여러 조각(MultiPolygon)인 채로 돌려주면, 그 상태로 안쪽 offset을
    적용했을 때 카드 하나가 조각 5~6개의 흩어진 칼선으로 보이는 문제가
    실제 파일로 재현됐다(이번 세션 앞부분의 "칸 자동 분리" 회귀와 근본
    원인이 같은 종류의 함정 -- 색 경계를 있는 그대로 여러 조각으로 두면
    무테 원래 요청인 "하나의 그룹 스티커 칼선"(멍푸님 표현, 단수)이 되지
    않는다).

    그래서 조각이 2개 이상이면, 작은 다리(bridge)를 점점 넓혀가며(우선
    아주 좁게 시작 -- 진짜 서로 다른 요소 사이의 좁은 배경 틈만 우선
    메꿔보고, 그래도 안 붙으면 다리를 두 배씩 넓혀 다시 시도) 결국 하나의
    도형이 될 때까지 반복한다. 다리 자체는 buffer(+g)로 부풀려서 서로
    닿게 한 뒤 buffer(-g)로 다시 원래 두께만큼 줄이는 "닫기(closing)"
    방식이라, 각 요소 자신의 실제 윤곽(볼록/오목한 굴곡)은 다리가 놓인
    부분을 제외하고는 그대로 유지된다(볼록 껍질(convex hull)처럼 전체
    모양을 뭉뚱그리지 않음).

    `min_bridge_px`: 호출하는 쪽이 이 함수 결과를 받은 뒤 실제로 적용할
    안쪽 offset 폭(보통 margin_mm을 px로 환산한 inset_px)을 그대로
    넘겨준다. 조각이 여럿일 때 "그냥 topology상 하나로 붙는 최소 폭"만
    확보하고 멈추면 안 되는 이유(실제 파일로 재현된 문제): 조각이 3개 이상
    이면 서로 다른 쌍마다 원래 틈(gap) 크기가 다 달라서, 전체가 "하나의
    도형"이 되는 순간에도 그중 가장 좁게 겨우 이어진 다리 하나는 폭이
    여전히 0에 가까울 수 있다 -- 그 상태에서 호출하는 쪽이 이어서
    min_bridge_px만큼 다시 안쪽으로 줄이면(margin_mm 적용), 바로 그 얇은
    다리만 잘려나가 도로 여러 조각으로 갈라져버렸다(실측: 8조각짜리 칸에서
    재현). 그래서 "다리가 이어졌다"를 판단하는 기준 자체를, 그냥
    topology(하나의 Polygon인가)가 아니라 "그 뒤에 min_bridge_px만큼
    안쪽으로 줄여도 여전히 하나의 Polygon으로 남는가"로 바꿨다 -- 실제로
    나중에 벌어질 연산을 미리 시뮬레이션해서 확인하는 것.

    다리 폭(g)을 작은 값에서부터 점점 넓혀가며(1.5배씩) 이 기준을 만족할
    때까지 반복한다. 무한정 넓어지는 것을 막기 위해 24회 반복까지만
    시도하고, 그래도 못 찾으면(극단적으로 멀리 떨어진 조각들) 전체의 볼록
    껍질을 최후 수단으로 쓴다(항상 "칼선 하나"를 보장하기 위함)."""
    if not polygons:
        return None

    # 2026-09-08(11차) 실제 파일로 검증하다가 발견/수정: 조각들이 원래부터
    # (버퍼링 없이도) 이미 하나의 Polygon으로 합쳐지는 경우(예: 컨투어
    # 추출/단순화 과정에서 우연히 살짝 겹치거나 맞닿음)를 예전엔 검증 없이
    # 그대로 반환했는데, 그 "우연히 맞닿은 지점"의 폭이 min_bridge_px보다
    # 얇을 수 있어서 여전히 같은 문제(나중에 안쪽으로 줄이면 다시 갈라짐)가
    # 생겼다(실측: 8조각짜리 칸에서 재현). 그래서 이제는 예외 없이 항상
    # 아래 반복문에서 "min_bridge_px만큼 줄여도 하나로 남는가"를 검증한다
    # -- g=0에 가까운 값부터 시작하므로 이미 충분히 잘 붙어있던 경우는
    # 첫 시도에서 바로 통과한다(추가 왜곡 없음).
    min_bridge_px = max(1.0, min_bridge_px)
    g = min_bridge_px * 0.1
    for _ in range(28):
        bridged = unary_union([p.buffer(g, join_style=1, resolution=8) for p in polygons])
        bridged = bridged.buffer(-g, join_style=1, resolution=8)
        if isinstance(bridged, Polygon) and not bridged.is_empty:
            survives = bridged.buffer(-min_bridge_px, join_style=1)
            if isinstance(survives, Polygon) and not survives.is_empty:
                return bridged
        g *= 1.5
    return unary_union(polygons).convex_hull


def _trace_content_silhouette_px(
    image_path: str,
    rect_px: tuple,
    supersample: int = 4,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    close_ratio: float = 0.006,
    edge_low: int = 25,
    edge_high: int = 80,
    min_bridge_px: float = 4.0,
    note_sink: Optional[list] = None,
):
    """2026-09-08(11차) 피드백("무테 칼선은 요소의 외곽 색을 기준으로 라인을
    생성하고 생성한 라인을 축소 및 재배치해 칼선을 생성해") 대응.

    `rect_px`(선택 영역/셀 사각형) 안의 실제 인쇄 내용을, 색이 아니라
    선/색 경계 자체로(core.multi_design._content_mask_from_gray -- Canny
    엣지 -> hole-fill, core.segmentation._edge_outline_mask/
    core.multi_design.detect_design_bboxes_px와 완전히 같은 원리) 직접
    추적한다. GrabCut을 쓰지 않는다 -- 무테는 원래부터 실루엣 추적을 하지
    않는 스타일이라는 이 모듈의 기존 전제(모듈 docstring 참고)는 그대로
    유지하되, "사각형을 그대로 쓴다"를 "실제 색 경계를 따라간 모양을
    쓴다"로 바꾸는 것뿐이다.

    여러 요소(예: 캐릭터 여러 마리 + 장식 소품)가 서로 배경 틈을 두고
    따로 있으면, 그 전부를 하나의 도형으로 묶는다(_bridge_into_one_shape
    -- 실제 파일로 검증하다가 발견: 그냥 unary_union만 하면 서로 안 붙은
    여러 조각(MultiPolygon)인 채로 남아, 카드형 디자인 하나가 흩어진 조각
    5~6개의 칼선으로 보이는 문제가 있었음). 개별 요소로 쪼개서 각자 다른
    도안처럼 취급하지 않는다 -- 항상 "그룹 스티커" 칼선 하나(단일
    Polygon)를 돌려준다. 호출하는 쪽이 이 결과 전체를 margin_mm만큼
    안쪽으로 줄인다.

    선/색 경계를 하나도 못 찾으면(빈 칸이거나, 배경까지 꽉 채우는 연속
    무늬라 안과 밖을 가를 경계 자체가 없는 경우-- 이 모듈 docstring의
    "공주토끼와 딸기" 사례) None을 돌려줘서, 호출하는 쪽이 안전하게 기존
    방식(사각형 자체를 축소)으로 되돌아가게 한다."""
    x0, y0, x1, y1 = [int(round(v)) for v in rect_px]
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = _content_mask_from_gray(gray, close_ratio, edge_low, edge_high)
    if not np.any(mask):
        return None

    requested_supersample = supersample
    supersample = auto_supersample(x1 - x0, y1 - y0, requested=requested_supersample)
    if supersample != requested_supersample and note_sink is not None:
        note_sink.append(
            f"선택 영역({x1-x0}x{y1-y0}px)이 커서 정밀도를 {requested_supersample}x -> "
            f"{supersample}x로 자동 조정했습니다 (처리 속도 보호)"
        )

    supersample = max(1, int(supersample))
    if supersample > 1:
        ch, cw = mask.shape
        upsampled = cv2.resize(
            mask, (cw * supersample, ch * supersample), interpolation=cv2.INTER_LINEAR
        )
        blur_k = max(3, (supersample // 2) * 2 + 1)
        upsampled = cv2.GaussianBlur(upsampled, (blur_k, blur_k), 0)
        _, mask_ss = cv2.threshold(upsampled, 127, 255, cv2.THRESH_BINARY)
        simplify_tol_super = simplify_tol_px * supersample
    else:
        mask_ss = mask
        simplify_tol_super = simplify_tol_px

    polygons = _contours_to_polygons(mask_ss, simplify_tol_super)
    if supersample > 1:
        polygons = [
            shapely_scale(p, xfact=1 / supersample, yfact=1 / supersample, origin=(0, 0))
            for p in polygons
        ]
    polygons = [p for p in polygons if p.area >= min_area_px]
    if not polygons:
        return None

    polygons = [shapely_translate(p, xoff=x0, yoff=y0) for p in polygons]
    content = _bridge_into_one_shape(polygons, min_bridge_px=min_bridge_px)
    if content is None or content.is_empty:
        return None
    return content


def generate_style_cutline(
    image_path: str,
    style: ImageStyle,
    dpi: float,
    selection_px: tuple = None,
    bounds_px: tuple = None,
    margin_mm: float = DEFAULT_STYLE_MARGIN_MM,
    grabcut_margin_px: int = 40,
    supersample: int = 4,
    note_sink: Optional[list] = None,
    sibling_boxes_px: Optional[list] = None,
    precomputed_content_px=None,
    art_region_px=None,
) -> Polygon:
    """
    Returns a single Polygon -- the one cutline this style calls for. No
    safety/cut/bleed tiers: the artist gave a number to use (`margin_mm`),
    so it's used almost exactly as given -- except it is never allowed
    below `MIN_STYLE_MARGIN_MM` (see that constant's docstring for why this
    is a different, smaller floor than the multi-tier path's MIN_GAP_MM). A
    value clamped up this way is reported via `note_sink` (pass e.g. `[]`
    and read it back) exactly like the multi-tier path already reports its
    own auto-adjustments -- never a silent change.

    `supersample`/`note_sink` also thread through to the LINE_ART branch's
    `segment_design_in_region` call (see `core.cutline_core.auto_supersample`)
    so a large selection on a large source image doesn't unknowingly balloon
    processing time; any such precision cap is appended to the same
    `note_sink` list.

    LINE_ART (유테): `selection_px` (a rough drag-selection around ONE character,
    on whatever colored background it sits on) is required -- GrabCut
    segments the actual silhouette, then the cutline is that silhouette
    buffered OUTWARD by `margin_mm`. The result is clipped to `bounds_px`
    (defaults to the full image) so it can never cross the cell/canvas edge.

    BORDERLESS: no segmentation happens at all -- `selection_px` (the
    artist's own drag rectangle around ONE design's cell, when given -- the
    actual per-design box, always more specific than any wider clip
    boundary) is shrunk INWARD by `margin_mm` directly. Falls back to
    `bounds_px` (a cell/slot rectangle known some other way, e.g. from
    core.guide, when there was no drag) or the full image if neither is
    given. This is the "토끼" case: one rectangle, done. (`selection_px` is
    preferred over `bounds_px` here deliberately: a caller like
    generate_cutline_auto may pass a wide `bounds_px`, e.g. the whole sheet,
    purely as an outer safety clamp for OTHER styles -- that must never be
    mistaken for the actual cell rectangle when this one IS 무테.)

    2026-09-12(55차 시도, 되돌림): 한때 `halo_search_bounds_px`라는 파라미터를
    추가해서, halo(배경 여유) 판정이 자동 인식으로 이미 타이트하게 잘린
    박스를 "연속 무늬"로 오판하는 걸 막아보려 했다. 실제 파일(파일2)로
    직접 시각 검증한 결과, halo 판정 자체는 의도대로 통과시켰지만 그렇게
    믿고 쓴 `_trace_content_silhouette_px`의 추적 결과 자체가 완전하지
    않은 경우가 실제로 확인됨(예: 캐릭터의 발/치마 일부가 잘려나가 도안을
    잘라먹는 모양이 됨, 눈코입만 작게 잡히고 머리 전체를 놓치는 경우 등)
    -- 즉 기존 halo 안전장치가 (엉뚱한 이유로 작동한 것이긴 해도) 실제로는
    이런 불완전한 추적 결과가 그대로 나가는 걸 막아주는 역할도 하고
    있었다는 뜻. halo 오판만 고치고 추적 자체의 신뢰도 문제는 그대로 둔
    채 내보내면, "안전하지만 부정확한 사각형" 대신 "도안을 잘라먹는 칼선"
    이 나갈 위험이 있어 더 위험하다고 판단해 되돌렸다. 다시 시도한다면
    halo 판정을 완화하기 전에 `_trace_content_silhouette_px`/
    `_bridge_into_one_shape`가 여러 색 영역으로 이루어진 캐릭터(예: 몸통
    색과 옷 색이 크게 다른 경우)의 전체 실루엣을 놓치지 않는지부터 실제
    파일로 검증해야 한다.

    `precomputed_content_px`: 2026-09-28 "갬뱃(GrabCut)을 지원하는 보조
    기능"(트라이맵 힌트 보정 도구, core.segmentation.segment_design_with_hints)
    연동용. 기본값 None(동작 변화 없음)이면 지금까지와 완전히 동일하게 이
    함수 안에서 직접 segment_design_in_region을 호출한다. 값이 주어지면
    (사람이 힌트를 찍어 이미 다시 계산해둔 실루엣, 원본 이미지 절대 픽셀
    좌표의 Polygon/MultiPolygon) LINE_ART(유테) 분기에서 그 값을 그대로
    실루엣으로 쓰고 GrabCut 재계산은 건너뛴다 -- 이후의 모든 후처리
    (형제 스필오버 제거, 저대비 헤일로 확장, 안내선 감지, 매끄럽게 다듬기,
    margin_mm 바깥 오프셋)는 기존과 완전히 동일하게 이어서 적용되므로,
    "사람이 검증한 실루엣이 항상 이 프로젝트의 다른 모든 안전장치를 그대로
    통과한다"는 것이 보장된다. BORDERLESS(무테)에 주어지면 그 실루엣을
    그림 안쪽으로 줄인다(_borderless_inward_from_silhouette).
    """
    if margin_mm < MIN_STYLE_MARGIN_MM:
        if note_sink is not None:
            note_sink.append(
                f"입력한 간격({margin_mm:g}mm)이 너무 좁아 인쇄/커팅 밀림에도 안전하도록 "
                f"최소값 {MIN_STYLE_MARGIN_MM:g}mm로 자동 조정했습니다."
            )
        margin_mm = MIN_STYLE_MARGIN_MM

    if style == ImageStyle.BORDERLESS:
        rect_px = selection_px or bounds_px
        if rect_px is None:
            from PIL import Image
            with Image.open(image_path) as im:
                rect_px = (0, 0, im.width, im.height)
        inset_px = mm_to_px(margin_mm, dpi)

        # 2026-09-28: 자동 인식 낱개 요소(sibling_boxes_px가 함께 온다 --
        # 이 맥락의 신호) 또는 힌트로 보정한 실루엣이면, GrabCut 실루엣을
        # 그림 안쪽으로 줄이는 경로를 먼저 쓴다(_borderless_inward_from_
        # silhouette 문서 -- 실제 테스트 파일 손 칼선과 대조해 정함).
        # 빈 도형 = "너무 가늘어 건너뜀". None이면 아래 기존 경로.
        if selection_px is not None and (
            sibling_boxes_px is not None or precomputed_content_px is not None
        ):
            silhouette_line = _borderless_inward_from_silhouette(
                image_path, selection_px, inset_px, grabcut_margin_px,
                supersample, note_sink, sibling_boxes_px, precomputed_content_px,
                art_region_px, dpi,
            )
            if silhouette_line is not None:
                if not silhouette_line.is_empty and bounds_px is not None:
                    silhouette_line = _drop_tiny_parts(
                        silhouette_line.intersection(_rect_from_bounds(bounds_px))
                    )
                if not silhouette_line.is_empty:
                    # 칼로 자를 수 없는 폭 0.5mm 미만 조각(실측: 9x6px 점 2개만
                    # 남은 항목)은 버리고, 전부 그렇다면 칼선을 만들지 않는다.
                    silhouette_line = _drop_uncuttable_slivers(silhouette_line, dpi, keep_largest=False)
                    if silhouette_line.is_empty and note_sink is not None:
                        note_sink.append("⚠ 무테: 너무 작거나 가는 요소라 칼선을 만들지 않았습니다.")
                return silhouette_line

        # 2026-09-08(11차) 피드백("무테 칼선은 요소의 외곽 색을 기준으로
        # 라인을 생성하고 생성한 라인을 축소 및 재배치해 칼선을 생성해"):
        # 예전엔 이 자리에서 늘 rect_px 사각형 자체를 안쪽으로 줄이기만
        # 했다("사각형을 그대로 쓴다"). 이제는 먼저 rect_px 안의 실제 선/색
        # 경계를 직접 추적해보고(_trace_content_silhouette_px -- GrabCut이
        # 아니라 core.multi_design과 완전히 같은 Canny 엣지 기반 방식이라
        # 무테가 "실루엣 추적을 안 한다"는 이 모듈의 기존 전제 자체를 깨지
        # 않는다), 추적된 실제 모양이 있으면 그걸 안쪽으로 줄인다 -- 여러
        # 요소가 있으면(예: 캐릭터 여러 마리 + 소품) 전부 하나로 묶인 모양
        # 하나(또는 서로 안 붙는 조각들의 묶음 하나)를 "그룹 스티커"
        # 칼선으로 만든다.
        #
        # 안전하게 예전 방식(사각형 축소)으로 되돌아가는 경우 두 가지:
        #   1) 추적 자체가 아무 것도 못 찾음(빈 칸, 또는 배경까지 꽉 채우는
        #      연속 무늬라 안/밖을 가를 경계 자체가 없는 경우 -- 이 모듈
        #      docstring의 "공주토끼와 딸기" 사례, 회귀 없음).
        #   2) 추적은 됐지만 margin_mm만큼 안쪽으로 줄이자마자 사라질
        #      정도로 실제 내용이 아주 작은 경우(예: 얇은 소품 하나) --
        #      이때 그대로 두면 그 요소의 칼선이 통째로 없어져 버리므로,
        #      기존처럼 셀 사각형 자체를 줄인 결과로 대체한다.
        rect = _rect_from_bounds(rect_px)
        content = None
        try:
            content = _trace_content_silhouette_px(
                image_path, rect_px, min_bridge_px=inset_px + 2.0, note_sink=note_sink,
            )
        except Exception:  # noqa: BLE001
            content = None

        # 2026-09-10(43차) 피드백("모든 칼선 어떤 시점부터 매끄럽지 않아"):
        # 800% 확대 스크린샷으로 직접 확인된 원인 -- 유테(LINE_ART, 아래
        # 587번째 줄 근방)는 실루엣을 margin만큼 밀어내기 전에 항상
        # smooth_design_naturally로 먼저 다듬는데(2026-09-09(14~16차)로
        # 실측 검증됨), 무테(BORDERLESS)의 색경계 추적(_trace_content_
        # silhouette_px)에는 이 다듬기 단계가 아예 없었다 -- 그래서 코나 꽃
        # 무늬처럼 캐릭터 안에 있는 작은 색 경계 디테일까지 그대로 윤곽에
        # 반영되어, 그 디테일 하나하나를 오르내리는 뾰족뾰족하고 안 매끄러운
        # 선이 나왔다(실제 화면 스크린샷으로 확인: 코 안쪽 무늬, 꽃 중심
        # 무늬를 따라 파고드는 선). 유테와 완전히 같은 함수/같은 배율(margin
        # 의 8배)로 다듬어서 큰 형태(그룹 전체 윤곽)는 그대로 두되 작은
        # 디테일의 뾰족한 부분만 뭉툭하게 만든다 -- 다듬는 시점은 halo
        # 판정(추적 결과를 쓸지 말지)과 안쪽 offset보다 먼저다(유테가 outward
        # offset 전에 다듬는 것과 대칭).
        if content is not None and not content.is_empty:
            # 2026-09-10(45차) 피드백("칼선 매끄러움 개선 됐으나 더 매끄러워야
            # 스티커 조각이 잘 떼어짐"): 유테와 같은 배율(8배)이 실측된
            # "왜곡 거의 없는" 상한이었으나(587번째 줄 근방 주석), 더 매끄럽게
            # 해달라는 명시적 요청에 따라 10배로 소폭 올림 -- 유테 쪽도 동일하게
            # 올림(아래 참고).
            content = smooth_design_naturally(content, inset_px * 10.0)

        # 2026-09-09(12차) 실사용 스크린샷으로 직접 확인된 문제: 위 추적을
        # "모든 무테에" 적용해보니(멍푸님 "우선 모든 무테에 적용해봐"),
        # 캐릭터 여러 마리 + 흩날리는 꽃잎/하트 장식이 셀 가장자리까지 꽉
        # 채우며 흩어져 있는(이 모듈 docstring의 "공주토끼와 딸기"류 --
        # 연속 무늬가 배경까지 채우는 원래 무테 정의) 실제 시트에서, 추적이
        # 그 장식 하나하나의 색 경계를 다 따라가며 지그재그로 이어붙인
        # 모양을 만들어 실제로 "칼선이 지저분하다"는 문제로 나타났다(실제
        # 화면 스크린샷으로 직접 확인: 캐릭터 머리/하트/꽃잎을 오르락내리락
        # 넘나드는 들쭉날쭉한 선). 이 실패 유형과, 서로 떨어진 캐릭터 여러
        # 마리를 "하나의 그룹 스티커"로 묶어야 하는 정당한 경우(원래 이
        # 추적 기능을 만들게 된 요청)를 구분해야 한다.
        #
        # 2026-09-12(57차, gap 기준 -> 면적비 기준으로 교체): 처음엔(12차)
        # 이 구분을 "추적된 모양이 rect_px 네 면 전부에서 실제 여유(halo)를
        # 두고 있는가"(가장자리까지의 거리, gap)로 판단했었다. 그런데 실제
        # 8개 파일 전부를 자동 인식 그대로 돌려 확인해보니(스크래치패드
        # qa_trace_area_ratio_all8.py, 커밋 안 함), gap 기준으로 "연속
        # 무늬"라고 잘못 판정되어 사각형으로 떨어지는 항목들이 실제로는
        # 전부(관측된 7건 전부, 비율 0.288~0.822) 이미 실제 캐릭터 윤곽을
        # 제대로 따라간 정상적인 추적 결과였다 -- 원인은 이 항목들의
        # rect_px 자체가 core.multi_design.detect_sub_element_boxes_px가
        # 이미 캐릭터 하나에 딱 맞게 타이트하게 잘라준 박스라서, 추적
        # 결과가 그 박스 가장자리에 가깝게 닿는 게 당연한데(애초에 타이트한
        # 박스니까) gap 기준은 그걸 "박스 가장자리까지 내용이 닿아 있다 ->
        # 연속 무늬"로 오판했던 것(실측: 캐릭터 세트 스타일 시트에서
        # 사각형 대체 29/156건 중 다수가 이 원인).
        #
        # "가장자리에 닿아 있는가"가 아니라 "추적이 실제로 뭔가를 깎아냈는가"
        # (content.area / rect 전체 면적의 비율)로 기준을 바꿨다: 비율이 1에
        # 가까우면(거의 전체 사각형 그대로) Canny가 내부 경계를 하나도 못
        # 찾고 다 채워버렸다는 뜻이므로(진짜 "배경까지 채우는 연속 무늬"거나
        # 추적이 무의미한 경우) 사각형으로 대체 -- 이러면 어차피 결과가
        # 사각형과 사실상 같아서 손해가 없다. 비율이 지나치게 작으면(그
        # 박스는 원래 캐릭터 하나에 맞춰 타이트하게 잘렸는데 추적 결과가
        # 그 안에서도 극히 일부만 차지) 55차에서 실제로 확인된 "눈코입만
        # 잡고 머리 전체를 놓침" 같은 불완전한 추적일 가능성이 높다고 보고
        # 역시 안전하게 사각형으로 대체한다 -- 두 임계값(MIN/MAX_TRUSTED_
        # TRACE_RATIO) 모두 이 모듈 위쪽 정의부 주석에 실측 근거가 있다.
        #
        # 55차와의 차이(회귀 아님): 55차는 rect_px 자체를 이웃 중간점까지
        # 넓혀서(halo_search_bounds_px) 판정에 여유를 주려다 추적 함수가
        # 그 넓어진 영역 안에서 완전한 실루엣을 못 찾는 문제를 노출시켰다.
        # 이번엔 rect_px를 전혀 건드리지 않는다 -- 지금 이미 만들어지고
        # 있는 타이트한 sub-element 박스에 대해, 그 결과를 "믿을지 말지"
        # 판단하는 기준만 gap에서 area ratio로 바꿨을 뿐이라 55차가 실제로
        # 노출시켰던 "더 넓은 영역에서 추적이 불완전함" 위험과는 무관하다.
        if content is not None and not content.is_empty:
            rect_area = rect.area
            ratio = (content.area / rect_area) if rect_area > 0 else 1.0
            if ratio >= MAX_TRUSTED_TRACE_RATIO or ratio <= MIN_TRUSTED_TRACE_RATIO:
                if note_sink is not None:
                    if ratio >= MAX_TRUSTED_TRACE_RATIO:
                        note_sink.append(
                            "무테 칼선: 이 칸은 배경까지 채우는 연속 무늬로 보여, "
                            "추적 대신 사각형 자체를 기준으로 안쪽으로 줄였습니다."
                        )
                    else:
                        note_sink.append(
                            "무테 칼선: 이 칸의 실제 경계 추적 결과가 너무 작아 "
                            "일부만 잡았을 가능성이 있어, 추적 대신 사각형 자체를 "
                            "기준으로 안쪽으로 줄였습니다."
                        )
                content = None

        line = None
        used_traced_content = False
        if content is not None and not content.is_empty:
            traced_line = content.buffer(-inset_px, join_style=1).intersection(rect)
            # _bridge_into_one_shape가 이미 "min_bridge_px만큼 줄여도 하나로
            # 남는가"를 검증했지만, 그보다 작은 실제 inset_px으로 줄이거나
            # rect에 다시 clip하는 이 마지막 단계에서 shapely의 buffer/
            # intersection 자체가 만드는 극미세 잡조각(실측: 656976px^2짜리
            # 진짜 모양 옆에 1.4px^2짜리 존재감 없는 조각)이 실제 파일로
            # 재현됐다 -- core.segmentation.segment_design_in_region이 이미
            # 쓰는 것과 같은 방식(가장 큰 조각 대비 2% 미만이면 버림)으로
            # 정리한다.
            if isinstance(traced_line, MultiPolygon) and len(traced_line.geoms) > 1:
                largest_area = max(g.area for g in traced_line.geoms)
                kept = [g for g in traced_line.geoms if g.area >= max(1e-6, 0.02 * largest_area)]
                traced_line = kept[0] if len(kept) == 1 else MultiPolygon(kept)
            if not traced_line.is_empty:
                line = traced_line
                used_traced_content = True
                if note_sink is not None:
                    note_sink.append(
                        "무테 칼선: 실제 선/색 경계를 추적한 모양을 기준으로 안쪽으로 줄였습니다."
                    )
        if line is None:
            line = rect.buffer(-inset_px, join_style=1)

        # Safety check (2026-08-26, direct feedback on a delivered example):
        # 무테's whole model is "shrink the ARTIST'S OWN selection/cell
        # rectangle inward by margin_mm" -- it deliberately never looks at
        # the real content (see this function's docstring). That means
        # nothing previously stopped a selection dragged too tight around
        # the actual visible art (less real clearance than margin_mm) from
        # producing a cutline that lands INSIDE the real artwork -- the
        # exact "칼선이 도안을 잘라먹는다" risk this project already guards
        # against for the multi-tier path (MIN_GAP_MM) and the margin value
        # itself (MIN_STYLE_MARGIN_MM), but not for THIS failure mode: a
        # selection rectangle with too little real margin already baked in.
        # A cheap content-bbox measurement (not a full segmentation -- see
        # _measure_content_bbox_px) catches this and reports it rather than
        # silently shipping a line that cuts into the art. Deliberately a
        # WARNING only, not an auto-correction: unlike a plain margin_mm
        # number, "widen the selection" isn't something this function can
        # safely guess on the artist's behalf (a real slot/cell rectangle,
        # e.g. from core.guide, is legitimately meant to be bigger than the
        # printed content by convention -- auto-expanding here could distort
        # that), so the artist is told to re-drag or adjust margin instead.
        #
        # 2026-09-08(11차) 위 _trace_content_silhouette_px 추가 이후: 이
        # 경고는 "사각형을 그대로 썼을 때"만 의미가 있다 -- 실제 색 경계를
        # 추적해서 그 모양 자체를 margin_mm만큼 안쪽으로 줄인 경우(위에서
        # used_traced_content=True)는, 정의상 항상 실제 그림 가장자리에서
        # 정확히 margin_mm만큼만 들어가 있으므로 "그림을 잘라먹는" 경우이
        # 아니다 -- 그대로 두면 이 경고 로직이 "안쪽으로 정상적으로 줄어든
        # 만큼"을 매번 잘못된 경고로 보고하게 된다(실제로 재현/확인:
        # test_accumulate.py의 "무테 with adequate padding raises NO 칼선
        # 경고" 케이스가 새 경로 추가 직후 거짓 경고를 냄). 그래서 이 검사는
        # 사각형 폴백 경로에서만 실행한다.
        if not used_traced_content and note_sink is not None:
            try:
                content_bbox = _measure_content_bbox_px(image_path, rect_px)
            except Exception:
                content_bbox = None
            if content_bbox is not None and not line.is_empty:
                lx0, ly0, lx1, ly1 = line.bounds
                cx0, cy0, cx1, cy1 = content_bbox
                overflow_px = max(
                    lx0 - cx0 if cx0 < lx0 else 0.0,
                    ly0 - cy0 if cy0 < ly0 else 0.0,
                    cx1 - lx1 if cx1 > lx1 else 0.0,
                    cy1 - ly1 if cy1 > ly1 else 0.0,
                )
                if overflow_px > 0:
                    overflow_mm = px_to_mm(overflow_px, dpi)
                    # 2026-09-07 피드백("1.2.3. 번 순으로 문제를 한줄로
                    # 설명해"): 예전엔 이 경고가 한 줄에 다 이어붙인 긴
                    # 문장이었음 -- GUI(_show_note_dialog)가 note_sink의
                    # 각 항목을 번호 붙여 한 줄씩 보여주므로, 여기서부터
                    # 아예 짧은 문장 여러 개로 나눠 담는다.
                    note_sink.append(
                        f"무테 칼선 경고: 선택 영역이 실제 그림 가장자리 대비 여유가 부족합니다 "
                        f"({margin_mm:g}mm 필요)."
                    )
                    note_sink.append(
                        f"계산된 칼선이 실제 그림 안쪽으로 최대 약 {overflow_mm:.2f}mm 들어가, "
                        f"그림을 잘라먹을 수 있습니다."
                    )
                    note_sink.append(
                        "선택 영역을 더 넉넉하게 다시 드래그하거나 간격(margin_mm)을 줄이세요."
                    )
        return line

    # LINE_ART (유테)
    if selection_px is None:
        raise ValueError("유테(LINE_ART) 스타일은 캐릭터 하나를 가리키는 selection_px가 필요합니다.")
    if precomputed_content_px is not None:
        design = precomputed_content_px
        if note_sink is not None:
            note_sink.append(
                "사람이 직접 확인/보정한 실루엣(트라이맵 힌트)을 사용해 실루엣 추적을 "
                "다시 하지 않았습니다."
            )
    else:
        design = segment_design_in_region(
            image_path, selection_px, margin_px=grabcut_margin_px,
            supersample=supersample, note_sink=note_sink,
        )
    # 2026-09-26("포들 얼굴 이중 칼선", 한 달째 미해결): GrabCut이 옆
    # sibling 요소의 일부를 내 것으로 잘못 집어와 서로 안 붙은 별개 조각을
    # 만드는 경우, 기존 "내 selection_px 안은 무조건 보존" 안전장치 때문에
    # 뒤(halo/최종 subtract) 단계에서도 못 걸러졌다 -- _drop_sibling_
    # spillover_fragments 문서 참고. halo 확장/버퍼링 전, 가장 이른 시점에
    # 걸러야 그 뒤 단계가 이 stray 조각을 더 키우거나 매끄럽게 다듬는 일도
    # 없다.
    design = _drop_sibling_spillover_fragments(design, sibling_boxes_px)
    design = _grow_design_into_low_contrast_halo_px(
        image_path, design, note_sink=note_sink, sibling_boxes_px=sibling_boxes_px,
        own_box_px=selection_px,
    )

    # 2026-09-26(멍푸 피드백, 실제 파일의 햄스터 도안으로 검증):
    # 손으로 칼선 따라 그리기 쉬우라고 캐릭터/장면 바깥에 일부러 그려 넣은
    # 두꺼운 단색 안내용 테두리가 있으면, 그 테두리까지 통째로 감싸고 또
    # margin_mm만큼 바깥으로 미는 대신 그 테두리 안쪽 경계에서 자른다(테두리
    # 자체는 칼선 밖으로 빠짐 -- 질문/답변으로 직접 확인된 방향). 보통
    # 캐릭터(경계 바로 안쪽이 단색이 아님)는 None을 돌려받아 기존 동작이
    # 전혀 안 바뀐다(_detect_inside_outer_ring_px 문서 참고).
    ring_shape = _detect_inside_outer_ring_px(
        image_path, design, sibling_boxes_px=sibling_boxes_px, own_box_px=selection_px,
    )

    if bounds_px is None:
        from PIL import Image
        with Image.open(image_path) as im:
            bounds_px = (0, 0, im.width, im.height)
    bounds_rect = _rect_from_bounds(bounds_px)

    # Smooth away jagged/noisy edge detail BEFORE the main outward offset:
    # a large margin buffer amplifies any remaining pixel-scale stairstep or
    # GrabCut edge wobble into a visible little point sticking out of an
    # otherwise round line ("뾰족한 모양"). A close-then-open round-trip
    # (out by some px, back in by the same amount, both with a round join)
    # rubs off that micro-noise without changing the shape's real
    # proportions, so the corner-rounding of the *real* margin buffer below
    # has a clean silhouette to work from instead of a noisy one.
    #
    # 2026-09-09(14차/15차/16차) 피드백 흐름: "모든 칼선은 매끄러워야하는데
    # 다 선이 구불구불해" -> "자연스러운 칼선이 중요해 옵션이 아니라
    # 기본이 되어야해" -> "칼선이 매끄럽지 않으면 스티커를 뜯을때도 안
    # 이쁘고 인쇄업체에서 해당 파일을 받아주지 않아. 매끄럽게 강도 강하게"
    # (실제 인쇄 발주 시 거절 위험까지 있는 문제라고 명확히 하심). 이전
    # 고정값(0.3mm, margin의 절반)은 실제 12칸 시트 파일로 확인해보니
    # 캐릭터가 귀처럼 촘촘한 굴곡을 가질 때는 margin의 2배까지 키워도 그
    # 굴곡을 따라가는 잔물결이 그대로 남았고, margin의 6배에서 처음으로
    # 눈에 보이는 잔물결이 사라짐을 확인했었다 -- "강도를 더 강하게"라는
    # 이번 요청에 따라, 실측으로 이미 확인해둔 범위(6배~8배는 모양 왜곡이
    # 거의 없음, 8배 기준 원래 실루엣 면적 대비 변화 약 3%) 중 더 강한
    # 쪽인 **8배**로 올림. 이것도 "선택 옵션"이 아니라 무조건 적용되는
    # 기본값이다.
    #
    # 2026-09-10(45차) 피드백("칼선 매끄러움 개선 됐으나 더 매끄러워야
    # 스티커 조각이 잘 떼어짐"): 8배가 그때까지 실측으로 확인된 "왜곡 거의
    # 없는" 상한이었지만, 더 매끄럽게 해달라는 요청이 다시 와서 10배로
    # 소폭 올림 -- 이 값은 아직 실제 파일(귀처럼 촘촘한 굴곡이 있는 캐릭터)
    # 로 왜곡 정도를 재확인하지 못했으니, 재빌드 후 뾰족한 부분(귀 사이 등)
    # 이 뭉개지지 않는지 꼭 확인 필요.
    #
    # 문제는 design이 여러 조각(큰 캐릭터 + 옆에 따로 그려진 작은 말풍선/
    # 아이콘/모서리 표시)일 수 있다는 것 -- 이 큰 presmooth를 조각 전체에
    # 그대로 적용하면 작은 조각(반지름이 presmooth보다 작은 조각)은
    # buffer(+)/buffer(-) 라운드트립 자체가 통째로 지워버린다(자기
    # 반지름보다 큰 구조로 열기 연산을 하면 사라지는 것과 같은 원리 --
    # 실측: 반지름 14~17px짜리 작은 요소에 85px 크기를 그대로 쓰면 다
    # 사라짐). core.cutline_core.smooth_design_naturally가 조각마다 자기
    # 크기에 맞게 이 크기를 스스로 낮춰서 큰 조각만 확실히 매끈해지고
    # 작은 조각은 사라지지 않게 한다.
    margin_px = mm_to_px(margin_mm, dpi)
    presmooth_px = margin_px * 10.0
    design = smooth_design_naturally(design, presmooth_px)

    if ring_shape is not None:
        line = ring_shape.intersection(bounds_rect)
        if line.is_empty and note_sink is not None:
            note_sink.append(
                "유테 칼선: 안내용 테두리 안쪽으로 자르면 내용이 사라져 원래 방식(테두리 바깥)으로 대체했습니다."
            )
        if line.is_empty:
            line = design.buffer(margin_px, join_style=1, resolution=24).intersection(bounds_rect)
        elif note_sink is not None:
            note_sink.append(
                "유테 칼선: 안내용 테두리를 감지해 그 안쪽 경계에서 잘랐습니다."
            )
    else:
        line = design.buffer(margin_px, join_style=1, resolution=24).intersection(bounds_rect)

    # 2026-09-14(실제 파일로 재확인): _grow_design_into_low_contrast_halo_px
    # 안에서 sibling_boxes_px를 이미 뺐어도(위 774번째 줄 근방), 그 함수에
    # 들어가기도 전에 segment_design_in_region(GrabCut) 자체가 이미 옆
    # 요소 쪽으로 침범한 실루엣을 내놓는 경우가 실측으로 확인됐다(위
    # _grow_design_into_low_contrast_halo_px 문서 참고) -- 게다가 침범한
    # 영역이 "옆 요소의 박스 내부"가 아니라 "옆 요소 박스와 내 박스 사이의
    # 빈 배경"인 경우도 있어(실측: 세트5 파일, 옆 요소 박스와는 겹치지
    # 않는 빈 여백까지 침범), sibling_boxes_px 하나만으로는 못 막았다. 그
    # 이후의 매끄럽게 다듬기(smooth_design_naturally)/바깥으로 밀기(margin
    # buffer) 단계도 모양을 더 키울 수 있으므로, 이 함수가 실제로 반환하는
    # 최종 도형에서 다시 한 번 -- 이번엔 확실하게 -- sibling_boxes_px 영역을
    # 뺀다. 직접 표시된 다른 요소의 박스를 침범하는 일은 이 마지막 단계
    # 이후로는 절대 없어야 한다.
    if sibling_boxes_px:
        try:
            forbidden = unary_union([shapely_box(*b) for b in sibling_boxes_px])
            # 2026-09-14(5차): 위 _grow_design_into_low_contrast_halo_px와
            # 같은 이유로, 내 자신의 selection_px 영역만큼은 sibling과
            # 겹치더라도 절대 깎아내지 않는다(내 박스 안은 무조건 내 것).
            if selection_px is not None:
                forbidden = forbidden.difference(shapely_box(*selection_px))
            if not forbidden.is_empty:
                line = line.difference(forbidden)
        except Exception:  # noqa: BLE001
            pass
        # 옆 요소 박스를 빼고 나면 폭 0.3mm 안팎의 실 같은 조각이 남을 수 있다
        # (실측: 격자 파일에서 35x4px 조각) -- 칼로 자를 수 없는 조각이므로 버린다.
        line = _drop_uncuttable_slivers(line, dpi)
    return line
