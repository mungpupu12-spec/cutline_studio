"""
General pixel<->page-point mapping for a placed raster image, INCLUDING
rotation -- not just the axis-aligned scale+translate every other module
in this project has assumed so far.

Why this exists: every real file this project validated against before
(1/2/3/4조수희) happened to place its single embedded print image
completely unrotated, so "image pixel (px,py) -> page point" was always
just a uniform per-axis scale from the image's own placement rect
(rx0,ry0,rx1,ry1). `5조수희_5_유포지_인델별(색감 수정).ai` breaks that
assumption two ways at once: (a) it places the SAME repeating-tile image
seven times (six upright, one physically rotated 90 degrees into a
leftover corner), and (b) its reused "bunny corner" design is *also*
placed rotated. Both need the image's real PDF placement MATRIX, not just
its bounding rect, or a rotated instance's cutline would be computed and
drawn sideways / mirrored.

PDF places an image by mapping the unit square [0,1]x[0,1] (image space,
origin bottom-left in the raw PDF convention -- but PyMuPDF's own
get_image_rects(xref, transform=True) already reports rect/matrix in its
own top-down page convention, the SAME one page.get_drawings() and
page.rect use, which every other module in this project already relies
on) through the standard 6-value PDF/PyMuPDF matrix (a, b, c, d, e, f):

    point_x = a*u + c*v + e
    point_y = b*u + d*v + f

where (u, v) in [0,1]x[0,1] -- field names deliberately match
`fitz.Matrix`'s own `.a .b .c .d .e .f` exactly (NOT a generic "d/e as
diagonal-scale/offset" naming), so a value copied straight from a
PyMuPDF Matrix always lands in the field with the same letter. This
module treats a raw pixel (px, py) in the image's own raster (0..W, 0..H,
origin top-left, y down -- exactly what cv2.imread / doc.extract_image's
saved PNG use) as u=px/W, v=py/H, and exposes both directions. Verified
against a real rotated placement (5조수희_인델별: a 90-degree-rotated
tile instance) -- see core/image_placement's test in the dev journal for
the worked numeric check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class ImagePlacement:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    img_w_px: int
    img_h_px: int

    @classmethod
    def from_pymupdf(cls, rect_matrix_pair, img_w_px: int, img_h_px: int) -> "ImagePlacement":
        """`rect_matrix_pair`: one (Rect, Matrix) tuple as returned by
        `page.get_image_rects(xref, transform=True)`."""
        _rect, mat = rect_matrix_pair
        return cls(mat.a, mat.b, mat.c, mat.d, mat.e, mat.f, img_w_px, img_h_px)

    def pixel_to_point(self, px: float, py: float) -> Tuple[float, float]:
        u = px / self.img_w_px
        v = py / self.img_h_px
        x = self.a * u + self.c * v + self.e
        y = self.b * u + self.d * v + self.f
        return (x, y)

    def point_to_pixel(self, x: float, y: float) -> Tuple[float, float]:
        """Inverse of pixel_to_point -- solves the 2x2 linear system
        [[a, c], [b, d]] @ [u, v] = [x - e, y - f]."""
        det = self.a * self.d - self.c * self.b
        if abs(det) < 1e-12:
            raise ValueError("Degenerate image placement matrix (det ~ 0) -- image has zero area on the page.")
        rx, ry = x - self.e, y - self.f
        u = (self.d * rx - self.c * ry) / det
        v = (self.a * ry - self.b * rx) / det
        return (u * self.img_w_px, v * self.img_h_px)

    @property
    def is_axis_aligned(self) -> bool:
        """True for the simple, common case (no rotation/shear) this
        project's earlier modules assumed: only a and d are non-zero."""
        return abs(self.b) < 1e-6 and abs(self.c) < 1e-6

    def px_to_pt_scale(self) -> Tuple[float, float]:
        """Only meaningful when is_axis_aligned -- the per-axis pixel-to-
        point scale factor, for callers (like stroke width) that need a
        single number rather than a full transform."""
        return (self.a / self.img_w_px, self.d / self.img_h_px)


def map_polygon_pixel_to_point(poly, placement: ImagePlacement):
    """Map every coordinate of a shapely Polygon/MultiPolygon from image
    pixel space to page point space through `placement`, preserving holes."""
    from shapely.geometry import MultiPolygon, Polygon

    def _map_poly(p: Polygon) -> Polygon:
        ext = [placement.pixel_to_point(x, y) for x, y in p.exterior.coords]
        interiors = [
            [placement.pixel_to_point(x, y) for x, y in ring.coords] for ring in p.interiors
        ]
        return Polygon(ext, interiors)

    if poly.geom_type == "MultiPolygon":
        return MultiPolygon([_map_poly(g) for g in poly.geoms])
    return _map_poly(poly)


def map_polygon_point_to_pixel(poly, placement: ImagePlacement):
    """Inverse of map_polygon_pixel_to_point -- page point space back into
    this image's own raw pixel space."""
    from shapely.geometry import MultiPolygon, Polygon

    def _map_poly(p: Polygon) -> Polygon:
        ext = [placement.point_to_pixel(x, y) for x, y in p.exterior.coords]
        interiors = [
            [placement.point_to_pixel(x, y) for x, y in ring.coords] for ring in p.interiors
        ]
        return Polygon(ext, interiors)

    if poly.geom_type == "MultiPolygon":
        return MultiPolygon([_map_poly(g) for g in poly.geoms])
    return _map_poly(poly)
