"""
Physical cutting-safety analysis.

Naive outward buffering treats the whole design as if it were made of
infinitely thin ink. Real die-cutting has two failure modes this ignores:

  1. DETAIL LOSS - a design element thinner than the requested offset
     distance gets smoothed away or silently merged into a neighboring
     blob during buffering. A thin whisker, a fine serif, a narrow gap
     between two eyes -- all can vanish without any error being raised.

  2. TEAR / SLIP RISK ("찢김" / "밀림") - even where a shape survives,
     if the *material bridge* between two nearby cut edges (e.g. the
     paper between an interior hole and the outer cut line, or between
     two cut shapes merged at the bleed layer) is narrower than what a
     real blade/laser can handle cleanly, the sticker backing can tear
     during weeding, or the piece can shift on the cutting bed.

This module finds those risk zones using a distance transform (which
gives, per pixel, the distance to the nearest background pixel -- i.e.
the local half-width of the material at that point) so they can be
flagged to the artist instead of silently "fixed" by the algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .cutline_core import mm_to_px


@dataclass
class RiskReport:
    min_feature_radius_px: float
    min_feature_radius_mm: float
    risk_mask: np.ndarray  # uint8, 255 where local radius < threshold
    risk_pixel_count: int
    ok: bool


def _mask_from_polygon_group(mp, width, height):
    """Rasterize a Shapely (Multi)Polygon (with holes) to a filled mask."""
    from shapely.geometry import Polygon

    mask = np.zeros((height, width), dtype=np.uint8)
    polys = [mp] if mp.geom_type == "Polygon" else list(mp.geoms)
    for poly in polys:
        ext = np.array(poly.exterior.coords, dtype=np.int32)
        cv2.fillPoly(mask, [ext], 255)
        for interior in poly.interiors:
            hole = np.array(interior.coords, dtype=np.int32)
            cv2.fillPoly(mask, [hole], 0)
    return mask


def _opening_removes_something(mask: np.ndarray, r_px: int) -> tuple[bool, np.ndarray]:
    r_px = max(1, r_px)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_px + 1, 2 * r_px + 1))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    removed = cv2.subtract(mask, opened)
    return bool(cv2.countNonZero(removed) > 0), removed


def _min_feature_radius_px(mask: np.ndarray, max_r: int = 80) -> int:
    """
    Binary search (well, linear scan -- shapes are small enough) for the
    largest disk radius r such that a morphological OPENING with that disk
    removes nothing from the mask. The first r that removes *something*
    means some part of the shape is narrower than ~2r px there.
    This is the standard trick: opening = erode(r) then dilate(r). A part
    of the shape survives opening only if a disk of radius r fits inside
    it at every point along that part -- exactly "local half-width >= r".
    """
    for r in range(1, max_r + 1):
        removes, _ = _opening_removes_something(mask, r)
        if removes:
            return r
    return max_r


def analyze_thin_features(
    mask: np.ndarray,
    dpi: float,
    min_safe_radius_mm: float,
) -> RiskReport:
    """
    mask: binary uint8 mask (255 = material/ink, 0 = background), e.g. the
          original artwork mask, OR a rasterized cut-line solid region.
    min_safe_radius_mm: the smallest local HALF-width (radius) considered
          safe for the target cutting equipment. A thinner spot means the
          full width there is < 2 * min_safe_radius_mm.

    Uses morphological opening (erode-then-dilate with a disk of the
    threshold radius) rather than a raw per-pixel distance transform:
    a raw distance-to-background value is small near the edge of *any*
    shape (including a perfectly safe, large one), so thresholding it
    directly would flag the rim of every shape as "risky". Opening only
    erases parts that are genuinely narrower than the disk, which is what
    we actually want to flag.
    """
    material = mask > 0
    if not material.any():
        return RiskReport(0.0, 0.0, np.zeros_like(mask), 0, ok=True)

    safe_radius_px = max(1, round(mm_to_px(min_safe_radius_mm, dpi)))
    _, risk_mask = _opening_removes_something(mask, safe_radius_px)

    min_radius_px = _min_feature_radius_px(mask)
    min_radius_mm = min_radius_px / mm_to_px(1.0, dpi)

    return RiskReport(
        min_feature_radius_px=min_radius_px,
        min_feature_radius_mm=min_radius_mm,
        risk_mask=risk_mask,
        risk_pixel_count=int((risk_mask > 0).sum()),
        ok=risk_mask.sum() == 0,
    )


def analyze_design_detail_risk(design, width, height, dpi, min_safe_radius_mm=0.15):
    """Detail-loss risk on the ORIGINAL artwork silhouette: any part of the
    design itself thinner than what buffering/simplification can reliably
    preserve."""
    mask = _mask_from_polygon_group(design, width, height)
    return analyze_thin_features(mask, dpi, min_safe_radius_mm)


def analyze_cutline_bridge_risk(offset_polygon, width, height, dpi, min_safe_radius_mm=0.4):
    """Tear/slip risk on the FINAL cut-line solid region: bridges of
    material (between a hole and the outer edge, or between two merged
    shapes) narrower than the cutting equipment can handle cleanly."""
    mask = _mask_from_polygon_group(offset_polygon, width, height)
    return analyze_thin_features(mask, dpi, min_safe_radius_mm)
