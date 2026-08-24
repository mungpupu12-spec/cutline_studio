"""
Export a CutlineResult to an Illustrator-friendly SVG file.

Layers are named/grouped to mirror the convention seen in print-vendor
templates (bleeding / cutting line / safety range), each in its
conventional color, plus the original artwork faintly for reference.
Illustrator opens SVG groups <g id="..."> as named layers when you use
File > Open (not Place), so this maps directly onto the artist's existing
workflow.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import MultiPolygon, Polygon

from .cutline_core import CutlineResult

COLORS = {
    "bleed": "#0000FF",   # blue  - bleeding
    "cut": "#FF0000",     # red   - cutting line
    "safety": "#00CC44",  # green - safety range
}

LABELS_KO = {
    "bleed": "bleeding(재단여백)",
    "cut": "cutting line(칼선)",
    "safety": "safety range(세이프티)",
}


def _ring_to_path_d(coords) -> str:
    if len(coords) < 3:
        return ""
    pts = list(coords)
    d = f"M {pts[0][0]:.2f},{pts[0][1]:.2f} "
    d += " ".join(f"L {x:.2f},{y:.2f}" for x, y in pts[1:])
    d += " Z"
    return d


def _polygon_to_path_d(poly: Polygon) -> str:
    d = _ring_to_path_d(poly.exterior.coords)
    for interior in poly.interiors:
        d += " " + _ring_to_path_d(interior.coords)
    return d


def _multipolygon_to_path_d(mp) -> str:
    if isinstance(mp, Polygon):
        polys = [mp]
    else:
        polys = list(mp.geoms)
    return " ".join(_polygon_to_path_d(p) for p in polys)


def export_svg(
    result: CutlineResult,
    out_path: str,
    stroke_width_pt: float = 0.75,
    include_artwork_fill: bool = True,
    margin_px: Optional[float] = None,
) -> str:
    margin = 0.0
    if margin_px:
        margin = margin_px
    else:
        # auto margin = bleed offset so nothing gets clipped by the artboard
        margin = 0.0
        for name in ("bleed",):
            geom = result.offsets.get(name)
            if geom is not None:
                b = geom.bounds  # (minx, miny, maxx, maxy)
                margin = max(margin, -min(0, b[0]), -min(0, b[1]),
                             max(0, b[2] - result.width_px), max(0, b[3] - result.height_px))

    vb_w = result.width_px + margin * 2
    vb_h = result.height_px + margin * 2

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{vb_w:.2f}" height="{vb_h:.2f}" '
        f'viewBox="0 0 {vb_w:.2f} {vb_h:.2f}">'
    )
    parts.append(f'<g id="offset" transform="translate({margin:.2f},{margin:.2f})">')

    if include_artwork_fill:
        d_art = _multipolygon_to_path_d(result.design)
        parts.append(
            f'<g id="artwork_reference"><path d="{d_art}" fill="#000000" '
            f'fill-opacity="0.15" stroke="none" fill-rule="evenodd"/></g>'
        )

    # draw largest (bleed) first so smaller ones render on top and stay visible
    draw_order = ["bleed", "cut", "safety"]
    for name in draw_order:
        geom = result.offsets.get(name)
        if geom is None:
            continue
        color = COLORS[name]
        label = LABELS_KO[name]
        d = _multipolygon_to_path_d(geom)
        parts.append(
            f'<g id="{name}" data-label="{label}">'
            f'<path d="{d}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke_width_pt:.2f}" fill-rule="evenodd"/></g>'
        )

    parts.append("</g>")
    parts.append("</svg>")

    svg_content = "\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    return out_path
