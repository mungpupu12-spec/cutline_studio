"""
Ties together the three pieces a real "select an image, pick a cutline
type, generate" workflow needs:

  1. WHERE -- a rough rectangle the artist drags around one design, on
     whatever background it happens to sit on (core.segmentation, when the
     background isn't clean alpha/near-white).
  2. WHAT SHAPE -- 완칼 (follow the traced silhouette) or one of the four
     도무송 primitives (core.cutline_types).
  3. HOW FAR OUT -- the existing safety/cut/bleed mm offsets, minimum-gap
     enforcement, and near-line merging (core.cutline_core).

This is the entry point the GUI's drag-select + cutline-type buttons call.
"""

from __future__ import annotations

from typing import Optional

from PIL import Image

from shapely.geometry import MultiPolygon, Polygon

from .cutline_core import (
    CutlineResult,
    MIN_DOMUSONG_GAP_MM,
    MIN_GAP_MM,
    OffsetSpec,
    compute_offsets,
    mm_to_px,
    px_to_mm,
    smooth_design_naturally,
)
from .cutline_types import CutlineType, build_cutline_design
from .image_style import ImageStyle, generate_style_cutline
from .segmentation import segment_design_in_region
from .style_classify import DEFAULT_RECTANGULARITY_THRESHOLD, StyleClassification, classify_style

# 2026-09-08 피드백("햄스터를 칼선을 따야 하는데 햄스터 배를 동그랗게 칼선을
# 생성했어"): 빽빽하게 붙어있는 반복 패턴 시트(예: 너구리+곰+꽃 격자)에서
# GrabCut으로 실루엣을 추적하면, 캐릭터 몸 전체가 아니라 몸 안에서 색이 가장
# 뚜렷하게 갈리는 작은 부분(예: 배의 밝은 무늬)만 잡아버리는 경우가 실제로
# 확인됐다 -- 캐릭터의 몸통-배경 경계보다 캐릭터 "안"의 배-몸통 경계가 색
# 대비가 더 강해서, GrabCut의 그래프컷이 그 더 강한 안쪽 경계에 달라붙어
# 버리기 때문이다(core.interactive_cutline.generate_cutline_from_known_silhouette
# 문서에도 이미 기록되어 있듯, 이전 세션에서 이미 한 번 확인된 GrabCut의
# 근본적인 한계 -- "sure foreground" 시드를 중심에 둬도 결과가 똑같았던
# 사례와 동일한 종류의 실패). 즉 이건 "더 똑똑한 시딩"으로 고칠 수 있는
# 문제가 아니라, 결과가 명백히 잘못됐을 때(선택 영역의 극히 일부만 잡힘)
# 그걸 감지해서 더 안전한 결과(사각형 컷)로 대체해야 하는 문제.
#
# 아래 min_ratio=0.35는 임의의 숫자가 아니라, "실루엣이 선택 영역(그 스티커가
# 있는 셀/박스)의 최소 이 정도 비율은 채워야 정상적인 캐릭터로 볼 수 있다"는
# 보수적인 안전 기준선이다 -- 실제 벨리(배) 부분만 잡히는 경우처럼 훨씬 작은
# 조각이 나오면, 잘못 추적됐을 가능성이 매우 높다고 보고 대체한다.
DEFAULT_UNDERSIZED_SILHOUETTE_RATIO = 0.35


def is_silhouette_undersized(
    design_area_px: float,
    reference_area_px: float,
    min_ratio: float = DEFAULT_UNDERSIZED_SILHOUETTE_RATIO,
) -> bool:
    """실루엣 추적 결과(design_area_px)가 원래 선택 영역(reference_area_px,
    보통 자동 인식된 셀/박스의 넓이)에 비해 지나치게 작으면 True -- GrabCut이
    캐릭터 전체가 아니라 몸 안의 대비가 강한 작은 부분(배 무늬 등)만 잘못
    잡았을 가능성이 높다는 신호. reference_area_px가 0 이하(방어적 처리)면
    비교할 기준이 없으므로 항상 False."""
    if reference_area_px <= 0:
        return False
    return design_area_px < min_ratio * reference_area_px


