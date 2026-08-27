"""
Replicate ONE already-generated, verified cutline across every repeated
instance of the same design on a sheet, instead of re-segmenting/re-tracing
each copy independently.

Real sheets very often print the exact same design tiled several times
(e.g. this project's reference file: one scene repeated 6-7 times across a
grid). Since every instance is pixel-identical (or at least placed on an
exact, known grid), the correct/robust approach is: generate one accurate
cutline for a single instance (however that was produced -- traced,
GrabCut-segmented, or a 도무송 primitive), then translate an exact copy of
that same geometry to every other instance's position. This is both far
cheaper than re-running segmentation per copy, and immune to segmentation
noise making equivalent copies slightly different from each other (which a
real print vendor would reasonably object to -- identical printed art should
get identical cutlines).

`replicate_by_offsets`/`grid_offsets_from_origins` only ever handled a pure
translation grid -- every file validated against before this round happened
to place its repeated tiles unrotated. `5조수희_5_유포지_인델별(색감 수정)`
breaks that: it repeats its tile 7 times, but one of those instances is
physically rotated 90 degrees into a leftover corner of the sheet (and its
reused "bunny corner" design is placed rotated too). `replicate_by_placements`
below is the rotation-aware sibling: instead of a plain (dx, dy) translate,
it re-projects the SAME tile-local pixel-space cutline through each
instance's own real PDF placement matrix (`core.image_placement.
ImagePlacement`, built from `page.get_image_rects(xref, transform=True)`),
so a rotated instance's cutline comes out correctly rotated too -- not
translated sideways into the wrong orientation.
"""

from __future__ import annotations

from shapely.affinity import translate as shapely_translate
from shapely.geometry import MultiPolygon, Polygon

from .image_placement import ImagePlacement, map_polygon_pixel_to_point


def replicate_by_offsets(geom, offsets_px: list):
    """
    `offsets_px`: list of (dx, dy) pixel translations relative to the
    geometry's current position -- e.g. [(0,0), (tile_w,0), (0,tile_h), ...]
    for a design already generated at the first (0,0) tile and repeated on a
    regular grid. Include (0, 0) explicitly if you want the original
    position back in the result list.

    Returns a list of geometries, one per offset, each an independent copy
    (translating never mutates the input).
    """
    return [shapely_translate(geom, xoff=dx, yoff=dy) for dx, dy in offsets_px]


def grid_offsets_from_origins(tile_origins_px: list, base_index: int = 0):
    """
    Convenience: given the top-left (or any consistent reference corner) of
    each repeated tile in pixel space, return the (dx, dy) offsets needed to
    move a cutline generated at `tile_origins_px[base_index]` to every other
    tile in the list (including itself, as (0, 0)).
    """
    bx, by = tile_origins_px[base_index]
    return [(ox - bx, oy - by) for ox, oy in tile_origins_px]


def replicate_by_placements(geom_px, placements: list):
    """
    Rotation-aware sibling of `replicate_by_offsets`: `geom_px` is a
    cutline already generated in ONE tile instance's own raw pixel space
    (e.g. the 1772x945 repeat-tile raster), and `placements` is a list of
    `ImagePlacement` -- one per real instance of that same image on the
    page (from `page.get_image_rects(xref, transform=True)`, wrapped via
    `ImagePlacement.from_pymupdf`). Returns one geometry PER placement,
    each mapped into PAGE POINT SPACE (not pixel space, since different
    instances can be rotated relative to each other -- there is no single
    shared pixel space to translate within once rotation is involved).

    Every returned geometry traces back to the exact same verified
    tile-local cutline; only the placement differs, so all instances stay
    visually identical modulo their real on-page position/rotation --
    exactly like `replicate_by_offsets`, generalized to matrices instead of
    plain offsets.
    """
    return [map_polygon_pixel_to_point(geom_px, placement) for placement in placements]


def union_all(geoms) -> MultiPolygon:
    """Flatten a list of Polygon/MultiPolygon results (e.g. from
    replicate_by_offsets) into a single MultiPolygon, e.g. for rendering all
    replicated copies together in one preview/export pass."""
    polys = []
    for g in geoms:
        if isinstance(g, MultiPolygon):
            polys.extend(g.geoms)
        elif isinstance(g, Polygon):
            polys.append(g)
    return MultiPolygon(polys)
