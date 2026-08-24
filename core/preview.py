"""Render a quick-look PNG (original artwork + overlaid offset lines) so we
can visually sanity-check the algorithm without opening Illustrator."""

from __future__ import annotations

from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from .cutline_core import CutlineResult

COLORS_RGB = {
    "bleed": (0, 80, 255),
    "cut": (255, 30, 30),
    "safety": (0, 190, 60),
}


def _draw_multipolygon_outline(draw: ImageDraw.ImageDraw, mp, offset_xy, color, width):
    if isinstance(mp, Polygon):
        polys = [mp]
    else:
        polys = list(mp.geoms)
    ox, oy = offset_xy
    for poly in polys:
        coords = [(x + ox, y + oy) for x, y in poly.exterior.coords]
        draw.line(coords, fill=color, width=width, joint="curve")
        for interior in poly.interiors:
            coords_i = [(x + ox, y + oy) for x, y in interior.coords]
            draw.line(coords_i, fill=color, width=width, joint="curve")


def render_preview(
    result: CutlineResult,
    out_path: str,
    original_image_path: str | None = None,
    line_width: int = 3,
    margin_px: int = 40,
) -> str:
    canvas_w = result.width_px + margin_px * 2
    canvas_h = result.height_px + margin_px * 2

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))

    if original_image_path:
        art = Image.open(original_image_path).convert("RGBA")
        canvas.alpha_composite(art, (margin_px, margin_px))

    draw = ImageDraw.Draw(canvas)

    for name in ("bleed", "cut", "safety"):
        geom = result.offsets.get(name)
        if geom is None:
            continue
        _draw_multipolygon_outline(
            draw, geom, (margin_px, margin_px), COLORS_RGB[name], line_width
        )

    canvas.convert("RGB").save(out_path)
    return out_path