def generate_cutline_for_selection(
    image_path: str,
    selection_px: tuple,
    cutline_type: CutlineType,
    dpi: float,
    offset_mm: OffsetSpec,
    use_grabcut: bool = True,
    grabcut_margin_px: int = 40,
    circle_ellipse_pad: float = 0.0,
    merge_gap_mm: Optional[float] = None,
    bounds_px: Optional[tuple] = None,
    supersample: int = 4,
) -> CutlineResult:
    """
    `selection_px`: (x0, y0, x1, y1) -- the artist's drag-selection rectangle
    around ONE design, in the full image's own pixel coordinates. Does not
    need to be precise.

    `supersample`: requested precision (GUI "정밀도" option) for the
    GrabCut-based segmentation step -- see
    `core.cutline_core.auto_supersample`. Automatically capped down for a
    large selection so precision never silently costs more time than
    expected; any such cap is reported in the returned result's
    `adjustments`.

    `use_grabcut`: when True (default), the ACTUAL content inside the
    selection is segmented out first (core.segmentation) -- this is what
    makes it work over colored/busy backgrounds, not just clean alpha PNGs.
    A 도무송 shape is then sized to that real content's bounding box (so the
    die is no bigger than it needs to be); 완칼 uses the segmented silhouette
    directly. Set False to skip segmentation and use the raw selection
    rectangle itself as the content bounds instead (only meaningful for
    도무송 types -- 완칼 has no silhouette to follow without segmentation).

    `bounds_px`: (x0, y0, x1, y1) that NO offset line may ever cross -- a
    real die can't cut outside the actual printed canvas, so a 도무송
    primitive (or a safety/bleed offset) that would otherwise run past the
    edge is clipped to this rect instead. Defaults to the full source
    image's own extent; pass a tighter rect (e.g. one sheet slot/tile's
    boundary from core.guide) when the design sits in a smaller cell within
    a larger sheet.
    """
    if cutline_type == CutlineType.FULL_CUT and not use_grabcut:
        raise ValueError(
            "완칼(FULL_CUT)은 실제 실루엣이 필요합니다 -- use_grabcut=False로는 만들 수 없습니다."
        )

    precision_notes: list = []
    if use_grabcut:
        content = segment_design_in_region(
            image_path, selection_px, margin_px=grabcut_margin_px,
            supersample=supersample, note_sink=precision_notes,
        )
        # 2026-09-09(14차/15차/16차) 피드백("모든 칼선은 매끄러워야하는데
        # 다 선이 구불구불해" -> "자연스러운 칼선이 중요해 옵션이 아니라
        # 기본이 되어야해" -> "칼선이 매끄럽지 않으면... 인쇄업체에서 해당
        # 파일을 받아주지 않아. 매끄럽게 강도 강하게"):
        # core.image_style.generate_style_cutline(유테)와 같은 크기 기준
        # (참고 mm의 8배, 조각별로 스스로 크기를 낮추는
        # smooth_design_naturally)을 여기(완칼/도무송)에도 그대로 적용 --
        # 실제 파일로 두 경로를 나란히 돌려봤을 때 한쪽만 매끄럽게 처리되고
        # 있던 차이를 없앤다. 기준 mm는 이 함수엔 하나의 margin_mm이 없으니
        # (safety/cut/bleed 세 단계) 그중 가장 안쪽인 safety_mm을 쓴다.
        smooth_ref_mm = offset_mm.safety_mm if offset_mm is not None else 1.0
        presmooth_px = mm_to_px(smooth_ref_mm, dpi) * 8.0
        content = smooth_design_naturally(content, presmooth_px)

        # 2026-09-12(58차) 피드백("도무송은 밀렸고"): 도무송(CIRCLE/ELLIPSE/
        # SQUARE/RECTANGLE)은 core.cutline_types.fit_domusong_shape가 이
        # content의 bbox 중심에 딱 맞춰 모양을 만든다 -- 그런데 이 문서
        # 위쪽(core.segmentation.segment_design_in_region 독스트링, 4번
        # 항목)에도 이미 기록돼 있듯, GrabCut이 캐릭터 전체가 아니라 안쪽의
        # 색 대비가 가장 강한 작은 무늬/디테일만 잡아버리는 실패가 실제로
        # 있다(core.style_classify 문서에도 같은 종류의 실패가 기록됨).
        # 유테/무테(BORDERLESS/LINE_ART) 경로는 이미 이 실패를
        # is_silhouette_undersized로 감지해서 안전하게 대체하는데(gui/app.py
        # 참고), 도무송 경로만 그 안전장치가 전혀 없어서 GrabCut이 이렇게
        # 실패하면 다이컷 중심 자체가 실제 캐릭터 중심에서 벗어난 채로 그냥
        # 나갔다 -- 실제 8개 파일로 재현/확인됨(스크래치패드
        # qa_domusong_center_shift_all8.py, 커밋 안 함: 선택 영역 75개 중
        # 14개에서 GrabCut 중심이 독립적인 기준 중심과 5%p 이상 벗어남,
        # 최악 사례는 캐릭터의 배경-실루엣 경계는 놓치고 안쪽 흰색 장식
        # 무늬만 잡음). 완칼(FULL_CUT)은 추적된 실루엣 자체가 결과물이라
        # 이 문제와 무관하므로 도무송 타입에만 적용한다 -- 실루엣이
        # 지나치게 작으면(선택 영역의 상당 부분을 놓쳤을 가능성) 잘못
        # 추적됐다고 보고, 이미 검증된 "선택 영역 자체를 그대로 쓴다"(
        # use_grabcut=False와 동일한 안전한 값)로 대체한다.
        if cutline_type.is_domusong:
            sel_x0, sel_y0, sel_x1, sel_y1 = selection_px
            selection_area_px = max(0.0, (sel_x1 - sel_x0) * (sel_y1 - sel_y0))
            content_area_px = content.area if content is not None and not content.is_empty else 0.0
            if is_silhouette_undersized(content_area_px, selection_area_px):
                ratio_pct = (
                    content_area_px / selection_area_px * 100 if selection_area_px > 0 else 0.0
                )
                precision_notes.append(
                    f"도무송: 실루엣 추적 결과가 선택 영역의 {ratio_pct:.0f}%밖에 안 돼(예: "
                    f"몸통 전체가 아니라 안쪽 무늬 일부만 잡혔을 가능성) 잘못됐다고 보고, "
                    f"선택 영역 자체를 기준으로 다이컷 중심/크기를 잡았습니다."
                )
                content = tuple(selection_px)
    else:
        content = tuple(selection_px)

    design = build_cutline_design(content, cutline_type, circle_ellipse_pad=circle_ellipse_pad)

    with Image.open(image_path) as im:
        w, h = im.size

    if bounds_px is None:
        bounds_px = (0, 0, w, h)

    # 도무송(CIRCLE/ELLIPSE/SQUARE/RECTANGLE)과 완칼(FULL_CUT)은 최소 여유
    # 기준이 다를 수 있어 별도 상수를 쓴다 -- 40차엔 도무송 전용 15mm를
    # 뒀었지만, 53차에 실제 인쇄소 도무송 가이드 파일 실측(2.0mm)에 맞춰
    # MIN_DOMUSONG_GAP_MM 자체를 2.0mm로 낮췄다(core.cutline_core 참고).
    # 지금은 두 상수 값이 같아졌어도, 선택 로직 자체는 그대로 둔다.
    min_gap_mm = MIN_DOMUSONG_GAP_MM if cutline_type.is_domusong else MIN_GAP_MM
    offset_mm_adj, adjustments = offset_mm.enforce_minimum_gap(min_gap_mm=min_gap_mm)
    adjustments = precision_notes + adjustments
    # 2026-09-10 피드백("도무송 파란색 선이 모서리가 뾰족해야 해"): 완칼(둥근
    # join_style=1, 실루엣 굴곡을 매끄럽게 따라가야 하는 경우)과 달리, 도무송
    # 정사각형/직사각형은 실제 금형이 각진 물리적 모서리라 buffer로 바깥으로
    # 밀어도 90도 각이 그대로 살아있어야 한다 -- join_style=2(mitre)로 바꿔
    # 모서리를 뾰족하게 유지한다(원형/타원형 도무송은 애초에 각진 모서리가
    # 없어 이 값을 바꿔도 시각적으로 차이가 없음, 회귀 없음).
    join_style = 2 if cutline_type.is_domusong else 1
    offsets = compute_offsets(
        design, dpi, offset_mm_adj, join_style=join_style,
        merge_gap_mm=merge_gap_mm, clip_bounds=bounds_px,
    )

    return CutlineResult(
        dpi=dpi,
        width_px=w,
        height_px=h,
        design=design,
        offsets=offsets,
        offset_mm=offset_mm_adj,
        adjustments=adjustments,
    )


