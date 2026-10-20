"""불러온 도안의 사본에 칼선을 레이어로 넣어 저장(2026-10-20, 멍푸: "불러온 png,jpg,ai파일 사본 생성 해서
png,jpg는 이미지/칼선 레이어를 분리한 pdf로 생성 하기. 기존의 칼선 이미지를 직접 파일에 적용하는 번거로움 보완.
똑같이 ai 파일도 사본 생성해 이미지 파일 위에 칼선 레이어 생성해서 적용하기").

예전에는 칼선만 그린 투명 PNG를 내보내서, 작가가 일러스트레이터에서 원본 위에 직접 겹쳐 맞춰야 했다.
이제는 원본은 그대로 두고 사본을 만든다.
  - PNG/JPG -> PDF 한 장: 레이어(PDF 선택 콘텐츠) "화이트레이어"(화이트를 넣을 때만, 맨 아래) / "도안"(원본 이미지를
    실제 크기로) / "칼선"(벡터 선, 맨 위). 원본 이미지는 다시 압축하지 않고 그대로 넣는다.
  - AI -> AI 사본: 원래 레이어와 그림은 건드리지 않고 "칼선" 레이어 하나(화이트를 넣으면 "화이트레이어"도)를 더한다.
칼선 색은 키스컷 좋은 예(실제 납품 파일)와 같이 마젠타 100%(C0 M100 Y0 K0) 0.5pt, 화이트는 C0 M0 Y0 K0 면.

좌표: 칼선은 작업 이미지 픽셀 좌표(왼쪽 위 원점, 아래로 +y). PDF/AI 페이지 좌표(pt, 왼쪽 위 원점)로 바꿀 때
이미지가 놓인 자리(x0, y0)와 1px당 pt를 쓴다 -- AI는 core.ai_import.ai_raster_placement가 준 값(같은 그림이
여러 번 놓였으면 놓인 자리마다 똑같이), PNG/JPG는 (0, 0)과 72/dpi.
"""
from __future__ import annotations

import os
import shutil
from typing import Optional

CUT_LAYER = "칼선"
WHITE_LAYER = "화이트레이어"
ART_LAYER = "도안"
CUT_CMYK = (0.0, 1.0, 0.0, 0.0)     # 마젠타 100%
WHITE_CMYK = (0.0, 0.0, 0.0, 0.0)
CUT_WIDTH_PT = 0.5


def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    gs = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return [g for g in gs if g.geom_type == "Polygon" and not g.is_empty]


def _rings(poly):
    yield list(poly.exterior.coords)
    for r in poly.interiors:
        yield list(r.coords)


def _draw(page, geom, ocg, to_pt, *, stroke=None, fill=None, width=CUT_WIDTH_PT, under=False) -> int:
    """geom(픽셀 좌표)을 to_pt로 옮겨 ocg 레이어에 그린다. under=True면 페이지의 기존 내용보다 먼저(아래에)
    그린다(화이트는 그림 밑). 그린 고리 수."""
    shape = page.new_shape()
    n = 0
    for p in _polys(geom):
        for ring in _rings(p):
            pts = [to_pt(x, y) for x, y in ring]
            if len(pts) >= 3:
                shape.draw_polyline(pts)
                n += 1
    if n:
        shape.finish(color=stroke, fill=fill, width=width, closePath=True, even_odd=True, oc=ocg)
        shape.commit(overlay=not under)
    return n


def cut_geom_of(result):
    """내보낼 칼선(실제로 자르는 선). 도무송/조각의 세이프티·블리딩 안내선은 넣지 않는다."""
    offs = getattr(result, "offsets", None) or {}
    g = offs.get("cut")
    if g is None and offs:
        g = next(iter(offs.values()))
    return g


def export_layered_pdf(image_path: str, result, out_path: str, dpi: float, white_geom=None) -> dict:
    """PNG/JPG 원본 + 칼선(+화이트)을 레이어로 나눈 PDF. 페이지 = 이미지 실제 크기(dpi 기준)."""
    import fitz
    from PIL import Image

    with Image.open(image_path) as im:
        w_px, h_px = im.size
    k = 72.0 / float(dpi)
    doc = fitz.open()
    try:
        page = doc.new_page(width=w_px * k, height=h_px * k)
        stats = {"page_mm": (round(w_px * k / 72 * 25.4, 2), round(h_px * k / 72 * 25.4, 2))}

        def to_pt(x, y):
            return (x * k, y * k)

        if white_geom is not None and not white_geom.is_empty:
            ocg_w = doc.add_ocg(WHITE_LAYER, on=True)
            stats["white_rings"] = _draw(page, white_geom, ocg_w, to_pt, fill=WHITE_CMYK, stroke=None, width=0)
        ocg_a = doc.add_ocg(ART_LAYER, on=True)
        with open(image_path, "rb") as f:
            page.insert_image(page.rect, stream=f.read(), oc=ocg_a, keep_proportion=False)
        ocg_c = doc.add_ocg(CUT_LAYER, on=True)
        stats["cut_rings"] = _draw(page, cut_geom_of(result), ocg_c, to_pt, stroke=CUT_CMYK)
        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()
    return stats


def strip_ai_private_data(doc) -> int:
    """일러스트레이터 고유 데이터(/PieceInfo의 AIPrivateData) 연결을 끊는다. 일러스트레이터는 이 데이터가 있으면
    PDF 쪽 내용을 무시하고 그것만 읽어서, 사본에 더한 칼선 레이어가 일러스트레이터에서는 안 보인다."""
    n = 0
    for i in range(doc.page_count):
        xref = doc[i].xref
        if doc.xref_get_key(xref, "PieceInfo")[0] != "null":
            doc.xref_set_key(xref, "PieceInfo", "null")
            n += 1
    cat = doc.pdf_catalog()
    if doc.xref_get_key(cat, "PieceInfo")[0] != "null":
        doc.xref_set_key(cat, "PieceInfo", "null")
        n += 1
    return n


