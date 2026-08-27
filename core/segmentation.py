"""
Extract a single design's silhouette from an arbitrary (possibly colored,
gradient, or busy multi-element) background, given only a rough rectangle
drawn around it -- e.g. the artist dragging a loose selection box over one
character in a finished, flattened print-layer scene.

This is the piece that makes cutline generation possible on REAL sticker
sheets: almost all real 씰스티커 work happens over a colored background (that
is exactly why it takes so long by hand), not over a clean transparent PNG.
core.cutline_core's alpha/near-white pipeline only ever handled the easy
case; this module handles the actual common case using GrabCut, an
interactive foreground/background segmentation algorithm built for exactly
this kind of "rough box around one thing on a busy background" input.

The rectangle does NOT need to be precise -- GrabCut treats it as "probably
foreground inside, definitely background outside", then iteratively refines
color-distribution models for each side. A generous, sloppy drag still
converges to a tight silhouette as long as the box fully contains the
design and mostly excludes its neighbors.
"""

from __future__ import annotations

import cv2
import numpy as np
from shapely.affinity import scale as shapely_scale, translate as shapely_translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .cutline_core import _contours_to_polygons, auto_supersample


def segment_design_in_region(
    image_path: str,
    rect_px: tuple,
    margin_px: int = 40,
    iterations: int = 5,
    supersample: int = 4,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    note_sink: list | None = None,
) -> MultiPolygon:
    """
    rect_px: (x0, y0, x1, y1) -- a rough rectangle around ONE design, in the
    FULL image's own pixel coordinates (e.g. from a drag-select in the GUI).
    Does not need to hug the design tightly; a bit of slack on every side is
    fine and expected.

    `supersample`: requested precision (see `core.cutline_core.auto_supersample`)
    -- capped down automatically based on the SELECTION CROP's own size
    (that is what actually gets upsampled here, not the full source image),
    so a small drag-selection on a huge sheet still gets full precision.
    Pass `note_sink=[]` to receive a note when the cap actually reduces it.

    Returns a MultiPolygon in the SAME full-image pixel coordinate space as
    `rect_px`, ready to feed into core.cutline_core.compute_offsets exactly
    like a design loaded via load_raster_design.
    """
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]

    x0, y0, x1, y1 = [int(round(v)) for v in rect_px]
    x0, y0 = max(0, x0 - margin_px), max(0, y0 - margin_px)
    x1, y1 = min(w, x1 + margin_px), min(h, y1 + margin_px)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Selection rectangle is empty/out of bounds.")

    crop = img[y0:y1, x0:x1]
    requested_supersample = supersample
    supersample = auto_supersample(x1 - x0, y1 - y0, requested=requested_supersample)
    if supersample != requested_supersample and note_sink is not None:
        note_sink.append(
            f"선택 영역({x1-x0}x{y1-y0}px)이 커서 정밀도를 {requested_supersample}x -> "
            f"{supersample}x로 자동 조정했습니다 (처리 속도 보호)"
        )

    # The GrabCut init rect is the artist's ORIGINAL drag box, expressed
    # relative to this crop (the margin we added around it is treated as
    # "definitely background" by virtue of being outside this inner rect).
    inner = (
        max(0, int(round(rect_px[0])) - x0),
        max(0, int(round(rect_px[1])) - y0),
        min(crop.shape[1], int(round(rect_px[2])) - x0) - max(0, int(round(rect_px[0])) - x0),
        min(crop.shape[0], int(round(rect_px[3])) - y0) - max(0, int(round(rect_px[1])) - y0),
    )

    gc_mask = np.zeros(crop.shape[:2], np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(crop, gc_mask, inner, bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_RECT)

    native_mask = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    # Same denoise + sub-pixel supersample/re-threshold pipeline used for
    # clean alpha/near-white designs, applied here to GrabCut's mask edge --
    # keeps cutline fidelity consistent regardless of which path a design
    # came in through.
    kernel = np.ones((3, 3), np.uint8)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_OPEN, kernel)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_CLOSE, kernel)

    supersample = max(1, int(supersample))
    if supersample > 1:
        ch, cw = native_mask.shape
        upsampled = cv2.resize(
            native_mask, (cw * supersample, ch * supersample), interpolation=cv2.INTER_LINEAR
        )
        blur_k = max(3, (supersample // 2) * 2 + 1)
        upsampled = cv2.GaussianBlur(upsampled, (blur_k, blur_k), 0)
        _, mask = cv2.threshold(upsampled, 127, 255, cv2.THRESH_BINARY)
        simplify_tol_super = simplify_tol_px * supersample
    else:
        mask = native_mask
        simplify_tol_super = simplify_tol_px

    polygons = _contours_to_polygons(mask, simplify_tol_super)
    if supersample > 1:
        polygons = [
            shapely_scale(p, xfact=1 / supersample, yfact=1 / supersample, origin=(0, 0))
            for p in polygons
        ]
    polygons = [p for p in polygons if p.area >= min_area_px]
    if not polygons:
        raise ValueError(
            "GrabCut found no foreground inside the selection -- try a looser "
            "rectangle that fully contains the design."
        )

    # Drop stray fragments that are tiny relative to the design's main body --
    # GrabCut occasionally leaves a pixel-scale speck disconnected from the
    # real shape (measured on a real file: a 71px^2 speck next to a
    # 115206px^2 main character, 0.06%), and buffering that speck outward by
    # the same margin as the real design draws a small extra floating line
    # right next to the real cutline ("이중 칼선"). A genuinely separate
    # design element (e.g. two accessories in one drag-selection) is never
    # this lopsided, so a generous 2% relative floor only catches noise.
    largest_area = max(p.area for p in polygons)
    min_fragment_area = max(min_area_px, 0.02 * largest_area)
    polygons = [p for p in polygons if p.area >= min_fragment_area]

    # shift from crop-local coordinates back into the full image's space
    polygons = [shapely_translate(p, xoff=x0, yoff=y0) for p in polygons]

    design = unary_union(polygons)
    if isinstance(design, Polygon):
        design = MultiPolygon([design])
    return design
