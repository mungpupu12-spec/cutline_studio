"""칼선을 투명 배경 PNG로 저장(2026-09-30, 멍푸: "칼선 파일 svg 말고 png로 ... 그대로 원본
이미지에 적용할 수 있게").

- 그림 크기(가로x세로 픽셀)가 원본 이미지와 똑같다 -- AI 파일이면 그 안에 든 인쇄 이미지와
  같은 크기. 해상도 정보(dpi)도 원본과 같게 넣어서, 일러스트레이터/포토샵에 가져오면 원본
  이미지와 같은 실제 크기로 놓인다(원본 이미지 위에 모서리를 맞춰 겹치면 끝).
- 칼선만 선으로(마젠타, 안쪽 채우기 없음), 나머지는 전부 투명. PNG 한 장이라 모든 칼선이
  하나의 개체로 함께 움직인다(따로 흩어지지 않음).
"""
from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw

# 인쇄 CMYK 마젠타 100%를 화면 색으로 나타낸 표준값(SVG와 같은 색)
CUT_RGB = (236, 0, 140)


def _rings(geom):
    if geom is None or geom.is_empty:
        return []
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        out.append(list(p.exterior.coords))
        out.extend(list(r.coords) for r in p.interiors)
    return out


def export_cut_png(result, out_path: str, dpi: Optional[float] = None,
                   line_mm: float = 0.25, color=CUT_RGB) -> str:
    """result.offsets["cut"]를 원본 이미지와 같은 크기의 투명 PNG에 선으로 그린다."""
    use_dpi = float(dpi) if dpi and dpi > 0 else float(result.dpi or 300.0)
    w, h = int(result.width_px), int(result.height_px)
    width_px = max(2, int(round(line_mm * use_dpi / 25.4)))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgba = tuple(color) + (255,)
    for ring in _rings(result.offsets.get("cut")):
        if len(ring) < 2:
            continue
        pts = [(float(x), float(y)) for x, y in ring]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        draw.line(pts, fill=rgba, width=width_px, joint="curve")
    img.save(out_path, format="PNG", dpi=(use_dpi, use_dpi))
    return out_path


# 2026-10-05 화이트 레이어: 좋은 예(실제 납품 파일)처럼 흰색(C0 M0 Y0 K0) 면. 흰 바탕에서는 안 보이므로
# 일러스트레이터에서 "화이트레이어" 레이어에 넣어 쓰면 된다.
WHITE_RGB = (255, 255, 255)


def export_white_png(white_geom, width_px: int, height_px: int, out_path: str, dpi: float = 300.0,
                     color=WHITE_RGB) -> str:
    """화이트 레이어(이미지 픽셀 좌표의 면)를 원본 이미지와 같은 크기의 투명 PNG에 채운다(구멍은 비움)."""
    img = Image.new("RGBA", (int(width_px), int(height_px)), (0, 0, 0, 0))
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    if white_geom is not None and not white_geom.is_empty:
        polys = list(white_geom.geoms) if hasattr(white_geom, "geoms") else [white_geom]
        for p in polys:
            if p.is_empty or p.geom_type != "Polygon" or len(p.exterior.coords) < 3:
                continue
            md.polygon([(float(x), float(y)) for x, y in p.exterior.coords], fill=255)
            for r in p.interiors:
                if len(r.coords) >= 3:
                    md.polygon([(float(x), float(y)) for x, y in r.coords], fill=0)
    img.paste(tuple(color) + (255,), (0, 0), mask)
    use_dpi = float(dpi) if dpi and dpi > 0 else 300.0
    img.save(out_path, format="PNG", dpi=(use_dpi, use_dpi))
    return out_path
