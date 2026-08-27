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

from enum import Enum
from typing import Optional

import numpy as np
from shapely.geometry import Polygon

from .cutline_core import mm_to_px, px_to_mm
from .segmentation import segment_design_in_region


class ImageStyle(Enum):
    LINE_ART = "유테"     # outlined character on a colored background -- cut OUTWARD around the silhouette
    BORDERLESS = "무테"   # fills its cell edge-to-edge -- cut INWARD from the cell's own rectangle


DEFAULT_STYLE_MARGIN_MM = 1.5

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


def _measure_content_bbox_px(image_path: str, rect_px: tuple):
    """
    Cheap, best-effort bounding box of whatever isn't background WITHIN
    `rect_px` -- deliberately NOT a full GrabCut segmentation (BORDERLESS is
    defined to skip that entirely, see this module's docstring: it trusts
    the artist's own selection/cell rectangle as ground truth, not a traced
    silhouette). This is only used as a cheap SAFETY CHECK -- alpha if the
    source has real transparency, otherwise a simple distance-from-corner-
    color threshold (same spirit as the Otsu fallback in
    core.cutline_core.load_raster_design, just without the extra denoise/
    contour machinery this check doesn't need). Returns None if the crop is
    degenerate or nothing looks like content (e.g. an empty background-only
    region), in which case the caller should skip the check rather than
    treat "None" as "content fills the whole rect".
    """
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
        corners = np.array(
            [rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]], dtype=np.int32
        )
        bg = np.median(corners, axis=0)
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
        mask = dist > 20

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return (cx0 + int(xs.min()), cy0 + int(ys.min()), cx0 + int(xs.max()) + 1, cy0 + int(ys.max()) + 1)


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
        rect = _rect_from_bounds(rect_px)
        inset_px = mm_to_px(margin_mm, dpi)
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
        if note_sink is not None:
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
                    note_sink.append(
                        f"무테 칼선 경고: 선택 영역이 실제 그림 가장자리 대비 {margin_mm:g}mm의 여유를 "
                        f"두기엔 너무 좁아서, 계산된 칼선이 실제 그림 안쪽으로 최대 약 {overflow_mm:.2f}mm "
                        f"들어갑니다 -- 이대로 쓰면 칼선이 그림을 잘라먹을 수 있습니다. 선택 영역을 더 "
                        f"넉넉하게 다시 드래그하거나 간격(margin_mm)을 줄이세요."
                    )
        return line

    # LINE_ART (유테)
    if selection_px is None:
        raise ValueError("유테(LINE_ART) 스타일은 캐릭터 하나를 가리키는 selection_px가 필요합니다.")
    design = segment_design_in_region(
        image_path, selection_px, margin_px=grabcut_margin_px,
        supersample=supersample, note_sink=note_sink,
    )

    if bounds_px is None:
        from PIL import Image
        with Image.open(image_path) as im:
            bounds_px = (0, 0, im.width, im.height)
    bounds_rect = _rect_from_bounds(bounds_px)

    # Smooth away small jagged/noisy edge detail BEFORE the main outward
    # offset: a large margin buffer amplifies any remaining pixel-scale
    # stairstep or GrabCut edge wobble into a visible little point sticking
    # out of an otherwise round line ("뾰족한 모양"). A short close-then-open
    # round-trip (out by a few px, back in by the same amount, both with a
    # round join) rubs off that micro-noise without changing the shape's
    # real proportions, so the corner-rounding of the *real* margin buffer
    # below has a clean silhouette to work from instead of a noisy one.
    presmooth_px = max(2.0, mm_to_px(0.3, dpi))
    design = design.buffer(presmooth_px, join_style=1, resolution=16).buffer(
        -presmooth_px, join_style=1, resolution=16
    )

    margin_px = mm_to_px(margin_mm, dpi)
    line = design.buffer(margin_px, join_style=1, resolution=24).intersection(bounds_rect)
    return line
