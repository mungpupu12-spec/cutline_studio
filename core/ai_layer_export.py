"""
Add a generated cutline as a NEW, separate layer (Illustrator OCG) on top of
an existing PDF-compatible .ai file -- without touching the file's existing
layers, the placed print image, or its size/resolution in any way.

Why this exists: real finished production files (like the ones this tool is
actually validated against) are not blank canvases -- they already have the
artist's own layer structure (가이드라인 / 개별재단 레이어 / 칼선레이어 /
인쇄레이어 in the file this was built against) with the artwork already
placed and sized correctly. Regenerating the whole file, or re-flattening
the image, would throw that away. Instead this module opens the file (a
*copy* -- callers are responsible for never pointing this at the artist's
original), adds one new OCG layer, and draws the cutline as vector paths
into that layer only. Every other xref in the PDF -- including the embedded
image -- is left completely untouched, so the image's pixel size and
resolution cannot change as a side effect of this step.
"""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon


def _polygon_rings(poly: Polygon):
    """Yield (ring_coords, is_hole) for a polygon's exterior + interiors."""
    yield list(poly.exterior.coords), False
    for interior in poly.interiors:
        yield list(interior.coords), True


def add_cutline_layer_to_ai(
    src_path: str,
    out_path: str,
    design_or_offsets,
    image_placement_rect_pt: tuple,
    image_w_px: int,
    image_h_px: int,
    page_index: int = 0,
    layer_name: str = "칼선레이어_자동생성",
    stroke_color: tuple = (0.0, 0.55, 1.0),  # distinct blue, so it's never
    # visually confused with the artist's own magenta hand-drawn 칼선레이어
    stroke_width_pt: float = 0.75,
) -> str:
    """
    Open `src_path` (a PDF-compatible .ai file, or a plain PDF), add one new
    OCG named `layer_name`, and draw `design_or_offsets` into it as vector
    paths -- then save the result to `out_path` (which may equal `src_path`
    if the caller already made their own copy).

    `design_or_offsets` may be:
      - a single shapely Polygon/MultiPolygon (e.g. CutlineResult.design, or
        one entry of CutlineResult.offsets), or
      - a dict of name -> Polygon/MultiPolygon (e.g. a whole
        CutlineResult.offsets, to draw safety/cut/bleed together) -- each
        entry becomes its own OCG layer named f"{layer_name}_{key}".

    Coordinates in the shapely geometry are assumed to be in the SAME pixel
    space as the placed raster image (`image_w_px` x `image_h_px`, origin
    top-left, y increasing downward -- exactly what core.cutline_core
    produces from that same image). `image_placement_rect_pt` is that
    image's own placement rect on the page, e.g. from
    `page.get_image_rects(xref)[0]`, as (x0, y0, x1, y1) -- used to map
    pixel coordinates back into the page's point space at the correct
    position and scale, however the artist actually placed/scaled the image
    on the artboard (so this works even if the image isn't at 1:1 with the
    page's own DPI assumption).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(src_path)
    page = doc[page_index]

    rx0, ry0, rx1, ry1 = image_placement_rect_pt
    scale_x = (rx1 - rx0) / image_w_px
    scale_y = (ry1 - ry0) / image_h_px

    def to_pt(xy):
        x_px, y_px = xy
        return (rx0 + x_px * scale_x, ry0 + y_px * scale_y)

    if isinstance(design_or_offsets, dict):
        layers = {f"{layer_name}_{key}": geom for key, geom in design_or_offsets.items()}
    else:
        layers = {layer_name: design_or_offsets}

    written_layers = []
    for name, geom in layers.items():
        ocg_xref = doc.add_ocg(name, on=True)
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]

        shape = page.new_shape()
        n_rings = 0
        for poly in polys:
            for ring, _is_hole in _polygon_rings(poly):
                pts = [to_pt(xy) for xy in ring]
                if len(pts) < 2:
                    continue
                shape.draw_polyline(pts)
                n_rings += 1
        if n_rings:
            shape.finish(
                color=stroke_color,
                width=stroke_width_pt,
                closePath=True,
                fill=None,
                oc=ocg_xref,
            )
            shape.commit()
        written_layers.append((name, n_rings))

    if out_path == src_path:
        # Incremental save appends the new layer/paths without touching any
        # existing object (including the embedded image) -- the safest way
        # to edit "in place" when the caller intentionally passed the same
        # path (e.g. a copy they already made and want to update further).
        doc.saveIncr()
    else:
        doc.save(out_path, garbage=0, deflate=True)
    doc.close()
    return out_path, written_layers


def add_point_space_layer_to_ai(
    src_path: str,
    out_path: str,
    design_or_offsets,
    page_index: int = 0,
    layer_name: str = "칼선레이어_자동생성",
    stroke_color: tuple = (0.0, 0.55, 1.0),
    stroke_width_pt: float = 0.75,
) -> str:
    """
    Sibling of `add_cutline_layer_to_ai` for the case where the caller has
    ALREADY mapped every polygon into page point space -- e.g. a full-sheet
    result assembled via `core.repeat_grid.replicate_by_placements`, where
    different tile instances (some rotated) each went through their OWN
    `core.image_placement.ImagePlacement`, so there is no single shared
    "image placement rect" left to map through here (unlike
    `add_cutline_layer_to_ai`, which assumes exactly one image/one simple
    rect for the whole layer). Draws `design_or_offsets` (a single
    Polygon/MultiPolygon, or a dict of name -> geometry, same convention as
    `add_cutline_layer_to_ai`) directly at its given page-point coordinates,
    no pixel->point conversion at all.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(src_path)
    page = doc[page_index]

    if isinstance(design_or_offsets, dict):
        layers = {f"{layer_name}_{key}": geom for key, geom in design_or_offsets.items()}
    else:
        layers = {layer_name: design_or_offsets}

    written_layers = []
    for name, geom in layers.items():
        ocg_xref = doc.add_ocg(name, on=True)
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]

        shape = page.new_shape()
        n_rings = 0
        for poly in polys:
            for ring, _is_hole in _polygon_rings(poly):
                if len(ring) < 2:
                    continue
                shape.draw_polyline(ring)
                n_rings += 1
        if n_rings:
            shape.finish(
                color=stroke_color,
                width=stroke_width_pt,
                closePath=True,
                fill=None,
                oc=ocg_xref,
            )
            shape.commit()
        written_layers.append((name, n_rings))

    if out_path == src_path:
        doc.saveIncr()
    else:
        doc.save(out_path, garbage=0, deflate=True)
    doc.close()
    return out_path, written_layers
