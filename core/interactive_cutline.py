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

    result = generate_cutline_by_style(
        image_path,
        classification.style,
        dpi,
        selection_px=selection_px,
        bounds_px=bounds_px,
        margin_mm=margin_mm,
        grabcut_margin_px=grabcut_margin_px,
        supersample=supersample,
        sibling_boxes_px=sibling_boxes_px,
    )
    if classification.halo_detected:
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
