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
    async_save: bool = False,
) -> str:
    canvas_w = result.width_px + margin_px * 2
    canvas_h = result.height_px + margin_px * 2

    # 2026-09-30 속도: 원본은 캐시에서(다시 읽지 않음), 합성은 RGB 붙여넣기로(투명 합성보다 빠름).
    # 원본의 투명한 부분은 예전처럼 흰 바탕 위에 보이도록 알파를 마스크로 쓴다.
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

    if original_image_path:
        from . import image_cache

        art = image_cache.pil_open(original_image_path, "RGBA")
        canvas.paste(art, (margin_px, margin_px), art)

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
