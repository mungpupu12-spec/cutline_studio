"""
Read the REAL, already-drawn cutline shapes (and the flattened print image
they sit on top of) out of a finished .ai file (PDF-compatible) -- the
reverse direction of core.ai_layer_export, which only ever WRITES a new
layer. This is what lets core.margin_inspector answer "how many mm of
margin does THIS vendor's own sample file actually use?" from a real
example file instead of a number someone remembers or guesses.

Only ever point this at a COPY of a file, same as ai_layer_export -- this
module only reads, but callers are still responsible for never treating
the artist's original client files as something to publish or share
outside her own machine/conversation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.ops import unary_union


def _flatten_bezier(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _items_to_rings(items):
    """One drawing's `items` list -> list of closed point-rings. PyMuPDF's
    get_drawings() doesn't emit explicit moveto ops, so a new ring starts
    whenever the current point doesn't connect to the previous segment's
    end (or on an explicit 're' -- a rectangle is always its own ring)."""
    rings = []
    current = []
    last_end = None

    def start_new(p0):
        nonlocal current
        if len(current) >= 3:
            rings.append(current)
        current = [p0]

    for item in items:
        op = item[0]
        if op == "re":
            rect = item[1]
            if len(current) >= 3:
                rings.append(current)
            rings.append(
                [
                    (rect.x0, rect.y0),
                    (rect.x1, rect.y0),
                    (rect.x1, rect.y1),
                    (rect.x0, rect.y1),
                    (rect.x0, rect.y0),
                ]
            )
            current = []
            last_end = None
            continue
        elif op == "l":
            p0, p1 = item[1], item[2]
        elif op == "c":
            p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
        elif op == "qu":
            quad = item[1]
            if len(current) >= 3:
                rings.append(current)
            rings.append(list(quad) + [quad[0]])
            current = []
            last_end = None
            continue
        else:
            continue

        p0t = (p0.x, p0.y) if hasattr(p0, "x") else tuple(p0)
        if last_end is None or (abs(p0t[0] - last_end[0]) > 1e-3 or abs(p0t[1] - last_end[1]) > 1e-3):
            start_new(p0t)

        if op == "l":
            p1t = (p1.x, p1.y) if hasattr(p1, "x") else tuple(p1)
            current.append(p1t)
            last_end = p1t
        else:
            p1t = (p1.x, p1.y) if hasattr(p1, "x") else tuple(p1)
            p2t = (p2.x, p2.y) if hasattr(p2, "x") else tuple(p2)
            p3t = (p3.x, p3.y) if hasattr(p3, "x") else tuple(p3)
            pts = _flatten_bezier(p0t, p1t, p2t, p3t)
            current.extend(pts[1:])
            last_end = p3t

    if len(current) >= 3:
        rings.append(current)
    return rings


def _drawing_to_polygon(drawing):
    rings = _items_to_rings(drawing["items"])
    polys = []
    for ring in rings:
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 0.01:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    try:
        return unary_union(polys)
    except Exception:
        return polys[0]


@dataclass
class RealCutlineFile:
    """Everything needed to compare a real file's own cutlines against its
    own artwork: the embedded print image (saved to disk once, reusable
    for GrabCut/margin measurement) and each real cutline shape, in that
    SAME image's pixel space."""

    print_image_path: str
    image_w_px: int
    image_h_px: int
    image_placement_rect_pt: tuple  # (x0,y0,x1,y1), page points
    dpi: float  # effective px-per-inch this image was placed at
    cutlines_px: list  # list of shapely Polygon/MultiPolygon, in image px space


def load_real_cutlines(ai_path: str, print_image_out_path: str, layer_name: str = "칼선레이어", page_index: int = 0) -> RealCutlineFile:
    """
    Opens `ai_path` (read-only), extracts the single embedded flattened
    print-layer raster (saved to `print_image_out_path` so downstream
    GrabCut-based tools can work with a plain image file) and every real
    cutline shape found on `layer_name`, mapped into that SAME image's own
    pixel coordinate space (origin top-left, y down -- matching
    core.cutline_core/core.segmentation's convention).

    Raises ValueError if the file has no embedded image or no shapes on
    `layer_name` -- both are expected of a real finished production file
    of the kind this project has been validated against (one flattened
    CMYK print image + one 칼선레이어 with the artist's real cut paths).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(ai_path)
    page = doc[page_index]

    images = page.get_images(full=True)
    if not images:
        raise ValueError(f"{ai_path}: no embedded raster image found on page {page_index}.")
    xref = images[0][0]
    info = doc.extract_image(xref)
    with open(print_image_out_path, "wb") as f:
        f.write(info["image"])

    rect = page.get_image_rects(xref)[0]
    rx0, ry0, rx1, ry1 = rect.x0, rect.y0, rect.x1, rect.y1

    from PIL import Image as PILImage

    with PILImage.open(print_image_out_path) as im:
        iw, ih = im.size

    sx = iw / (rx1 - rx0)
    sy = ih / (ry1 - ry0)
    # Page units are always points (72/inch); this image's OWN effective
    # DPI is whatever scale it ended up placed at on the page.
    dpi = 72.0 * sx

    drawings = [d for d in page.get_drawings() if d.get("layer") == layer_name]
    if not drawings:
        raise ValueError(f"{ai_path}: no shapes found on layer '{layer_name}'.")

    cutlines_px = []
    for d in drawings:
        poly = _drawing_to_polygon(d)
        if poly is None or poly.is_empty:
            continue
        # map page-point coords -> this image's own pixel space
        from shapely.affinity import affine_transform

        # affine_transform matrix: [a, b, d, e, xoff, yoff] for
        # x' = a*x + b*y + xoff ; y' = d*x + e*y + yoff
        mapped = affine_transform(poly, [sx, 0, 0, sy, -rx0 * sx, -ry0 * sy])
        cutlines_px.append(mapped)

    doc.close()

    return RealCutlineFile(
        print_image_path=print_image_out_path,
        image_w_px=iw,
        image_h_px=ih,
        image_placement_rect_pt=(rx0, ry0, rx1, ry1),
        dpi=dpi,
        cutlines_px=cutlines_px,
    )