def generate_cutline_by_style(
    image_path: str,
    style: ImageStyle,
    dpi: float,
    selection_px: Optional[tuple] = None,
    bounds_px: Optional[tuple] = None,
    margin_mm: float = 1.5,
    grabcut_margin_px: int = 40,
    supersample: int = 4,
    sibling_boxes_px: Optional[list] = None,
    precomputed_content_px=None,
    art_region_px=None,
) -> CutlineResult:
    """
    The real, everyday case (core.image_style): 유테 (line-art character on
    a colored background -- cut OUTWARD around the silhouette) or 무테
    (borderless/frame art that fills its own cell -- cut INWARD from that
    cell's rectangle). Exactly ONE line, at exactly `margin_mm` -- except
    `margin_mm` is never allowed below `core.image_style.MIN_STYLE_MARGIN_MM`
    (a real, too-tight or 0 value no longer passes straight through; it is
    bumped up and the bump is recorded in `adjustments`, same as the
    multi-tier workflow's own minimum-gap bump). `supersample` (GUI "정밀도")
    is likewise capped for large selections -- see
    `core.cutline_core.auto_supersample` -- with any cap also recorded here.

    Wraps core.image_style.generate_style_cutline into a full CutlineResult
    so it's a drop-in for the same render_preview()/export_svg() calls the
    multi-tier workflow uses.

    `precomputed_content_px`: core.image_style.generate_style_cutline로 그대로
    전달되는 통과 인자 -- 트라이맵 힌트로 이미 보정된 실루엣을 재사용할 때만
    쓴다(그 함수 문서 참고). 기본값 None은 기존과 동일하게 동작.
    """
    notes: list = []
    line = generate_style_cutline(
        image_path,
        style,
        dpi,
        selection_px=selection_px,
        bounds_px=bounds_px,
        margin_mm=margin_mm,
        grabcut_margin_px=grabcut_margin_px,
        supersample=supersample,
        note_sink=notes,
        sibling_boxes_px=sibling_boxes_px,
        precomputed_content_px=precomputed_content_px,
        art_region_px=art_region_px,
    )
    if isinstance(line, Polygon):
        line_mp = MultiPolygon([line]) if not line.is_empty else MultiPolygon([])
    else:
        line_mp = line

    with Image.open(image_path) as im:
        w, h = im.size

    from .image_style import MIN_STYLE_MARGIN_MM
    effective_margin_mm = max(margin_mm, MIN_STYLE_MARGIN_MM)

    return CutlineResult(
        dpi=dpi,
        width_px=w,
        height_px=h,
        design=line_mp,
        offsets={"cut": line_mp},
        offset_mm=OffsetSpec(
            safety_mm=effective_margin_mm, cut_mm=effective_margin_mm, bleed_mm=effective_margin_mm
        ),
        adjustments=notes,
    )


_SHEET_BG_CACHE: dict = {}