def export_ai_copy_with_cut(ai_path: str, out_path: str, result, placement: dict, white_geom=None,
                            page_index: int = 0, strip_private: bool = True) -> dict:
    """AI 원본의 사본에 칼선(+화이트) 레이어를 더한다. 원본 파일은 읽기만 한다."""
    import fitz

    if os.path.abspath(ai_path) == os.path.abspath(out_path):
        raise ValueError("원본 파일에 덮어쓸 수 없습니다. 다른 이름으로 저장하세요.")
    shutil.copyfile(ai_path, out_path)
    doc = fitz.open(out_path)
    try:
        page = doc[page_index]
        pr = page.rect
        ppp = float(placement.get("pt_per_px") or 1.0)
        tiles = placement.get("tiles") or [(placement.get("x0_pt", 0.0), placement.get("y0_pt", 0.0))]
        stats = {"tiles": len(tiles), "cut_rings": 0, "white_rings": 0}
        ocg_w = doc.add_ocg(WHITE_LAYER, on=True) if white_geom is not None and not white_geom.is_empty else None
        ocg_c = doc.add_ocg(CUT_LAYER, on=True)
        cut = cut_geom_of(result)
        for tx, ty in tiles:
            def to_pt(x, y, tx=tx, ty=ty):
                return (pr.x0 + tx + x * ppp, pr.y0 + ty + y * ppp)

            if ocg_w is not None:
                stats["white_rings"] += _draw(page, white_geom, ocg_w, to_pt, fill=WHITE_CMYK, stroke=None, width=0,
                                              under=True)
            stats["cut_rings"] += _draw(page, cut, ocg_c, to_pt, stroke=CUT_CMYK)
        if strip_private:
            stats["stripped"] = strip_ai_private_data(doc)
        doc.save(out_path + ".tmp", garbage=0, deflate=True)
    finally:
        doc.close()
    os.replace(out_path + ".tmp", out_path)
    return stats


def default_copy_name(source_path: str) -> tuple:
    """(기본 파일 이름, 확장자). PNG/JPG -> '_칼선.pdf', AI -> '_칼선.ai'."""
    base, ext = os.path.splitext(os.path.basename(source_path))
    if ext.lower() == ".ai":
        return base + "_칼선.ai", ".ai"
    return base + "_칼선.pdf", ".pdf"


# ---- 일러스트레이터로 직접 레이어 넣기(core.illustrator_bridge) -------------------------------------------
# 2026-10-20 멍푸 PC 일러스트레이터 2026에서 확인: 위 export_ai_copy_with_cut처럼 PDF 쪽에만 레이어를 더하면
# 일러스트레이터에서는 안 보이고(고유 데이터만 읽음), 고유 데이터를 끊으면 원래 레이어가 "레이어 1" 하나로 합쳐졌다.
# 그래서 AI 사본은 일러스트레이터가 직접 레이어를 더하게 한다. 일러스트레이터가 없으면 PDF로 대신 저장.

def ai_polys_pt(geom, placement: dict, page_rect_wh: Optional[tuple] = None) -> list:
    """작업 이미지 픽셀 좌표 -> AI 페이지 pt(왼쪽 위 원점). 같은 그림이 여러 번 놓였으면 놓인 자리마다."""
    from core.illustrator_bridge import polys_to_pt

    ppp = float(placement.get("pt_per_px") or 1.0)
    tiles = placement.get("tiles") or [(placement.get("x0_pt", 0.0), placement.get("y0_pt", 0.0))]
    out = []
    for tx, ty in tiles:
        out += polys_to_pt(geom, lambda x, y, tx=tx, ty=ty: (tx + x * ppp, ty + y * ppp))
    return out


def image_polys_pt(geom, dpi: float) -> list:
    from core.illustrator_bridge import polys_to_pt

    k = 72.0 / float(dpi)
    return polys_to_pt(geom, lambda x, y: (x * k, y * k))


def save_ai_copy_via_illustrator(ai_path: str, out_path: str, result, placement: dict, white_geom=None) -> str:
    """원본 AI를 그대로 복사한 뒤 일러스트레이터가 '칼선'(+'화이트레이어') 레이어를 더해 저장. 결과 문자열."""
    from core.illustrator_bridge import add_layers_to_ai

    if os.path.abspath(ai_path) == os.path.abspath(out_path):
        raise ValueError("원본 파일에 덮어쓸 수 없습니다. 다른 이름으로 저장하세요.")
    shutil.copyfile(ai_path, out_path)
    cut = ai_polys_pt(cut_geom_of(result), placement)
    white = ai_polys_pt(white_geom, placement) if white_geom is not None and not white_geom.is_empty else []
    return add_layers_to_ai(out_path, cut, white)


def save_layered_pdf_via_illustrator(image_path: str, out_path: str, result, dpi: float, white_geom=None) -> str:
    from PIL import Image

    from core.illustrator_bridge import make_layered_pdf

    with Image.open(image_path) as im:
        w_px, h_px = im.size
    k = 72.0 / float(dpi)
    cut = image_polys_pt(cut_geom_of(result), dpi)
    white = image_polys_pt(white_geom, dpi) if white_geom is not None and not white_geom.is_empty else []
    return make_layered_pdf(image_path, out_path, w_px * k, h_px * k, cut, white)
