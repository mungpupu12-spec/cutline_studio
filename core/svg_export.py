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
    # 2026-09-29(멍푸: "칼선은 마젠타 100으로 선으로 있어야해"): SVG는 RGB만 담을 수 있어,
    # 인쇄 CMYK 마젠타 100%(C0 M100 Y0 K0)를 화면용으로 나타낸 표준값 #EC008C를 쓴다
    # (일러스트레이터 CMYK 문서에 붙이면 마젠타 100 근처로 변환됨 -- 색상 설정에 따라 약간 다를 수 있음).
    "cut": "#EC008C",     # magenta 100 - cutting line
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


def svg_size_mm(width_px: float, height_px: float, dpi: float):
    """이미지 픽셀 크기 -> 실제 크기(mm)."""
    dpi = float(dpi) if dpi and dpi > 0 else 300.0
    return width_px * 25.4 / dpi, height_px * 25.4 / dpi


PT_TO_MM = 25.4 / 72.0


def export_svg(
    result: CutlineResult,
    out_path: str,
    stroke_width_pt: float = 0.75,
    include_artwork_fill: bool = False,
    margin_px: Optional[float] = None,
    dpi: Optional[float] = None,
    placement: Optional[dict] = None,
) -> str:
    """2026-09-29(멍푸: "svg 파일 원본 이미지 크기에 맞게 적용되게 해줘" -- 일러스트레이터에서
    칼선 SVG가 원본보다 훨씬 크고 위치도 어긋나 보였음): 예전 SVG는 width/height에 단위가
    없어서(=px) 일러스트레이터가 1px을 1pt(1/72인치)로 읽었다 -- 300dpi 이미지면 원본보다
    300/72 = 약 4.17배 크게 열렸다. 또 AI 파일은 인쇄 이미지가 대지 전체가 아니라 대지 안
    한 자리(예: 왼쪽 위에서 약 26mm·23mm 들어간 곳)에 놓여 있는데, SVG는 그 위치를 몰랐다.

    이제는 SVG 좌표를 전부 mm로 쓴다(width/height도 mm, viewBox도 mm).
      - placement(core.ai_import.ai_raster_placement)가 있으면: SVG 대지 = AI 대지 크기,
        칼선은 이미지가 놓인 자리/배율 그대로 -- 일러스트레이터에서 원본 AI 대지와 정확히
        겹친다(열기 또는 복사 후 "제자리에 붙이기").
      - 없으면(PNG/JPG): SVG 대지 = 이미지 크기(픽셀 / dpi).
      - 선 두께는 pt 그대로(0.75pt).
      - 칼선 안쪽 채우기 없음(선만). 예전엔 참고용으로 그림 모양을 15% 회색으로 채워 넣었는데,
        일러스트레이터에서 칼선 안에 회색 면이 생겨 "있으면 안 돼"(멍푸) -> 기본값 끔.
    margin_px를 주면 예전처럼 대지 사방에 여백을 더 둔다(그만큼 원점도 옮김)."""
    use_dpi = float(dpi) if dpi and dpi > 0 else float(result.dpi or 300.0)
    if placement:
        mm_per_px = float(placement["pt_per_px"]) * PT_TO_MM
        page_w_mm = float(placement["page_w_pt"]) * PT_TO_MM
        page_h_mm = float(placement["page_h_pt"]) * PT_TO_MM
        x0_mm = float(placement.get("x0_pt", 0.0)) * PT_TO_MM
        y0_mm = float(placement.get("y0_pt", 0.0)) * PT_TO_MM
    else:
        mm_per_px = 25.4 / use_dpi
        page_w_mm, page_h_mm = svg_size_mm(result.width_px, result.height_px, use_dpi)
        x0_mm = y0_mm = 0.0
    margin_mm = float(margin_px) * mm_per_px if margin_px else 0.0
    W = page_w_mm + 2 * margin_mm
    H = page_h_mm + 2 * margin_mm
    stroke_px = stroke_width_pt * PT_TO_MM / mm_per_px  # 그룹 안(픽셀 좌표)에서의 두께

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.4f}mm" height="{H:.4f}mm" '
        f'viewBox="0 0 {W:.4f} {H:.4f}" overflow="visible">'
    )
    parts.append(
        f'<g id="offset" transform="translate({x0_mm + margin_mm:.4f},{y0_mm + margin_mm:.4f}) '
        f'scale({mm_per_px:.8f})">'
    )

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
            f'stroke-width="{stroke_px:.3f}" fill-rule="evenodd"/></g>'
        )

    parts.append("</g>")
    parts.append("</svg>")

    svg_content = "\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    return out_path
