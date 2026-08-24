"""
CutLine Studio - core algorithm
--------------------------------
Given a design image (raster PNG with alpha, or a flattened design where the
background is a known color) OR a vector SVG, this module extracts the
design's silhouette and generates three concentric offset outlines used for
sticker/decal die-cutting:

    safety range  (green)  - closest to the artwork
    cutting line  (red)    - the actual die/laser cut path
    bleeding line (blue)   - furthest out, print bleed allowance

All three offsets are expressed in millimeters, outward from the design's
silhouette edge, and are converted to pixels using the artboard's DPI.

The heavy lifting is done with:
  - OpenCV: raster contour extraction (with hierarchy, so holes are kept)
  - Shapely: polygon simplification + buffering (offsetting) + union of
    disjoint shapes (so nearby objects merge into one contour if their
    offsets would overlap, exactly like a real print vendor would do)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.validation import make_valid


MM_PER_INCH = 25.4


def mm_to_px(mm: float, dpi: float) -> float:
    return mm / MM_PER_INCH * dpi


@dataclass
class OffsetSpec:
    """Millimeter offsets, measured outward from the artwork edge."""

    safety_mm: float = 1.0
    cut_mm: float = 2.0
    bleed_mm: float = 3.0

    def sorted_items(self):
        # Always processed from smallest (closest to art) to largest (furthest out)
        return sorted(
            [("safety", self.safety_mm), ("cut", self.cut_mm), ("bleed", self.bleed_mm)],
            key=lambda kv: kv[1],
        )


@dataclass
class CutlineResult:
    dpi: float
    width_px: int
    height_px: int
    design: MultiPolygon
    offsets: dict  # name -> MultiPolygon (buffered outward)
    offset_mm: OffsetSpec


# --------------------------------------------------------------------------
# Raster (PNG/JPG) -> design silhouette
# --------------------------------------------------------------------------

def _contours_to_polygons(mask: np.ndarray, simplify_tol_px: float) -> list[Polygon]:
    """Extract contours (with holes) from a binary mask and return Shapely polygons."""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]  # shape (N, 4): next, prev, first_child, parent

    polygons = []
    # top-level contours (parent == -1) are exteriors; their children are holes
    for i, h in enumerate(hierarchy):
        parent = h[3]
        if parent != -1:
            continue  # this is a hole, handled below via its parent
        exterior_cnt = contours[i]
        if len(exterior_cnt) < 3:
            continue
        exterior = exterior_cnt.squeeze(1).astype(float)

        # collect holes (direct children of this contour)
        holes = []
        child = h[2]
        while child != -1:
            hole_cnt = contours[child]
            if len(hole_cnt) >= 3:
                holes.append(hole_cnt.squeeze(1).astype(float))
            child = hierarchy[child][0]

        try:
            poly = Polygon(exterior, holes)
            if simplify_tol_px > 0:
                poly = poly.simplify(simplify_tol_px, preserve_topology=True)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.area > 0:
                polygons.append(poly)
        except Exception:
            continue
    return polygons


def load_raster_design(
    image_path: str,
    alpha_threshold: int = 20,
    simplify_tol_px: float = 1.5,
    min_area_px: float = 25.0,
) -> tuple[MultiPolygon, int, int]:
    """
    Load a PNG (or any image PIL/OpenCV can decode). If the image has an
    alpha channel, the foreground is wherever alpha > alpha_threshold.
    If there is no alpha channel, we fall back to treating near-white pixels
    as background (common when the artist flattened onto a white artboard).
    """
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    h, w = img.shape[:2]

    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        mask = (alpha > alpha_threshold).astype(np.uint8) * 255
    else:
        # no alpha channel -> assume near-white background
        if img.ndim == 2:
            gray = img
        else:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

    # light denoise so single stray pixels don't become tiny contours
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    polygons = _contours_to_polygons(mask, simplify_tol_px)
    polygons = [p for p in polygons if p.area >= min_area_px]

    if not polygons:
        raise ValueError(
            "No design silhouette found. Check that the image has transparency "
            "(or a clean white background) around the artwork."
        )

    design = unary_union(polygons)
    if isinstance(design, Polygon):
        design = MultiPolygon([design])

    return design, w, h


# --------------------------------------------------------------------------
# Vector (SVG) -> design silhouette
# --------------------------------------------------------------------------
#
# Rather than re-implementing SVG curve flattening and fill-rule (nonzero /
# evenodd) semantics by hand -- which is exactly the kind of thing that goes
# subtly wrong with holes, self-intersecting paths, and multi-subpath fonts
# -- we rasterize the SVG with a real renderer (cairosvg) at a resolution
# matched to the requested working DPI, then reuse the exact same
# battle-tested raster contour pipeline used for PNG/JPG input. This keeps a
# single source of truth for "what counts as the design silhouette".

SVG_REFERENCE_DPI = 96.0  # standard CSS px definition (1px = 1/96 inch)


def load_vector_design(
    svg_path: str,
    dpi: float = 300.0,
    simplify_tol_px: float = 1.0,
) -> tuple[MultiPolygon, int, int]:
    """
    Rasterize an SVG at `dpi` (assuming the SVG's own user units follow the
    standard 96dpi CSS definition) and extract the design silhouette using
    the same contour+hole logic as load_raster_design.
    """
    import tempfile
    import os
    import cairosvg

    scale = dpi / SVG_REFERENCE_DPI

    fd, tmp_png = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cairosvg.svg2png(url=svg_path, write_to=tmp_png, scale=scale)
        design, w, h = load_raster_design(
            tmp_png, alpha_threshold=10, simplify_tol_px=simplify_tol_px
        )
    finally:
        try:
            os.remove(tmp_png)
        except OSError:
            pass

    return design, w, h


# --------------------------------------------------------------------------
# Offsetting
# --------------------------------------------------------------------------

def compute_offsets(
    design: MultiPolygon,
    dpi: float,
    offset_mm: OffsetSpec,
    join_style: int = 1,  # 1 = round
    buffer_resolution: int = 16,
) -> dict:
    """
    Buffer the design outward by each offset distance (mm -> px).
    Returns dict: name -> MultiPolygon (the buffered outward shape; the
    outer boundary of this shape IS the cut/safety/bleed line).
    """
    results = {}
    for name, mm in offset_mm.sorted_items():
        px = mm_to_px(mm, dpi)
        buffered = design.buffer(px, join_style=join_style, resolution=buffer_resolution)
        if isinstance(buffered, Polygon):
            buffered = MultiPolygon([buffered])
        results[name] = buffered
    return results


def generate_cutlines(
    image_path: str,
    is_vector: bool,
    dpi: float,
    offset_mm: OffsetSpec,
    alpha_threshold: int = 20,
    simplify_tol_px: float = 1.5,
) -> CutlineResult:
    if is_vector:
        design, w, h = load_vector_design(
            image_path, dpi=dpi, simplify_tol_px=min(simplify_tol_px, 1.0)
        )
    else:
        design, w, h = load_raster_design(
            image_path, alpha_threshold=alpha_threshold, simplify_tol_px=simplify_tol_px
        )

    offsets = compute_offsets(design, dpi, offset_mm)

    return CutlineResult(
        dpi=dpi,
        width_px=w,
        height_px=h,
        design=design,
        offsets=offsets,
        offset_mm=offset_mm,
    )