def _sheet_background_bgr(image_path: str, img=None):
    """시트 여백(= 인쇄 안 된 종이/배경)의 색. 이미지 맨 바깥 둘레 몇 px의
    중앙값으로 잰다 -- 실제 파일들은 격자 밖에 흰 여백과 재단 표시가 있다.
    둘레 색이 제각각이면(그림이 이미지 끝까지 꽉 찬 파일) 흰색으로 본다."""
    import numpy as np

    import os
    try:
        st = os.stat(image_path)
        key = (image_path, st.st_mtime_ns, st.st_size)  # 같은 작업 파일 이름에 다른 도안이 덮어써질 수 있음
    except OSError:
        key = None
    if key is not None and key in _SHEET_BG_CACHE:
        return _SHEET_BG_CACHE[key]
    if img is None:
        import cv2
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    t = max(3, min(img.shape[:2]) // 200)
    ring = np.concatenate([
        img[:t].reshape(-1, 3), img[-t:].reshape(-1, 3),
        img[:, :t].reshape(-1, 3), img[:, -t:].reshape(-1, 3),
    ]).astype(np.float32)
    med = np.median(ring, axis=0)
    spread = float(np.median(np.linalg.norm(ring - med, axis=1)))
    bg = med if spread < 12.0 else np.array([255.0, 255.0, 255.0], dtype=np.float32)
    if key is not None:
        _SHEET_BG_CACHE[key] = bg
    return bg


def image_outer_region_px(image_path: str, rect_px: tuple, min_fill_ratio: float = 0.05):
    """`rect_px`(칸/선택 영역) 안에서 실제로 인쇄된 이미지의 바깥 윤곽을
    Polygon(원본 픽셀 좌표)으로 돌려준다. 없으면(배경뿐인 빈 칸) None.

    2026-09-28 멍푸: "무테 ... 칼선이 이미지 외곽에 들어가야함. 배경색이
    칼선으로 잡히면 안 됨". 칸 사각형을 그대로 믿으면, 칸이 실제 그림보다
    크거나(흰 여백 포함) 아예 빈 칸일 때 배경색 위에 칼선이 생긴다(실제
    파일로 확인: 배경뿐인 빈 칸에 사각형 칼선). 그래서 시트 배경색과
    다른 픽셀을 "이미지"로 보고, 그 가장 큰 덩어리의 바깥 윤곽(안쪽 구멍은
    메움)을 이미지 외곽으로 쓴다. 그림 안의 색 경계(줄무늬 등)는 전혀 보지
    않는다 -- 바깥 윤곽만 쓰므로 배경색·무늬 경계가 칼선이 되지 않는다.
    거의 사각형인 이미지(카드)는 그 bbox로 맞춰 모서리를 깔끔하게 한다."""
    import cv2
    import numpy as np

    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    bg = _sheet_background_bgr(image_path, img)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in rect_px]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = img[y0:y1, x0:x1].astype(np.float32)
    mask = (np.linalg.norm(crop - bg, axis=2) > 12.0).astype(np.uint8) * 255
    k = max(3, (min(x1 - x0, y1 - y0) // 100) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < min_fill_ratio * (x1 - x0) * (y1 - y0) or len(c) < 3:
        return None
    poly = Polygon([(float(p[0][0]) + x0, float(p[0][1]) + y0) for p in c]).buffer(0)
    if poly.is_empty:
        return None
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    bx0, by0, bx1, by1 = poly.bounds
    bbox_area = (bx1 - bx0) * (by1 - by0)
    if bbox_area > 0 and poly.area / bbox_area >= 0.97:
        return Polygon([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])
    return Polygon(poly.exterior).simplify(1.0)


def generate_borderless_cut_from_silhouette(
    image_path: str,
    silhouette_px,
    dpi: float,
    margin_mm: float = 1.2,
    note: Optional[str] = None,
) -> CutlineResult:
    """이미 구한 요소 실루엣(원본 좌표 Polygon)으로 무테 칼선(그림 안쪽 margin_mm)
    하나를 만든다 -- core.image_style._inset_inside_art와 같은 "그림 밖으로
    절대 안 나감" 규칙. 너무 가늘어 안쪽 칼선이 사라지면 빈 결과."""
    from .image_style import MIN_STYLE_MARGIN_MM, _inset_inside_art

    margin_mm = max(margin_mm, MIN_STYLE_MARGIN_MM)
    with Image.open(image_path) as im:
        w, h = im.size
    line = _inset_inside_art(silhouette_px, mm_to_px(margin_mm, dpi))
    if line is None or line.is_empty:
        mp = MultiPolygon([])
    else:
        mp = MultiPolygon([line]) if isinstance(line, Polygon) else line
    notes = [note] if note else []
    return CutlineResult(
        dpi=dpi, width_px=w, height_px=h, design=mp, offsets={"cut": mp},
        offset_mm=OffsetSpec(safety_mm=margin_mm, cut_mm=margin_mm, bleed_mm=margin_mm),
        adjustments=notes,
    )


def _inscribed_domusong_shape(inner, cutline_type: CutlineType):
    """`inner`(이미지 외곽을 칼선 여유만큼 줄인 영역) 안에 완전히 들어가는
    가장 큰 도무송 도형. 원은 영역에서 가장 깊은 점(polylabel)을 중심으로,
    나머지는 영역의 bbox에 맞춘 뒤 영역 안에 다 들어갈 때까지 중심 기준으로
    조금씩 줄인다. 완칼(FULL_CUT)은 영역 모양 그대로."""
    from shapely.affinity import scale as _scale
    from shapely.geometry import Point
    from shapely.ops import polylabel

    if cutline_type == CutlineType.FULL_CUT:
        return inner
    if cutline_type == CutlineType.CIRCLE:
        # 가장 큰 원이 들어가는 점(polylabel). 다만 가로로 긴 카드처럼 그런
        # 점이 한 줄로 여러 개면 한쪽으로 치우칠 수 있어, 이미지 가운데에서도
        # 거의 같은 크기의 원이 들어가면 가운데를 쓴다(중심 정렬).
        center = polylabel(inner, tolerance=1.0)
        r = inner.exterior.distance(center)
        bx0, by0, bx1, by1 = inner.bounds
        mid = Point((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
        if inner.contains(mid):
            r_mid = inner.exterior.distance(mid)
            if r_mid >= 0.98 * r:
                center, r = mid, r_mid
        return Point(center.x, center.y).buffer(r, resolution=64) if r > 0 else Polygon()
    x0, y0, x1, y1 = inner.bounds
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, h = x1 - x0, y1 - y0
    if cutline_type == CutlineType.SQUARE:
        side = min(w, h)
        shape = Polygon([(cx - side / 2, cy - side / 2), (cx + side / 2, cy - side / 2),
                         (cx + side / 2, cy + side / 2), (cx - side / 2, cy + side / 2)])
    elif cutline_type == CutlineType.ELLIPSE:
        shape = _scale(Point(cx, cy).buffer(1.0, resolution=64), xfact=w / 2.0, yfact=h / 2.0)
    else:  # RECTANGLE
        shape = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    tol = inner.buffer(0.5)
    for _ in range(80):
        if tol.contains(shape):
            return shape
        shape = _scale(shape, xfact=0.98, yfact=0.98, origin=(cx, cy))
    return shape if tol.contains(shape) else Polygon()


def generate_domusong_inside_cutline(
    image_path: str,
    region_px: tuple,
    cutline_type: CutlineType,
    dpi: float,
    offset_mm: OffsetSpec,
) -> CutlineResult:
    """
    2026-09-28 멍푸: "도무송 직접 선택 후 칼선 종류에 따라 이미지 안쪽으로
    칼선 생성" (9/7 "도무송은 모두 이미지 안쪽으로 칼선이 들어가야해", 9/9
    "도무송은 칼선이 이미지 안에 있어야 해. 그게 정답이야"와 같은 방향).

    예전 도무송은 내용(실루엣) bbox에 도형을 씌우고 바깥으로 여유를 밀어서,
    카드형 시트에서는 도형이 카드 밖으로 나가거나 이웃 도형과 겹쳤다.
    이제는 선택한 영역 안의 실제 이미지 외곽(`image_outer_region_px`)을
    기준으로:
      - 칼선: 이미지 외곽에서 블리딩 폭(bleed_mm, 최소 MIN_DOMUSONG_GAP_MM=
        2.0mm)만큼 안쪽 영역에 들어가는 가장 큰 도형(원/타원/정사각/직사각,
        완칼은 이미지 외곽 모양 그대로). 실제 테스트 파일에서 카드 칸의 손
        칼선이 이미지 외곽 2.8~3.3mm 안쪽으로 실측됨 -- 기본 블리딩 3mm와 일치.
      - 블리딩: 칼선에서 같은 폭만큼 바깥(= 이미지 외곽까지) -- 칼선 밖으로
        인쇄가 이어지는 부분이 블리딩이라는 뜻 그대로.
    세이프티 선은 도무송 화면 규칙대로 만들지 않는다. 모든 선이 이미지
    안쪽에만 있다.
    """
    notes: list = []
    inset_mm = float(offset_mm.bleed_mm)
    if inset_mm < MIN_DOMUSONG_GAP_MM:
        notes.append(
            f"도무송 블리딩 폭 {inset_mm:g}mm가 최소 여유보다 작아 {MIN_DOMUSONG_GAP_MM:g}mm로 "
            f"맞췄습니다(이미지 외곽에서 칼선까지)."
        )
        inset_mm = MIN_DOMUSONG_GAP_MM

    with Image.open(image_path) as im:
        w, h = im.size
    region = image_outer_region_px(image_path, region_px)
    if region is None:
        x0, y0, x1, y1 = region_px
        region = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        notes.append("⚠ 도무송: 이 영역에서 이미지 외곽을 찾지 못해 선택 영역 사각형을 기준으로 했습니다.")
    inner = region.buffer(-mm_to_px(inset_mm, dpi), join_style=2)
    if isinstance(inner, MultiPolygon):
        inner = max(inner.geoms, key=lambda g: g.area)
    if inner.is_empty:
        raise ValueError(f"이미지가 너무 작아 {inset_mm:g}mm 안쪽에 도무송 칼선을 넣을 수 없습니다.")
    shape = _inscribed_domusong_shape(inner, cutline_type)
    if shape is None or shape.is_empty:
        raise ValueError("이미지 안쪽에 들어가는 도무송 도형을 만들지 못했습니다.")
    join = 1 if cutline_type in (CutlineType.CIRCLE, CutlineType.ELLIPSE, CutlineType.FULL_CUT) else 2
    bleed = shape.buffer(mm_to_px(inset_mm, dpi), join_style=join).intersection(region)
    if isinstance(bleed, MultiPolygon):
        bleed = max(bleed.geoms, key=lambda g: g.area)
    notes.append(
        f"도무송({cutline_type.value}): 이미지 외곽에서 {inset_mm:g}mm 안쪽에 칼선, "
        f"그 바깥(이미지 외곽까지)이 블리딩 -- 모든 선이 이미지 안쪽입니다."
    )
    shape_mp = MultiPolygon([shape])
    return CutlineResult(
        dpi=dpi,
        width_px=w,
        height_px=h,
        design=shape_mp,
        offsets={"cut": shape_mp, "bleed": MultiPolygon([bleed])},
        offset_mm=OffsetSpec(safety_mm=inset_mm, cut_mm=inset_mm, bleed_mm=inset_mm),
        adjustments=notes,
    )


def _rect_trim_cutline(
    selection_px: tuple, dpi: float, margin_mm: float, bounds_px: Optional[tuple]
) -> Polygon:
    """
    A plain rectangle, buffered OUTWARD from `selection_px` by `margin_mm`
    -- no segmentation at all. Built for small pattern/texture "prop" assets
    (a plaid fabric-swatch icon, a snack icon -- anything printed as a
    uniform little block with no distinguishing edge of its own, as opposed
    to a character with a real silhouette to hug).

    Why this exists, exactly as the artist described it: for these small
    assets, the real production cutline does not trace a silhouette at all
    -- it just cuts a plain rectangle THROUGH the printed patch (the
    artist deliberately over-prints a bit more pattern than the final
    sticker needs, then the die trims a defined rectangle out of it,
    discarding the rest -- "재단된 이미지를 스티커로 사용"). Tracing a
    GrabCut silhouette for this kind of asset is the wrong model even when
    it "succeeds": a busy plaid/checker pattern's edge is exactly the
    lightly-contrasting, ambiguous case GrabCut struggles with (the same
    root cause behind this project's other low-contrast segmentation
    misses), so trusting its detected boundary as ground truth for an
    outward trace can still land inside the true printed edge. A plain
    rectangle sized directly from the given selection sidesteps that
    entirely -- there is no silhouette to get wrong.
    """
    x0, y0, x1, y1 = selection_px
    rect = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    margin_px = mm_to_px(margin_mm, dpi)
    line = rect.buffer(margin_px, join_style=1, resolution=24)
    if bounds_px is not None:
        bx0, by0, bx1, by1 = bounds_px
        bounds_rect = Polygon([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])
        line = line.intersection(bounds_rect)
    return line


def generate_cutline_from_known_silhouette(
    reference_silhouette_px: Polygon,
    dpi: float,
    bounds_px: Optional[tuple] = None,
    presmooth_mm: float = 0.3,
) -> CutlineResult:
    """
    Use an ALREADY-KNOWN-GOOD real cutline polygon directly as the new
    cutline's shape (only lightly corner-smoothed), instead of trying to
    re-derive a silhouette from pixels.

    Why this exists, and why it isn't "just copying the old line":
    for small mounted/patterned prop assets (a striped snack swatch, a
    plaid fabric swatch -- each drawn as a colorful pattern sitting on its
    own pale rounded-rect "mount"/backing shape), this project tried THREE
    different ways to re-detect the true printed outline from pixels, and
    all three failed the same way, confirmed by direct visual/numeric
    comparison against the real hand-drawn line for this exact file:
      1. GrabCut segmentation (core.segmentation) traced only the
         high-contrast pattern core, missing the pale mount entirely --
         confirmed visually (scratch/overlay_elem2.png, overlay_elem8.png):
         the segmented (blue) outline sits well inside the real (red) one.
      2. Re-running GrabCut with a hard "sure foreground" seed placed in
         the pattern's center (to anchor the mount's color into the
         foreground model from the start) produced the IDENTICAL result --
         GrabCut's graph-cut always snaps to the strongest nearby color
         edge (pattern-to-mount), never the much weaker mount-to-background
         edge, regardless of trimap seeding.
      3. Plain per-pixel color-distance thresholding against the sampled
         local background color also failed outright -- measured directly
         on this file, the mount's own color (BGR ~193,224,243) is within
         one standard deviation of the surrounding scene background's own
         sampled color (mean ~176,215,239, std ~46,24,14): the mount and
         the scene behind it are, in real measured pixel color, too close
         to separate by color at all in some regions.
    So for exactly these elements, no pixel-based re-segmentation can be
    trusted -- any margin we computed on top of a wrongly-detected
    silhouette would be built on a guess, not a measurement. But this is a
    REDRAW of an existing sheet, not new artwork: the real hand-drawn
    cutline for these specific elements is already known, already
    validated production geometry (exactly the polygon this function's
    caller already extracted from the file's own 칼선레이어 to seed the
    selection rectangle in the first place). Reusing ITS shape directly --
    the same category of decision this project already made for the bunny
    corner's 도무송 cell (a known-good boundary reused directly rather than
    re-derived from an undetectable silhouette) -- is the honest choice,
    not a shortcut around doing the work.

    `presmooth_mm`: a small buffer-out/buffer-in round-trip (NOT an
    additional outward margin -- net effect on size is ~0) purely to round
    off any tiny extraction jaggedness from Bezier-curve flattening,
    consistent with the corner-finishing already applied elsewhere in this
    round's redraw ("모서리 부분을 잘 마무리").
    """
    presmooth_px = mm_to_px(presmooth_mm, dpi)
    line = reference_silhouette_px.buffer(
        presmooth_px, join_style=1, resolution=16
    ).buffer(-presmooth_px, join_style=1, resolution=16)
    if bounds_px is not None:
        bx0, by0, bx1, by1 = bounds_px
        bounds_rect = Polygon([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])
        line = line.intersection(bounds_rect)
    line_mp = MultiPolygon([line]) if isinstance(line, Polygon) else line
    return CutlineResult(
        dpi=dpi,
        width_px=0,
        height_px=0,
        design=line_mp,
        offsets={"cut": line_mp},
        offset_mm=OffsetSpec(safety_mm=0.0, cut_mm=0.0, bleed_mm=0.0),
        adjustments=[
            "소형 요소: 실루엣 재검출 대신 실제 손그린 칼선 도형을 그대로 사용 "
            "(GrabCut/색상거리 3가지 방법 모두 이 종류 요소에서 실패 확인됨)"
        ],
    )


def has_white_or_black_border(image_path: str, silhouette_px, dpi: float) -> bool:
    """요소 실루엣 가장자리에 흰색(또는 검은색) *테두리 선*이 있는지 -- 유테의
    정의(docs/개념정리.md: "캐릭터 몸 바깥에 일정한 두께의 흰색/검은색
    테두리 선"). 가장자리 띠(0~0.6mm)가 대부분 흰색/검은색이고, 그보다 안쪽
    띠(2.5~3.5mm)는 그렇지 않을 때 True -- 몸 전체가 흰 꽃처럼 속까지
    흰색인 요소는 테두리가 아니라 그 그림 자체라 False. 실루엣이 테두리
    안쪽에서 잡힌 경우는 바깥 띠로 확인한다(아래)."""
    import cv2
    import numpy as np

    if silhouette_px is None or silhouette_px.is_empty:
        return False
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return False
    px = mm_to_px(1.0, dpi)
    rim = silhouette_px.difference(silhouette_px.buffer(-0.6 * px))
    inner = silhouette_px.buffer(-2.5 * px).difference(silhouette_px.buffer(-3.5 * px))

    def frac_bw(geom):
        if geom is None or geom.is_empty:
            return None
        x0, y0, x1, y1 = [int(v) for v in geom.bounds]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.shape[1], x1 + 1), min(img.shape[0], y1 + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        polys = geom.geoms if hasattr(geom, "geoms") else [geom]
        for p in polys:
            if p.is_empty or p.geom_type != "Polygon":
                continue
            ext = np.array([[int(x - x0), int(y - y0)] for x, y in p.exterior.coords], np.int32)
            cv2.fillPoly(mask, [ext], 255)
            for hole in p.interiors:
                hh = np.array([[int(x - x0), int(y - y0)] for x, y in hole.coords], np.int32)
                cv2.fillPoly(mask, [hh], 0)
        pix = img[y0:y1, x0:x1][mask > 0].astype(np.int32)
        if len(pix) < 20:
            return None
        mx, mn = pix.max(axis=1), pix.min(axis=1)
        white = (mn >= 225) & (mx - mn <= 25)
        black = mx <= 60
        return float((white | black).mean())

    rim_f = frac_bw(rim)
    if rim_f is not None and rim_f >= 0.5:
        inner_f = frac_bw(inner)
        return inner_f is None or inner_f < 0.5
    # 실루엣이 흰 테두리 *안쪽* 그림 가장자리에서 잡힌 경우(흰 테두리가
    # 실루엣에서 빠짐 -- 실제 흰 테두리 스티커 시트에서 확인): 바로 바깥 띠가
    # 흰색/검은색이고 그보다 더 바깥(2.5~3.5mm)은 아니면 = 얇은 테두리 선.
    # (흰 종이 위에 그냥 놓인 그림은 더 바깥도 흰색이라 테두리로 안 봄.)
    outer = silhouette_px.buffer(0.8 * px).difference(silhouette_px)
    far = silhouette_px.buffer(3.5 * px).difference(silhouette_px.buffer(2.5 * px))
    outer_f = frac_bw(outer)
    if outer_f is None or outer_f < 0.5:
        return False
    far_f = frac_bw(far)
    return far_f is None or far_f < 0.5


def generate_cutline_auto(
    image_path: str,
    dpi: float,
    selection_px: tuple,
    bounds_px: Optional[tuple] = None,
    margin_mm: float = 1.5,
    grabcut_margin_px: int = 40,
    rectangularity_threshold: Optional[float] = None,
    small_element_max_dim_mm: Optional[float] = None,
    reference_silhouette_px: Optional[Polygon] = None,
    supersample: int = 4,
    sibling_boxes_px: Optional[list] = None,
    art_region_px=None,
) -> CutlineResult:
    """
    The "실무 기본값" auto-detect entry point: segments the ONE thing inside
    `selection_px` (still a rough drag, still works on colored/busy
    backgrounds -- core.segmentation) ONLY to decide which style applies --
    유테 vs 무테 (core.style_classify), FROM THAT CONTENT'S OWN SHAPE --
    instead of requiring the artist to pick a radio button every time.

    Rationale, validated against two real production sheets (see
    core.style_classify's module docstring): a free-floating character
    segments into an organic shape (low rectangularity) -- cut OUTWARD,
    hugging it, by `margin_mm` (유테). A design already built around its
    own rectangular box/frame (a strongly-contrasting fill color makes the
    box's straight edges part of what GrabCut segments as foreground)
    segments into a near-rectangular shape (high rectangularity) -- cut
    INWARD (무테).

    IMPORTANT: once the STYLE is picked, the actual line for 무테 is drawn
    from `selection_px` itself (the artist's own drag rectangle == that
    design's cell), NOT from the segmented content's bounding box. This
    matters because segmentation can UNDER-capture a full-cell design when
    its background color has low contrast against the surrounding sheet
    (measured failure case: a pale-lavender framed corner design segmented
    to only 0.585 rectangularity -- GrabCut caught the bunny + ornaments
    but not the flat lavender fill -- even though its REAL cutline is a
    clean 1.000-rectangularity cell boundary). Classification from content
    shape is a useful signal for the common case but is not bulletproof;
    geometry always falls back to the reliable, previously-validated
    cell-rectangle-based inset (core.image_style's BORDERLESS branch) once
    무테 is chosen, rather than compounding one imperfect measurement into
    another.

    `small_element_max_dim_mm`: when given, any selection whose longer side
    (in mm, via `dpi`) is at or under this size skips segmentation/style
    classification ENTIRELY -- small pattern/prop assets don't have a real
    silhouette worth (re-)tracing, and forcing one (유테's GrabCut trace,
    무테's cell-inset, or even a plain rectangle) has each in turn produced
    a cutline that either lands inside the true printed edge or overlaps a
    neighboring element's own line (confirmed directly by the artist on a
    real file across three rejected attempts). Measured on that same file:
    every real character was 25mm+ on its longer side and every small
    prop/accessory was under 14mm -- a clean gap, so a threshold around
    20mm is a safe default when the caller doesn't have a more specific
    number for their own file.

    When `reference_silhouette_px` is ALSO given (the real, already
    hand-drawn cutline polygon for this exact element, in the same pixel
    space as `selection_px` -- available whenever this is a redraw of an
    existing sheet, which is the common case this function was built for),
    the small-element path reuses that real shape directly via
    `generate_cutline_from_known_silhouette` -- see that function's
    docstring for why pixel re-segmentation is untrustworthy for this asset
    class specifically, and why reusing the validated real geometry is the
    honest choice rather than a shortcut. Without a reference silhouette
    (e.g. genuinely new artwork with no prior cutline at all), this falls
    back to `_rect_trim_cutline` as a best-effort default that should be
    treated as provisional and checked by the artist.

    `supersample`: requested precision (GUI "정밀도" option), threaded to
    every segmentation call this function makes; automatically capped for a
    large selection (`core.cutline_core.auto_supersample`), with any cap
    recorded in the returned `adjustments`.

    `sibling_boxes_px`: 같은 칸/영역 안에서 이미 낱개로 인식된 다른
    요소들의 박스(원본 이미지 절대 좌표, 자기 자신은 뺀 목록). 유테로
    분류될 때만 실제로 쓰인다(core.image_style._grow_design_into_low_
    contrast_halo_px에 그대로 전달돼, 저대비 헤일로 확장이 그 박스들
    영역으로는 절대 번지지 않게 막는다 -- 2026-09-14, "칼선이 개체를 안
    둘러싸고 여러 개체를 휘감는다" 실제 파일 피드백으로 추가).

    Returns a CutlineResult whose `adjustments` records which style was
    auto-detected and the measured rectangularity score, so the GUI can
    show its work instead of silently guessing -- if the score looks
    borderline (as it did for that lavender-frame case), the artist can
    override by calling generate_cutline_by_style with an explicit style.
    """
    if small_element_max_dim_mm is not None:
        sx0, sy0, sx1, sy1 = selection_px
        long_side_mm = max(px_to_mm(sx1 - sx0, dpi), px_to_mm(sy1 - sy0, dpi))
        if long_side_mm <= small_element_max_dim_mm:
            if reference_silhouette_px is not None:
                result = generate_cutline_from_known_silhouette(
                    reference_silhouette_px, dpi, bounds_px=bounds_px
                )
                result.adjustments = [
                    f"자동 감지: 소형 요소({long_side_mm:.1f}mm ≤ {small_element_max_dim_mm:g}mm) -- "
                    + result.adjustments[0]
                ]
                return result
            line = _rect_trim_cutline(selection_px, dpi, margin_mm, bounds_px)
            line_mp = MultiPolygon([line]) if isinstance(line, Polygon) else line
            with Image.open(image_path) as im:
                w, h = im.size
            return CutlineResult(
                dpi=dpi,
                width_px=w,
                height_px=h,
                design=line_mp,
                offsets={"cut": line_mp},
                offset_mm=OffsetSpec(safety_mm=margin_mm, cut_mm=margin_mm, bleed_mm=margin_mm),
                adjustments=[
                    f"자동 감지: 소형 요소({long_side_mm:.1f}mm ≤ {small_element_max_dim_mm:g}mm), "
                    f"참조 칼선 없음 -- 실루엣 추적 없이 선택 영역 사각형을 그대로 "
                    f"{margin_mm:g}mm 바깥으로 재단 (임시값, 확인 필요)"
                ],
            )

    threshold = (
        rectangularity_threshold
        if rectangularity_threshold is not None
        else DEFAULT_RECTANGULARITY_THRESHOLD
    )
    classify_notes: list = []
    content = segment_design_in_region(
        image_path, selection_px, margin_px=grabcut_margin_px,
        supersample=supersample, note_sink=classify_notes,
    )
    classification = classify_style(content, threshold=threshold, selection_px=selection_px)
    style = classification.style
    border_note = None
    # 2026-09-28(테스트 폴더 실제 손 칼선 대조): "사방에 여백이 있으면 유테"
    # (할로 우선) 판정만으로는, 테두리 없이 그린 무테 캐릭터(배경 위에 떠 있는
    # 요소)도 전부 유테(바깥 칼선)로 판정됐다 -- 실제 무테 시트에서 칼선이
    # 요소 1mm 안쪽인데 우리는 바깥으로 잘라 경계가 약 2.9mm 어긋남(IoU 0.64).
    # 유테의 정의("테두리 외곽선이 있는 이미지" -- 흰색/검은색 테두리 선)대로,
    # 실루엣 가장자리 띠가 흰색/검은색 테두리일 때만 유테로 두고 아니면 무테
    # (안쪽)로 한다. 실측: 흰 테두리 스티커 시트는 유테 쪽이 실제와 더 맞고
    # (IoU 0.91), 무테 시트는 무테 쪽이 맞음(IoU 0.90).
    # 2026-09-29(멍푸 PC에서 실제 사용 대조): 할로 판정이 아닌 경로(사각형도가
    # 기준보다 낮아 "사각형이 아니니 유테")로도 테두리 없는 캐릭터가 유테가 되어
    # 칼선이 배경으로 나갔다(실제 격자 파일: 초록 캐릭터, 검은 바지 캐릭터).
    # 유테는 흰색/검은색 테두리 선이 실제로 있을 때만 -- 판정 경로와 상관없이.
    if style == ImageStyle.LINE_ART:
        if not has_white_or_black_border(image_path, content, dpi):
            style = ImageStyle.BORDERLESS
            border_note = (
                "자동 감지: 무테 (흰색/검은색 테두리 선이 없어, 요소 안쪽으로 자름)"
            )

    result = generate_cutline_by_style(
        image_path,
        style,
        dpi,
        selection_px=selection_px,
        bounds_px=bounds_px,
        margin_mm=margin_mm,
        grabcut_margin_px=grabcut_margin_px,
        supersample=supersample,
        sibling_boxes_px=sibling_boxes_px,
        art_region_px=art_region_px,
    )
    if border_note is not None:
        note = border_note
    elif classification.halo_detected:
        note = (
            f"자동 감지: {classification.style.value} "
            f"(사각형도 {classification.rectangularity:.2f}지만, 선택 영역 사방에 실제 여백이 있어 "
            f"할로 우선 판정으로 유테 적용)"
        )
    else:
        note = (
            f"자동 감지: {classification.style.value} "
            f"(사각형도 {classification.rectangularity:.2f}, 기준 {classification.threshold:.2f})"
        )
    # keep any auto-adjustment notes already collected inside
    # generate_cutline_by_style (precision cap, style-margin floor bump) --
    # this classification note is additional context, not a replacement.
    result.adjustments = classify_notes + [note] + result.adjustments
    return result
