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

from .cutline_core import CutlineResult, MIN_GAP_MM, OffsetSpec, compute_offsets, mm_to_px, px_to_mm
from .cutline_types import CutlineType, build_cutline_design
from .image_style import ImageStyle, generate_style_cutline
from .segmentation import segment_design_in_region
from .style_classify import DEFAULT_RECTANGULARITY_THRESHOLD, StyleClassification, classify_style


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
    else:
        content = tuple(selection_px)

    design = build_cutline_design(content, cutline_type, circle_ellipse_pad=circle_ellipse_pad)

    with Image.open(image_path) as im:
        w, h = im.size

    if bounds_px is None:
        bounds_px = (0, 0, w, h)

    offset_mm_adj, adjustments = offset_mm.enforce_minimum_gap()
    adjustments = precision_notes + adjustments
    offsets = compute_offsets(
        design, dpi, offset_mm_adj, merge_gap_mm=merge_gap_mm, clip_bounds=bounds_px
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
