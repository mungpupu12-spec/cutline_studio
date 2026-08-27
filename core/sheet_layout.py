"""
Place individual design images into a real print-vendor sheet guide's slot
grid, then hand the whole composited sheet to the existing cutline pipeline
(core.cutline_core.generate_cutlines already handles multiple disjoint
shapes on one image -- that part needed no changes).

The guide's own rule -- "칼선/재단 라인을 넘어가지 않도록 작업해주세요" -- is
enforced explicitly: each design is centered on its actual non-transparent
content (not the PNG canvas, which may have extra transparent padding), and
if it still doesn't fit inside its slot after fitting, that is reported as a
violation instead of being silently cropped.

2026-08-25 revision: on review of a real finished production file, the
previous default (place at native pixel size, flag oversized art as a
violation) turned out to be wrong for the common case -- most real designs
are exported at a much higher resolution than any single slot and are
*meant* to be scaled down to fit, not flagged. Auto-fit-to-slot (preserving
aspect ratio, via `auto_fit`/`fit_margin_mm`) briefly became the DEFAULT.

2026-08-26 reversal: direct artist feedback -- "도안을 슬롯에 맞춰 몰래
축소하지 않는다" (don't secretly shrink the design to fit the slot). Silently
resizing real production artwork is exactly the kind of change that should
never happen without the artist seeing it: a design placed at the wrong
physical size is a real print-quality problem (a shrunk master can end up
printed smaller than the artist intended, or upscaled/blurred if the slot
is unexpectedly larger), and finding out only after checking a numeric
report is too easy to miss. `auto_fit` is now `False` by DEFAULT again --
oversized art is reported as a `violation` (as before 2026-08-25) instead of
silently resized. `auto_fit=True` is still available for the cases where
scaling down really is wanted, but even then every actual resize is now
recorded on the placement's `resize_note` (never folded silently into a
successful placement with no trace) so it is always visible to whoever
reads the result, not just when it happens to also violate the slot.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .guide import SheetGuide, pt_to_mm, pt_to_px


@dataclass
class SlotPlacement:
    index: int
    image_path: str | None
    slot_rect_px: tuple  # (x0,y0,x1,y1) in the composite sheet's px space
    content_bbox_px: tuple | None  # actual placed design's content bbox, or None if empty
    violation: str | None  # human-readable note if the design exceeds the slot
    resize_note: str | None = None  # non-None whenever auto_fit actually scaled this design down -- always populated, so a resize is never silent even when it succeeds


def place_designs_into_sheet(
    guide: SheetGuide,
    image_paths: list,
    dpi: float = 300.0,
    out_path: str = "sheet_composite.png",
    target_sizes_mm: list | None = None,
    auto_fit: bool = False,
    fit_margin_mm: float = 0.0,
) -> tuple:
    """
    Places each image into consecutive guide slots in reading order, centered
    on its actual non-transparent content (not the PNG canvas, which may
    have extra transparent padding).

    Sizing priority, per image:
      1. `target_sizes_mm[i]` if given -- explicit physical size, always wins.
      2. otherwise (`auto_fit=False`, the DEFAULT): place at native pixel
         size (interpreted at `dpi`) and report a violation if it doesn't
         fit -- nothing is ever resized without the caller asking for it.
      3. otherwise (`auto_fit=True`, opt-in): fit the design's content into
         the slot automatically, preserving aspect ratio and leaving
         `fit_margin_mm` of clearance on each side (so the die-cut line
         generated afterwards -- which sits *outside* the art -- still has
         room to stay inside the slot boundary instead of immediately
         violating it). Every placement that actually gets scaled down this
         way carries a `resize_note` recording the before/after size, so the
         resize is always visible in the returned report -- never folded
         silently into a placement that just "succeeded".
    """
    sheet_w_px = int(round(pt_to_px(guide.page_w_pt, dpi)))
    sheet_h_px = int(round(pt_to_px(guide.page_h_pt, dpi)))
    canvas = Image.new("RGBA", (sheet_w_px, sheet_h_px), (0, 0, 0, 0))

    n_slots = len(guide.slot_rects_pt)
    if len(image_paths) > n_slots:
        raise ValueError(
            f"이미지가 {len(image_paths)}장인데 가이드 슬롯은 {n_slots}칸뿐입니다 "
            f"-- 시트를 나눠서 작업하세요."
        )

    placements = []
    for i, slot_rect_pt in enumerate(guide.slot_rects_pt):
        sx0 = pt_to_px(slot_rect_pt[0], dpi)
        sy0 = pt_to_px(slot_rect_pt[1], dpi)
        sx1 = pt_to_px(slot_rect_pt[2], dpi)
        sy1 = pt_to_px(slot_rect_pt[3], dpi)
        slot_w, slot_h = sx1 - sx0, sy1 - sy0
        slot_px = (sx0, sy0, sx1, sy1)

        if i >= len(image_paths):
            placements.append(SlotPlacement(i, None, slot_px, None, None))
            continue

        img_path = image_paths[i]
        design_img = Image.open(img_path).convert("RGBA")
        resize_note = None

        if target_sizes_mm:
            target_mm = target_sizes_mm[i]
        elif auto_fit:
            slot_w_mm = pt_to_mm(slot_rect_pt[2] - slot_rect_pt[0])
            slot_h_mm = pt_to_mm(slot_rect_pt[3] - slot_rect_pt[1])
            target_mm = (
                max(0.1, slot_w_mm - 2 * fit_margin_mm),
                max(0.1, slot_h_mm - 2 * fit_margin_mm),
            )
        else:
            target_mm = None

        if target_mm is not None:
            target_w_px = target_mm[0] / 25.4 * dpi
            target_h_px = target_mm[1] / 25.4 * dpi
            # scale so the CONTENT (not the padded canvas) fits target_mm,
            # preserving aspect ratio -- fit to the tighter dimension
            probe_alpha = design_img.getchannel("A")
            probe_box = probe_alpha.getbbox()
            if probe_box is not None:
                pcw, pch = probe_box[2] - probe_box[0], probe_box[3] - probe_box[1]
                fit_scale = min(target_w_px / pcw, target_h_px / pch)
                if not target_sizes_mm:
                    # auto_fit only ever SHRINKS oversized master art to fit
                    # the slot -- it never enlarges already-small art just
                    # because the slot has room, which would upscale (and
                    # blur) it for no reason. An explicit target_sizes_mm
                    # request is taken literally either way.
                    fit_scale = min(fit_scale, 1.0)
                if fit_scale < 1.0:
                    before_w_mm = pcw / dpi * 25.4
                    before_h_mm = pch / dpi * 25.4
                    after_w_mm = before_w_mm * fit_scale
                    after_h_mm = before_h_mm * fit_scale
                    resize_note = (
                        f"슬롯 {i+1}: 도안을 슬롯에 맞춰 "
                        f"{before_w_mm:.1f}×{before_h_mm:.1f}mm -> "
                        f"{after_w_mm:.1f}×{after_h_mm:.1f}mm ({fit_scale*100:.0f}%)로 "
                        f"자동 축소했습니다 -- auto_fit=True로 요청됨"
                    )
                new_size = (
                    max(1, round(design_img.width * fit_scale)),
                    max(1, round(design_img.height * fit_scale)),
                )
                design_img = design_img.resize(new_size, Image.LANCZOS)

        alpha = design_img.getchannel("A")
        content_box = alpha.getbbox()  # (left, upper, right, lower) of non-zero alpha
        if content_box is None:
            # fully transparent image -- nothing to place, nothing to violate
            placements.append(
                SlotPlacement(
                    i, img_path, slot_px, None,
                    f"슬롯 {i+1}: 이미지가 완전히 투명합니다.",
                    resize_note=resize_note,
                )
            )
            continue

        cl, ct, cr, cb = content_box
        content_w, content_h = cr - cl, cb - ct

        # center the CONTENT (not the raw canvas, which may have transparent
        # padding around it) within the slot, at its own natural size
        target_cx = sx0 + slot_w / 2.0
        target_cy = sy0 + slot_h / 2.0
        paste_x = int(round(target_cx - content_w / 2.0 - cl))
        paste_y = int(round(target_cy - content_h / 2.0 - ct))
        canvas.alpha_composite(design_img, (paste_x, paste_y))

        content_bbox_px = (
            paste_x + cl,
            paste_y + ct,
            paste_x + cr,
            paste_y + cb,
        )

        violation = None
        if content_w > slot_w or content_h > slot_h:
            over_w_mm = max(0.0, (content_w - slot_w)) / dpi * 25.4
            over_h_mm = max(0.0, (content_h - slot_h)) / dpi * 25.4
            content_w_mm = content_w / dpi * 25.4
            content_h_mm = content_h / dpi * 25.4
            slot_w_mm = slot_w / dpi * 25.4
            slot_h_mm = slot_h / dpi * 25.4
            violation = (
                f"슬롯 {i+1}: 도안 실제 크기({content_w_mm:.1f}×{content_h_mm:.1f}mm)가 "
                f"가이드 슬롯 크기({slot_w_mm:.1f}×{slot_h_mm:.1f}mm)를 넘어갑니다"
                + (f" (가로 {over_w_mm:.1f}mm 초과)" if over_w_mm > 0 else "")
                + (f" (세로 {over_h_mm:.1f}mm 초과)" if over_h_mm > 0 else "")
                + " -- 가이드 라인을 넘어가지 않도록 크기를 줄이거나 나눠서 작업하세요."
            )

        placements.append(
            SlotPlacement(i, img_path, slot_px, content_bbox_px, violation, resize_note=resize_note)
        )

    canvas.save(out_path)
    return out_path, placements
