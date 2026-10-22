"""화이트 레이어(흰색 잉크 판) 만들기와 화이트 기준 칼선 보완(2026-10-05).

멍푸: "칼선을 보완하고 화이트 레이어를 만들어주는 기능을 추가할거야. 좋은 예는 키스컷 마테 좋은 예의
화이트 레이어를 기준으로 인쇄소 가이드, 화이트 레이어 튜토리얼 등 자료 찾아서 만들어."

좋은 예(키스컷 마스킹테이프 롤, 실제 인쇄 납품 파일)를 실측한 결과(일지 2026-10-05 참고):
  - 레이어 4개(참고 / 칼선 / 도안 / 화이트레이어). 화이트는 "화이트레이어"라는 별도 레이어에 흰색(C0 M0 Y0 K0)
    면으로 채운 벡터. 칼선은 마젠타 100%(C0 M100 Y0 K0) 0.5pt 선.
  - 화이트 범위 = 그림 실루엣과 거의 같음(줄이거나 늘리지 않음) -- 그림 알파 약 25% 경계로 잡으면 좋은 예
    화이트와 겹침 비율(IoU) 0.967로 가장 잘 맞았다(50% 경계는 0.938). 아주 작은 점까지 모두 덮고, 그림 안의 작은 구멍(안에 들어가는 가장 큰 원 지름
    약 2.1mm 이하)은 메웠고 그보다 큰 구멍(2.5mm 이상)은 비워 뒀다.
  - 칼선 = 화이트 외곽에서 바깥으로 약 1.3mm(1.1~1.5mm)를 고르게 띄운 닫힌 선. 칼선끼리 1.5mm보다 가까워지는
    조각은 하나로 합쳐져 있다(남은 가장 가까운 간격 약 1.57mm). 이 규칙으로 만든 칼선은 좋은 예의 칼선 66개와
    66개 모두 일치(겹침 비율 중앙값 0.985, 경계 차이 중앙값 0.07mm).
인쇄소 자료: 화이트는 별도 레이어(또는 별색)로 따로 지정, 화이트는 그림보다 같거나 살짝 작게(색이 화이트 경계를
덮도록), 반투명·그러데이션 부분은 화이트를 얼마나 깔지 따로 정해야 한다 -- 그래서 "안쪽으로 줄이기(mm)"를
설정으로 둔다(기본 0 = 좋은 예와 같음).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

# 좋은 예에서 잰 기본값
DEFAULT_FILL_HOLES_MM = 2.3     # 이보다 좁은 구멍(안에 들어가는 원 지름)은 화이트로 메움
DEFAULT_CHOKE_MM = 0.0          # 화이트를 그림보다 안쪽으로 줄이는 양
DEFAULT_CUT_OFFSET_MM = 1.3     # 화이트 기준 칼선: 화이트 외곽에서 띄우는 거리
DEFAULT_MERGE_GAP_MM = 2.0      # 칼선끼리 이보다 가까우면 하나로 합침(저장 전 점검 기준과 같음)
ALPHA_THRESHOLD = 64            # 그림 알파 약 25%(좋은 예 화이트와 가장 잘 맞음)


def _mm_to_px(mm: float, dpi: float) -> float:
    return float(mm) * float(dpi) / 25.4


def _disk(r_px: float):
    r = max(0, int(round(r_px)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def has_transparent_background(rgba: np.ndarray, min_frac: float = 0.02) -> bool:
    """알파가 있고 투명한 곳이 어느 정도(기본 2%) 있으면 '투명 배경 그림'."""
    return rgba.ndim == 3 and rgba.shape[2] == 4 and float((rgba[..., 3] < 128).mean()) >= min_frac


def rasterize_geom(geom, width: int, height: int) -> np.ndarray:
    """shapely (Multi)Polygon(이미지 픽셀 좌표) -> bool 마스크(구멍 반영)."""
    m = np.zeros((int(height), int(width)), np.uint8)
    if geom is None or geom.is_empty:
        return m.astype(bool)
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        ext = np.round(np.asarray(p.exterior.coords) * 8).astype(np.int32)
        cv2.fillPoly(m, [ext], 1, lineType=cv2.LINE_8, shift=3)
        for r in p.interiors:
            cv2.fillPoly(m, [np.round(np.asarray(r.coords) * 8).astype(np.int32)], 0, lineType=cv2.LINE_8, shift=3)
    return m.astype(bool)


def fill_small_holes(mask: np.ndarray, max_diameter_px: float) -> np.ndarray:
    """마스크 안의 구멍 중 '안에 들어가는 가장 큰 원의 지름'이 max_diameter_px 이하인 것만 메운다."""
    if max_diameter_px <= 0:
        return mask
    inv = (~mask).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    if n <= 1:
        return mask
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    dt = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
    maxd = np.zeros(n, np.float32)
    np.maximum.at(maxd, lab.ravel(), dt.ravel())
    fill = (2.0 * maxd) <= float(max_diameter_px)
    fill[0] = False
    fill[border] = False
    out = mask.copy()
    out[fill[lab]] = True
    return out


def build_white_mask(art_mask: np.ndarray, dpi: float, fill_holes_mm: float = DEFAULT_FILL_HOLES_MM,
                     choke_mm: float = DEFAULT_CHOKE_MM) -> np.ndarray:
    """그림 마스크 -> 화이트 마스크(작은 구멍 메움, 필요하면 안쪽으로 줄임)."""
    m = fill_small_holes(np.asarray(art_mask, bool), _mm_to_px(fill_holes_mm, dpi))
    if choke_mm and choke_mm > 0:
        m = cv2.erode(m.astype(np.uint8), _disk(_mm_to_px(choke_mm, dpi))).astype(bool)
    return m


def mask_to_geom(mask: np.ndarray, simplify_px: float = 0.4, min_area_px: float = 1.0):
    """bool 마스크 -> MultiPolygon(구멍 포함, 이미지 픽셀 좌표 -- 프로그램의 다른 칼선과 같은 픽셀 중심
    기준). 계단은 simplify_px 이내로 단순화."""
    m = np.asarray(mask, np.uint8)
    if not m.any():
        return MultiPolygon()
    contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        return MultiPolygon()
    hier = hier[0]
    polys = []
    for i, c in enumerate(contours):
        if hier[i][3] != -1 or len(c) < 3:
            continue
        ext = c[:, 0, :].astype(float)
        holes = []
        j = hier[i][2]
        while j != -1:
            h = contours[j][:, 0, :].astype(float)
            if len(h) >= 3:
                holes.append(h)
            j = hier[j][0]
        p = Polygon(ext, holes) if len(ext) >= 3 else None
        if p is None:
            continue
        if not p.is_valid:
            p = p.buffer(0)
        if simplify_px:
            p = p.simplify(simplify_px, preserve_topology=True)
        if p.is_empty or p.area < min_area_px:
            continue
        polys.extend(list(p.geoms) if hasattr(p, "geoms") else [p])
    return MultiPolygon([p for p in polys if p.geom_type == "Polygon"])


def cut_from_white_mask(white_mask: np.ndarray, dpi: float, offset_mm: float = DEFAULT_CUT_OFFSET_MM,
                        merge_gap_mm: float = DEFAULT_MERGE_GAP_MM, simplify_px: float = 0.5):
    """화이트 기준 칼선(키스컷 좋은 예 방식): 화이트를 offset_mm만큼 고르게 넓힌 외곽선. 칼선끼리
    merge_gap_mm보다 가까워지는 조각은 하나로 합친다. 구멍 없는 바깥 외곽선만."""
    m = cv2.dilate(np.asarray(white_mask, np.uint8), _disk(_mm_to_px(offset_mm, dpi)))
    if merge_gap_mm and merge_gap_mm > 0:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _disk(_mm_to_px(merge_gap_mm / 2.0, dpi)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    polys = []
    for c in contours:
        if len(c) < 3:
            continue
        p = Polygon(c[:, 0, :].astype(float))
        if not p.is_valid:
            p = p.buffer(0)
        if simplify_px:
            p = p.simplify(simplify_px, preserve_topology=True)
        if not p.is_empty:
            polys.extend([g for g in (p.geoms if hasattr(p, "geoms") else [p]) if g.geom_type == "Polygon"])
    return MultiPolygon(polys)


def ai_image_alpha(ai_path: str, page_index: int = 0) -> Optional[np.ndarray]:
    """.ai(PDF 호환) 안에 심긴 인쇄 이미지가 하나(같은 이미지를 여러 번 배치한 것 포함)이고 투명도 마스크
    (SMask)가 있으면 그 알파(0~255, 이미지와 같은 크기)를 돌려준다. 프로그램은 .ai를 열 때 이 이미지의 색만
    꺼내 쓰므로(core.ai_import) 투명했던 곳이 흰 배경이 된다 -- 화이트는 이 알파로 정확히 잡는다."""
    import fitz

    doc = fitz.open(ai_path)
    try:
        page = doc[page_index]
        imgs = page.get_images(full=True)
        if not imgs or len({e[0] for e in imgs}) != 1:
            return None
        xref, smask = imgs[0][0], imgs[0][1]
        if not smask:
            return None
        pm = fitz.Pixmap(doc, smask)
        if pm.n != 1:
            pm = fitz.Pixmap(fitz.csGRAY, pm)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w).copy()
        info = doc.extract_image(xref)
        if (info.get("width"), info.get("height")) != (pm.w, pm.h):
            a = cv2.resize(a, (int(info["width"]), int(info["height"])), interpolation=cv2.INTER_LINEAR)
        return a
    finally:
        doc.close()


def art_mask_for_white(image_path: str, result=None, ai_path: Optional[str] = None) -> tuple:
    """화이트를 깔 '그림 영역' 마스크와 그 출처를 돌려준다.
      1) .ai 안 이미지에 투명도(SMask)가 있으면 그 알파(가장 정확)
      2) 불러온 그림(PNG 등)이 투명 배경이면 그 알파
      3) 아니면(흰 배경 등 꽉 찬 그림) 프로그램이 인식한 그림 실루엣(각 칼선 결과의 design)을 합친 것
    Returns (bool mask, source)  source: "ai_alpha" | "alpha" | "design"."""
    from PIL import Image

    with Image.open(image_path) as im:
        w, h = im.size
        rgba = np.asarray(im.convert("RGBA")) if ("A" in im.getbands() or im.mode == "P") else None
    if ai_path:
        try:
            a = ai_image_alpha(ai_path)
        except Exception:  # noqa: BLE001
            a = None
        if a is not None and a.shape == (h, w) and float((a < 128).mean()) >= 0.02:
            return a >= ALPHA_THRESHOLD, "ai_alpha"
    if rgba is not None and has_transparent_background(rgba):
        return rgba[..., 3] >= ALPHA_THRESHOLD, "alpha"
    geoms = []
    for r in (result if isinstance(result, (list, tuple)) else [result]):
        d = getattr(r, "design", None) if r is not None else None
        if d is not None and not d.is_empty:
            geoms.append(d)
    if not geoms:
        return np.zeros((h, w), bool), "design"
    return rasterize_geom(unary_union(geoms), w, h), "design"


def sticker_border_mask(rgb: np.ndarray, dpi: float, min_sticker_mm2: float = 15.0) -> Optional[np.ndarray]:
    """흰 테두리가 둘린 스티커가 진하고 화려한 배경(그러데이션, 격자, 반짝이) 위에 놓인 시트에서 스티커 모양
    (흰 테두리 바깥선까지)을 찾는다(2026-10-12 멍푸: "배경색이 진하고 화려해도 정확한 칼선을 따야해").

    실제 시트(파란 그러데이션 + 연한 격자 + 흰 반짝이, 스티커마다 흰 테두리 약 1.8mm)에서 확인한 방법:
      1) 흰색(밝고 무채색)만 고른다 -- 격자는 연한 파랑이라 빠진다.
      2) 0.3mm보다 가는 흰 선(반짝이 빛줄기 등)은 깎아 낸다.
      3) 흰 테두리가 감싼 안쪽을 채운다(그림이 들어 있는 닫힌 테두리).
      4) 안에 그림(흰색 아닌 곳)이 15% 이상 있고 충분히 큰 덩어리만 스티커로 본다 -- 흰 반짝이·점은 빠진다.
    결과: 그림을 100% 덮고, 테두리 바깥선을 따라감(그림에서 최대 약 2mm).
    배경이 흰색이거나(가장자리가 대부분 흰색) 스티커가 없으면 None -- 이때는 예전 방식 그대로."""
    if cv2 is None:
        return None
    a = np.asarray(rgb)
    if a.ndim != 3 or a.shape[2] < 3:
        return None
    a = a[..., :3].astype(np.int16)
    h, w = a.shape[:2]
    mn = a.min(axis=2)
    mx = a.max(axis=2)
    white = ((mn >= 225) & ((mx - mn) <= 30)).astype(np.uint8)
    edge = np.concatenate([white[0], white[-1], white[:, 0], white[:, -1]])
    if float(edge.mean()) > 0.3:          # 흰 배경 시트 -- 흰 테두리를 배경과 구분할 수 없음
        return None
    r = max(1, int(round(_mm_to_px(0.3, dpi))))
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, _disk(r))
    inv = (1 - white).astype(np.uint8)
    n, lab, _, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    outside = np.isin(lab, np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    filled = (~outside).astype(np.uint8)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(filled, connectivity=8)
    if n2 <= 1:
        return None
    min_area = _mm_to_px(1.0, dpi) ** 2 * float(min_sticker_mm2)
    nonwhite = np.bincount(lab2[white == 0].ravel(), minlength=n2)
    keep = np.zeros(n2, bool)
    for i in range(1, n2):
        area = st2[i, cv2.CC_STAT_AREA]
        if area >= min_area and nonwhite[i] >= 0.15 * area:
            keep[i] = True
    if not keep.any():
        return None
    m = keep[lab2]
    if float(m.mean()) > 0.85:            # 거의 전체가 스티커 -- 배경을 못 찾은 것
        return None
    return m


def scene_objects_mask(rgb: np.ndarray, dpi: float, tol: float = 12.0, min_share: float = 0.004,
                       min_obj_mm2: float = 4.0, allow_single: bool = True, _depth: int = 0) -> Optional[np.ndarray]:
    """불투명 그림에서 배경을 빼고 그림 모양만 골라낸다. 투명 배경 그림처럼 그림 모양 기준 칼선을 만들 때 쓴다.

    2026-10-21(멍푸: 여러 색 배경 앞 그림의 무테 칼선이 배경과 묶임 -- "칼선 엉망이야"): 여러 색 배경.
    2026-10-22(멍푸: 흰 바탕 위 흰 캐릭터의 유테 칼선이 얼굴을 가로지름 -- "검정선 라인 밖으로 칼선 만들면 되는
    기초적인 문제"): 흰 바탕(한 가지 색 배경)도 같은 방식으로. 배경을 가장자리에서부터 채워 들어가되 그림 선은
    넘지 않으므로, 선으로 닫힌 흰 얼굴은 그림으로 남고 칼선은 선 바깥으로 간다.

    방법:
      1) 가장자리 픽셀의 색들을 배경 색으로 삼는다(가장자리의 65% 이상이 한 색이면 그 색 하나 -- 흰 바탕에 그림이
         가장자리까지 닿아도).
      2) 배경 색과 비슷한(차이 12 이하) 픽셀 가운데 가장자리까지 이어진 곳이 배경. 선에 난 틈이나 그림 안의 가는
         흰 선(1mm 미만)으로는 새어 들어가지 않는다.
      3) 나머지가 그림. 그림 안의 구멍은 채우고 4mm²보다 작은 조각은 버린다. 여러 색 배경이면 색 경계의 얇은
         찌꺼기(0.3mm 미만)를 깎고, 거의 배경 색뿐인 조각(얇은 경계에 갇힌 배경 칸, 예: 창틀 안 유리 한 칸)도 버린다.
    그림이 너무 작거나(2% 미만) 너무 크면(85% 초과) None -- 예전 방식 그대로."""
    if cv2 is None:
        return None
    h, w = rgb.shape[:2]
    if h < 20 or w < 20:
        return None
    sm = cv2.medianBlur(np.ascontiguousarray(rgb[:, :, :3]), 5)
    border = np.concatenate([sm[0], sm[-1], sm[:, 0], sm[:, -1]]).astype(np.float32)
    q = border.astype(np.int32) // 4
    _keys, inv, cnt = np.unique(q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2], return_inverse=True, return_counts=True)
    inv = inv.ravel()
    top = border[inv == int(np.argmax(cnt))].mean(0)
    single = (((border - top) ** 2).sum(1) <= tol * tol).mean() >= 0.65
    if single and not allow_single:
        return None
    if single:
        pal = top[None, :]
    else:
        pal = np.asarray([border[inv == i].mean(0) for i in np.argsort(-cnt) if cnt[i] >= min_share * len(border)],
                         np.float32)
    f = sm.astype(np.float32)
    d = None
    ones = np.ones((1, 3), np.float32)
    for c in pal:
        diff = cv2.subtract(f, (float(c[0]), float(c[1]), float(c[2]), 0.0))   # OpenCV로 계산(numpy보다 빠름)
        dc = cv2.transform(cv2.multiply(diff, diff), ones)
        d = dc if d is None else cv2.min(d, dc)
    d = d.reshape(h, w)
    pure = d <= tol * tol
    bglike = pure.astype(np.uint8)
    # 선의 좁은 틈이나 그림 안의 가는 흰 선(1mm 미만 -- 예: 모자의 흰 격자 무늬가 그림 끝까지 닿은 경우, 2026-10-22
    # 멍푸 도안으로 확인)으로 배경이 새어 들어가지 않게: 좁은 길을 끊은 배경에서 가장자리와 이어진 곳만 찾고,
    # 끊었던 만큼만 다시 넓힌다(원래 배경 색인 곳 안에서만).
    # (한 색 배경일 때만 -- 여러 색 배경은 배경 칸 사이가 원래 좁아서 끊으면 안 됨)
    rg = max(1, int(round(_mm_to_px(0.5, dpi)))) if single else 0
    core_bg = cv2.morphologyEx(bglike, cv2.MORPH_OPEN, _disk(rg)) if rg else bglike
    _n, lab = cv2.connectedComponents(core_bg, connectivity=4)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    lut = np.zeros(int(lab.max()) + 1, np.uint8)
    lut[edge[edge > 0]] = 1
    bg = lut[lab]
    if rg:
        bg = (cv2.dilate(bg, _disk(rg + 1)) > 0) & pure
    else:
        bg = bg > 0
    fg = (~bg).astype(np.uint8)
    if not single:
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, _disk(max(1, int(round(_mm_to_px(0.3, dpi))))))
    _n2, lab2 = cv2.connectedComponents((1 - fg).astype(np.uint8), connectivity=4)
    edge2 = np.unique(np.concatenate([lab2[0], lab2[-1], lab2[:, 0], lab2[:, -1]]))
    lut2 = np.ones(int(lab2.max()) + 1, np.uint8)                  # 가장자리와 안 닿은 빈 곳 = 그림 안 구멍
    lut2[edge2] = 0
    lut2[0] = 0
    fg |= lut2[lab2]                                              # 그림 안 구멍 채우기(흰 얼굴 등)
    n3, lab3, st, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    min_px = min_obj_mm2 / (25.4 / float(dpi)) ** 2
    pure_frac = np.bincount(lab3.ravel(), weights=pure.ravel().astype(np.float64), minlength=n3) / np.maximum(
        st[:, cv2.CC_STAT_AREA], 1)
    keep = [i for i in range(1, n3) if st[i, cv2.CC_STAT_AREA] >= min_px
            and (single or pure_frac[i] < 0.9)]
    if not keep:
        return None
    lut3 = np.zeros(n3, bool)
    lut3[keep] = True
    out = lut3[lab3]
    if _depth == 0:
        # 아주 큰 덩어리(그림의 25% 이상) 안에 바탕(예: 사진 속 투명 시트, 큰 판 위에 놓인 스티커들)이 따로
        # 있으면, 그 덩어리의 가장자리 색을 바탕으로 보고 안에서 한 번 더 그림을 찾는다(그림이 2개 이상일 때만 바꿈).
        for i in keep:
            if st[i, cv2.CC_STAT_AREA] < 0.25 * h * w:
                continue
            x, y, bw, bh = st[i, :4]
            comp = lab3[y:y + bh, x:x + bw] == i
            ring = comp & ~cv2.erode(comp.astype(np.uint8), _disk(2)).astype(bool)
            if not ring.any():
                continue
            sub = sm[y:y + bh, x:x + bw].copy()
            sub[~comp] = np.median(sub[ring], axis=0).astype(sub.dtype)
            inner = scene_objects_mask(sub, dpi, tol, min_share, min_obj_mm2, True, _depth=1)
            if inner is None:
                continue
            if cv2.connectedComponents(inner.astype(np.uint8), connectivity=8)[0] - 1 < 2:
                continue
            region = out[y:y + bh, x:x + bw]
            region[comp] = inner[comp]
    frac = out.mean()
    if frac < 0.02 or frac > 0.85:
        return None
    return out


def borderless_cut_from_mask(art_mask: np.ndarray, dpi: float, inset_mm: float = 1.2) -> tuple:
    """투명 배경 그림의 무테 칼선(2026-10-20, 멍푸: "무테 + 투명 배경 PNG도 고쳐"): 그림 조각마다 바깥 외곽을
    그림 안쪽으로 inset_mm만큼 줄인 선. 기존 무테 규칙(core.image_style._inset_inside_art -- 실제 손 칼선 대조로
    정한 "항상 그림 안쪽, 뾰족한 끝만 둥글게")을 그대로 쓴다. 안쪽으로 줄이면 사라지는 가는 조각은 배경을
    자르지 않도록 칼선을 만들지 않는다. Returns (MultiPolygon, 건너뛴 조각 수)."""
    from core.image_style import _inset_inside_art

    geom = mask_to_geom(art_mask)
    inset_px = _mm_to_px(inset_mm, dpi)
    lines, skipped = [], 0
    for p in geom.geoms:
        outer = Polygon(p.exterior.coords)
        if outer.is_empty:
            continue
        ln = _inset_inside_art(outer, inset_px)
        parts = [g for g in (getattr(ln, "geoms", None) or [ln]) if g is not None and not g.is_empty
                 and g.geom_type == "Polygon"]
        if not parts:
            skipped += 1
            continue
        lines.extend(parts)
    return MultiPolygon(lines), skipped


def count_close_pairs(cuts, dpi: float, gap_mm: float = DEFAULT_MERGE_GAP_MM) -> int:
    """칼선끼리 gap_mm보다 가까운 쌍의 수."""
    gs = list(getattr(cuts, "geoms", []) or [])
    lim = _mm_to_px(gap_mm, dpi)
    n = 0
    for i in range(len(gs)):
        bi = gs[i].bounds
        for j in range(i + 1, len(gs)):
            bj = gs[j].bounds
            if bi[0] - lim > bj[2] or bj[0] - lim > bi[2] or bi[1] - lim > bj[3] or bj[1] - lim > bi[3]:
                continue
            if gs[i].distance(gs[j]) < lim:
                n += 1
    return n


def shape_cut_source(image_path: str, dpi: float, ai_path: Optional[str] = None, scene: bool = False,
                     scene_single: bool = False) -> tuple:
    """그림 모양 기준 칼선을 만들 수 있는 마스크와 종류.
      ("ai_alpha" | "alpha", 그림 마스크): 투명 배경 그림 -- 칼선은 그림에서 띄운다.
      ("sticker", 스티커 마스크): 진한 배경 위 흰 테두리 스티커 -- 칼선은 흰 테두리 바깥선.
      ("scene", 그림 마스크): scene=True("배경이 복잡해도 그림만 분리")이고 불투명 배경(흰 바탕, 여러 색 배경) 위
        그림 -- 투명 배경처럼 그림 모양 기준. 한 색 배경(흰 바탕)은 scene_single=True일 때만(유테·무테).
      (None, None): 모두 아님(예전 방식)."""
    art, src = art_mask_for_white(image_path, None, ai_path=ai_path)
    if src in ("ai_alpha", "alpha") and art.any():
        return src, art
    from PIL import Image

    with Image.open(image_path) as im:
        rgb = np.asarray(im.convert("RGB"))
    m = sticker_border_mask(rgb, dpi)
    sc = scene_objects_mask(rgb, dpi, allow_single=scene_single) if scene else None
    # 흰 테두리를 일부 스티커에서만 찾았으면(배경을 뺀 그림 넓이의 85% 미만 -- 사진처럼 흰 테두리가 고르게 밝지
    # 않을 때, 2026-10-22 확인) 흰 테두리 대신 그림 모양 기준으로.
    if m is not None and (sc is None or (m & sc).sum() >= 0.85 * sc.sum()):
        return "sticker", m
    if sc is not None:
        return "scene", sc
    if m is not None:
        return "sticker", m
    return None, None


def supplement_missing_cuts(existing_cut, white_mask: np.ndarray, white_geom, dpi: float,
                            offset_mm: float = DEFAULT_CUT_OFFSET_MM, merge_gap_mm: float = DEFAULT_MERGE_GAP_MM,
                            min_piece_mm2: float = 0.5):
    """이미 있는 칼선은 그대로 두고, 칼선이 없는 그림 조각(화이트 기준)에만 칼선을 더한다.

    화이트 조각마다 기존 칼선 안에 든 비율을 재서 절반도 안 들어 있으면 "빠진 그림"으로 보고, 빠진 그림끼리만
    화이트 기준 칼선(키스컷 좋은 예 방식)을 만든다. 새 칼선이 기존 칼선과 merge_gap_mm보다 가까우면 억지로
    붙이거나 기존 칼선을 고치지 않고 위치만 알려 준다(사람이 확인할 곳).
    Returns (added MultiPolygon, too_close [(x_mm, y_mm)], n_places) -- n_places = 빠진 그림 자리 수."""
    wm = np.asarray(white_mask, np.uint8)
    h, w = wm.shape
    mm = 25.4 / float(dpi)
    covered = np.zeros((h, w), bool)
    ex_polys = []
    if existing_cut is not None and not existing_cut.is_empty:
        ex_polys = [Polygon(p.exterior) for p in (existing_cut.geoms if hasattr(existing_cut, "geoms") else [existing_cut])
                    if p.geom_type == "Polygon" and not p.is_empty]
        covered = rasterize_geom(MultiPolygon(ex_polys), w, h)
    n, lab, st, _ = cv2.connectedComponentsWithStats(wm, connectivity=8)
    if n <= 1:
        return MultiPolygon(), [], 0
    cov = np.bincount(lab[covered & (wm > 0)], minlength=n) / np.maximum(st[:, cv2.CC_STAT_AREA], 1)
    min_px = min_piece_mm2 / (mm * mm)
    missing_ids = [i for i in range(1, n) if cov[i] < 0.5 and st[i, cv2.CC_STAT_AREA] >= min_px]
    if not missing_ids:
        return MultiPolygon(), [], 0
    keep = np.zeros(n, bool)
    keep[missing_ids] = True
    miss_mask = keep[lab]
    cands = cut_from_white_mask(miss_mask, dpi, offset_mm=offset_mm, merge_gap_mm=merge_gap_mm)
    ex_union = unary_union(ex_polys) if ex_polys else None
    gap_px = _mm_to_px(merge_gap_mm, dpi)
    added, too_close = [], []
    for c in cands.geoms:
        if ex_union is not None and c.distance(ex_union) < gap_px:
            q = c.representative_point()
            too_close.append((q.x * mm, q.y * mm))
            continue
        added.append(c)
    return MultiPolygon(added), too_close, len(cands.geoms)
