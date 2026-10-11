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

# 2026-10-05 화이트 레이어: 화면에서 화이트 자리를 파란색으로 보여 준다(실제 저장 색은 흰색).
# 2026-10-11(멍푸: "저장본은 흰색이지만 컷라인 스튜디오에는 푸른색으로 보이게 해서 차이를 확인하게 해"):
# 그림 아래에 진한 파란 바탕으로 깔고(그림이 없는 곳까지 화이트가 나온 자리가 파랗게 드러남), 그 위에
# 화이트 외곽선을 파란 선으로 한 번 더 그린다(꽉 찬 그림에서도 화이트 범위가 보이게).
WHITE_PREVIEW_RGB = (40, 110, 255)
WHITE_PREVIEW_EDGE_RGB = (0, 70, 230)
_WHITE_OVERLAY = {"geom": None}


def set_white_overlay(geom):
    """미리보기에 겹쳐 그릴 화이트 레이어(이미지 픽셀 좌표). None이면 안 그림."""
    _WHITE_OVERLAY["geom"] = geom if (geom is not None and not geom.is_empty) else None


def _white_polys(geom):
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return [p for p in polys if not p.is_empty and p.geom_type == "Polygon" and len(p.exterior.coords) >= 3]


def _draw_white_fill(canvas, geom, offset_xy):
    """화이트 자리를 파랗게 채운다(그림을 붙이기 전에 -- 그림 아래 바탕)."""
    ox, oy = offset_xy
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    for poly in _white_polys(geom):
        md.polygon([(x + ox, y + oy) for x, y in poly.exterior.coords], fill=255)
        for r in poly.interiors:
            if len(r.coords) >= 3:
                md.polygon([(x + ox, y + oy) for x, y in r.coords], fill=0)
    canvas.paste(WHITE_PREVIEW_RGB, (0, 0), mask)


def _draw_white_edge(canvas, geom, offset_xy, width=2):
    """화이트 외곽선(그림 위에)."""
    ox, oy = offset_xy
    d = ImageDraw.Draw(canvas)
    for poly in _white_polys(geom):
        for ring in [poly.exterior] + list(poly.interiors):
            pts = [(x + ox, y + oy) for x, y in ring.coords]
            if len(pts) >= 2:
                d.line(pts, fill=WHITE_PREVIEW_EDGE_RGB, width=width, joint="curve")


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
    async_save: bool = False,
) -> str:
    canvas_w = result.width_px + margin_px * 2
    canvas_h = result.height_px + margin_px * 2

    # 2026-09-30 속도: 원본은 캐시에서(다시 읽지 않음), 합성은 RGB 붙여넣기로(투명 합성보다 빠름).
    # 원본의 투명한 부분은 예전처럼 흰 바탕 위에 보이도록 알파를 마스크로 쓴다.
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

    white = _WHITE_OVERLAY["geom"]
    if white is not None:
        _draw_white_fill(canvas, white, (margin_px, margin_px))

    if original_image_path:
        from . import image_cache

        art = image_cache.pil_open(original_image_path, "RGBA")
        canvas.paste(art, (margin_px, margin_px), art)

    if white is not None:
        _draw_white_edge(canvas, white, (margin_px, margin_px), width=max(1, line_width - 1))

    draw = ImageDraw.Draw(canvas)

    for name in ("bleed", "cut", "safety"):
        geom = result.offsets.get(name)
        if geom is None:
            continue
        _draw_multipolygon_outline(
            draw, geom, (margin_px, margin_px), COLORS_RGB[name], line_width
        )

    _LAST_RENDERED.clear()
    _LAST_RENDERED[out_path] = canvas
    # 2026-09-30 속도: 화면 표시용 임시 파일이라 압축을 약하게(저장 1.1초 -> 약 0.2초)
    # 2026-10-02 속도(멍푸: "칼선 속도 5초이내로"): 큰 시트는 그래도 약 0.8초 -- 화면은 메모리의
    # 그림(open_rendered)을 쓰므로 파일은 뒤에서 쓴다(같은 파일을 다시 그리면 마지막 것만 남음).
    if async_save:
        _save_async(canvas, out_path)
    else:
        canvas.save(out_path, compress_level=1)
    return out_path


import threading as _threading

_SAVE_LOCK = _threading.Lock()


def _save_async(img, out_path):
    def _run():
        with _SAVE_LOCK:
            if _LAST_RENDERED.get(out_path) is not img:
                return  # 그새 더 새 그림이 그려짐
            try:
                img.save(out_path, compress_level=1)
            except Exception:  # noqa: BLE001
                pass

    _threading.Thread(target=_run, daemon=True).start()


def wait_saved():
    """뒤에서 쓰던 미리보기 파일 저장이 끝날 때까지(시험·종료용)."""
    with _SAVE_LOCK:
        pass


# 방금 그린 미리보기(파일로도 저장했지만, 화면에 띄울 때 다시 읽지 않도록 메모리에 하나 보관)
_LAST_RENDERED: dict = {}


def open_rendered(out_path: str):
    """render_preview가 방금 그린 이미지면 메모리에서, 아니면 파일에서 연다."""
    img = _LAST_RENDERED.get(out_path)
    if img is not None:
        return img
    return Image.open(out_path)
