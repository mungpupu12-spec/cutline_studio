"""
"이 업체는 실제로 몇 mm 여백을 쓰는지" -- measure the ACTUAL margin an
EXISTING cutline (hand-drawn by an artist, or already baked into a vendor's
sample/guide file) keeps from the artwork underneath it, instead of an
artist eyeballing it in Illustrator or asking around. Confirmed as a real,
wanted feature directly by the artist this project is built for: different
print vendors expect different margins, and checking each one by hand,
file by file, is exactly the kind of manual measurement work this whole
project exists to remove.

This is the reverse of every other generator in this project: those START
from a margin (in mm) and PRODUCE a cutline. This module starts from a
cutline that already exists and RECOVERS the margin it was actually drawn
at -- reusing core.segmentation's GrabCut pipeline to find "the artwork"
under a real cutline the same way this project already finds it under an
artist's rough drag-selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from .cutline_core import px_to_mm
from .segmentation import segment_design_in_region


@dataclass
class MarginStats:
    mean_mm: float
    median_mm: float
    min_mm: float
    max_mm: float
    std_mm: float
    n_samples: int
    samples_mm: list  # per-sampled-point margin, for a histogram/plot if wanted


def _resample_ring(ring, n_samples: int):
    """n evenly arc-length-spaced points around a LinearRing/closed coord
    sequence -- avoids over-weighting whichever stretch happens to have
    more vertices (e.g. a tightly-curved section from bezier flattening)."""
    coords = list(ring.coords)
    line_len = ring.length
    if line_len <= 0:
        return [coords[0]] * n_samples
    pts = []
    for i in range(n_samples):
        d = (i / n_samples) * line_len
        p = ring.interpolate(d)
        pts.append((p.x, p.y))
    return pts


def measure_margin_mm(
    design,
    cutline,
    dpi: float,
    n_samples: int = 96,
) -> MarginStats:
    """
    The pure-geometry core: `design` (the artwork silhouette, a
    Polygon/MultiPolygon) and `cutline` (the already-existing cutline,
    same kind), BOTH in the same pixel coordinate space. Walks `n_samples`
    evenly-spaced points around the cutline's own boundary and measures
    each one's distance to the NEAREST point on the design's boundary --
    that distance is exactly what "margin" means for an outward cutline
    (how far out from the artwork edge this line was actually drawn), and
    works identically for an inward cutline (e.g. a 무테 cell rectangle
    inset from its own boundary) as long as `design` is that same
    reference boundary instead of a segmented character.

    Reports mean/median/min/max/std in mm, not just a single number -- a
    hand-drawn line is never perfectly parallel to the artwork edge, and
    the *spread* (std/range) is itself useful: a vendor whose margin
    varies wildly point-to-point is a different situation from one with a
    tight, consistent 1.5mm all the way around. Prefer `median_mm` as the
    single headline number over `mean_mm`: a GrabCut segmentation
    imperfection (a missed strand of hair, a small disjoint accessory)
    tends to blow out a handful of sample points to a much larger
    distance rather than shifting the whole ring evenly, which drags the
    mean up much more than the median (measured on a real file: one
    character's mean/median came out 4.08mm/2.26mm with a handful of
    14mm-range outlier points -- the median is the trustworthy read
    there, not the mean).
    """
    design_b = design.boundary if hasattr(design, "boundary") else unary_union(design).boundary

    cutline_polys = list(cutline.geoms) if isinstance(cutline, MultiPolygon) else [cutline]
    samples_px = []
    for poly in cutline_polys:
        samples_px.extend(_resample_ring(poly.exterior, max(1, n_samples // len(cutline_polys))))

    distances_px = [Point(pt).distance(design_b) for pt in samples_px]
    distances_mm = [px_to_mm(d, dpi) for d in distances_px]

    arr = np.array(distances_mm) if distances_mm else np.array([0.0])
    return MarginStats(
        mean_mm=float(arr.mean()),
        median_mm=float(np.median(arr)),
        min_mm=float(arr.min()),
        max_mm=float(arr.max()),
        std_mm=float(arr.std()),
        n_samples=len(distances_mm),
        samples_mm=distances_mm,
    )


def measure_margin_from_image(
    image_path: str,
    cutline,
    dpi: float,
    grabcut_margin_px: int = 40,
    n_samples: int = 96,
) -> MarginStats:
    """
    Convenience entry point for the common real-world case: you have the
    print image and an existing cutline (e.g. from
    core.ai_cutline_reader.load_real_cutlines), but the artwork silhouette
    itself hasn't been segmented yet. Uses the cutline's OWN bounding box
    (widened slightly) as the GrabCut selection rectangle -- reasonable
    because a real cutline, by definition, was drawn to surround (or, for
    an inward/무테 line, to sit just inside) the artwork it belongs to, so
    its bounding box is already a good rough "drag selection".

    NOTE (see core.style_classify's module docstring for the same
    underlying limitation): if the artwork's background has low contrast
    against its surroundings, GrabCut can under-segment (miss a flat
    fill), which would make a 무테-style measurement read as a much LARGER
    margin than what was actually drawn, because "the design" it measures
    against is smaller than the real cell boundary. Cross-check a
    surprising result against core.style_classify's rectangularity score
    for the same cutline -- a high score (>=0.85) means the cutline itself
    is a near-rectangle, so measuring against `cutline`'s own image/cell
    bounds directly (not a GrabCut segmentation) is the more trustworthy
    number for that case.
    """
    minx, miny, maxx, maxy = cutline.bounds
    pad = grabcut_margin_px // 2
    rect_px = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    design = segment_design_in_region(image_path, rect_px, margin_px=grabcut_margin_px)
    return measure_margin_mm(design, cutline, dpi, n_samples=n_samples)
