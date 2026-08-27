"""
Auto-detects 유테 vs 무테 (core.image_style.ImageStyle) FROM THE SEGMENTED
CONTENT ITSELF, instead of asking the artist to pick a radio button every
single time -- one more "실무 기본값" step removed from the manual workflow.

Why this works, and why it isn't a guess:

Real production files (this project has validated against two real 6-tile
sheets so far) mix two kinds of design elements in the very same tile:

  - a free-floating character (a bear, a dog, a leaf) -- its printed
    silhouette is organic, so its own minimum-rotated-bounding-rectangle is
    much bigger than the shape itself. Real hand-drawn cutline for these:
    a silhouette-hugging line (유테).

  - a design that is ALREADY built around its own rectangular frame/box
    (a scene drawn inside a rounded box, a bordered vignette) -- because
    that box's own straight edges become part of what GrabCut segments as
    foreground, the segmented shape's own bounding rectangle is nearly
    equal to the shape's actual area. Real hand-drawn cutline for these:
    that box's own rectangle, inset (무테).

Measured on 2조수희_5_유포지_유광코팅(cs6).ai's REAL 칼선레이어 polygons
(rectangularity = polygon.area / polygon.minimum_rotated_rectangle.area):
  - the framed swing-bear box: real cutline rectangularity 0.879
  - the reused bunny corner frame: real cutline rectangularity 1.000
  - every free-floating character (skateboard bear, ballet bears, poodle
    head, cabbage leaf): real cutline rectangularity 0.55-0.79
That is a clean, wide gap -- 0.85 sits in the middle of it and is used
as the default threshold below.

Confirmed this isn't just a property of the REAL hand-traced line but of
the underlying artwork itself: running THIS project's own GrabCut
segmentation (core.segmentation.segment_design_in_region) over a rough
selection around each of those same two regions reproduces the same split
almost exactly (segmented box rectangularity 0.8895 vs real 0.879;
segmented skateboard-bear rectangularity 0.727 vs real 0.666) -- so the
classifier below can run on OUR OWN segmentation output, before any real
cutline exists to compare against.

**Halo override (added after a real failure)**: rectangularity alone
mis-fires on a small decorative accessory (a plaid fabric-swatch icon, a
snack icon) whose own artwork just happens to be drawn as a square/rect --
it scored 0.87-0.91 (over the 0.85 threshold) and got called 무테, so its
cutline was generated as a rectangle INSET from the selection box. But that
accessory has its own halo/background margin around it on every side, just
like any other character (measured on a real file: 2.6-12.9px of real gap
on all four sides between the segmented content and the selection box) --
exactly 유테's own definition from the artist ("each design is its own
discrete cutout with space around it"), regardless of how square the
silhouette itself looks. Insetting a rectangle sized from the selection
box then made the cutline land INSIDE the visible printed icon instead of
around it -- a real, visible error the artist caught directly ("칼선이
스티커 안에 들어가 있어"). Her fix, stated directly: for small elements,
whenever a cutline CAN be traced from the actual image outline, trace it --
don't default to a bounding-rectangle inset just because the shape scores
as rectangular. `classify_style` below now checks for that halo FIRST (a
real, measurable gap on every side between the segmented content and the
selection) and forces 유테 when one is present, before ever consulting
rectangularity -- rectangularity only still decides the case where there is
NO halo at all (content reaches the selection's edge on at least one side),
which is exactly the genuine full-cell/framed-box case this score was
originally built for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shapely.geometry.base import BaseGeometry

from .image_style import ImageStyle

DEFAULT_RECTANGULARITY_THRESHOLD = 0.85

# A gap smaller than this (px, in the image's OWN raster resolution) isn't
# trusted as a real halo -- it's within the noise of GrabCut's edge/contour
# extraction (see core.cutline_core's supersample pipeline), so a shape that
# reaches to within this distance of the selection edge is still treated as
# "no halo" (genuinely full-bleed) rather than a real, if thin, margin.
MIN_TRUSTED_HALO_PX = 2.0


def rectangularity(geom: BaseGeometry) -> float:
    """1.0 for a perfect axis/rotated rectangle, lower for organic shapes.
    Uses the minimum ROTATED bounding rectangle (not axis-aligned), so a
    box drawn at a slight angle isn't penalized just for its rotation."""
    if geom is None or geom.is_empty:
        return 0.0
    mrr_area = geom.minimum_rotated_rectangle.area
    if mrr_area <= 0:
        return 0.0
    return geom.area / mrr_area


def _has_halo(segmented_design: BaseGeometry, selection_px: tuple) -> bool:
    """True when the segmented content sits with a real, measurable gap
    from EVERY side of the selection box it was found in -- i.e. there is
    actual background/halo surrounding it, not a full-bleed fill reaching
    the selection's own edge."""
    if segmented_design is None or segmented_design.is_empty:
        return False
    sx0, sy0, sx1, sy1 = selection_px
    cx0, cy0, cx1, cy1 = segmented_design.bounds
    gaps = (cx0 - sx0, cy0 - sy0, sx1 - cx1, sy1 - cy1)
    return min(gaps) > MIN_TRUSTED_HALO_PX


@dataclass
class StyleClassification:
    style: ImageStyle
    rectangularity: float
    threshold: float
    halo_detected: Optional[bool] = None


def classify_style(
    segmented_design: BaseGeometry,
    threshold: float = DEFAULT_RECTANGULARITY_THRESHOLD,
    selection_px: Optional[tuple] = None,
) -> StyleClassification:
    """`segmented_design`: the MultiPolygon/Polygon returned by
    core.segmentation.segment_design_in_region for one drag-selection.

    `selection_px`: the SAME selection rectangle passed to that
    segmentation call, when the caller has it -- enables the halo check
    (see module docstring) that overrides rectangularity when the content
    clearly has its own background margin on every side. Omit to fall back
    to the original rectangularity-only decision (e.g. when classifying a
    real hand-drawn cutline directly, with no selection rectangle at all).

    Returns which style (유테/무테) applies, plus the measured score(s) so
    the caller/GUI can show its work rather than presenting the guess as
    unquestionable."""
    score = rectangularity(segmented_design)

    if selection_px is not None and _has_halo(segmented_design, selection_px):
        return StyleClassification(
            style=ImageStyle.LINE_ART, rectangularity=score, threshold=threshold, halo_detected=True
        )

    style = ImageStyle.BORDERLESS if score >= threshold else ImageStyle.LINE_ART
    halo_detected = False if selection_px is not None else None
    return StyleClassification(
        style=style, rectangularity=score, threshold=threshold, halo_detected=halo_detected
    )
