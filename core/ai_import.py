"""
Load an .ai file (saved "PDF compatible", like a real Illustrator export) as
the GUI's primary "1. 도안 파일" input -- distinct from the other two .ai-
related modules in this project: core.guide (parses a guide file's own
structural rects, not artwork) and core.ai_cutline_reader/core.ai_layer_export
(read/write an EXISTING production file's own cutline layer, which requires
the file to already have one). This module is the newest and simplest of the
three -- given ANY .ai file the artist wants to start a FRESH cutline for
(2026-08-26: "주 입력 파일은 어도비 일러스트야"), it produces one plain raster
image file that the rest of this project's pipeline (GrabCut, drag-select,
sticker/domusong generation) can work with exactly like an already-opened
PNG/JPG -- no changes needed anywhere else in the pipeline.

Prefers extracting the file's own embedded print-resolution raster (same
technique already used and verified in core.ai_cutline_reader.load_real_cutlines
against this project's real production files, which always embed one
flattened print image) so the working image is the artist's own pixels at
their own resolution -- never a re-rendered copy that could lose quality.
Falls back to flattening the whole page at the requested DPI when the file
has no embedded raster at all (a purely vector illustration with no placed
bitmap), OR (2026-09-08(10차) 피드백: "자료집에 있는 일러스트 파일 전부
칼선 분석하고 문제에 반영해") when the page embeds MORE THAN ONE distinct
raster image.

That second case was found by actually running this on every .ai file in
멍푸님's "자료집"(교재) reference folder, not just her usual single-sheet
production files: several files there (masking-tape roll templates, a
multi-motif reference sheet) place SEVERAL DIFFERENT small motif images side by side
on one page (e.g. one real file: 4 distinct dessert-character motifs, each
placed 5 times along a tape strip -- 20 placements, 4 distinct images).
`page.get_images(full=True)` lists EVERY placement, so `images[0]` there
was just whichever ONE of the 4 motifs happened to be embedded first in the
PDF's internal object order -- extracting only that xref silently discarded
the other 3 motifs entirely and returned that one motif's own raw embedded
resolution (its full original source-art size, e.g. 3500x3500px) instead of
anything resembling the actual tiny tiled strip that's really printed. The
correct working image for a page like that is the fully composited page
itself (every motif, at its real placement/size) -- exactly what
`page.get_pixmap()` already produces -- not any single embedded asset.

A page where every placement shares the SAME single embedded xref (her
normal production files, and reference files that just repeat one design
many times) is unaffected by this change: "more than one DISTINCT image"
is judged by the number of unique xrefs among all placements, not the
number of placements, so a design repeated 12 times under one xref still
takes the fast, full-resolution extraction path exactly as before.
"""

from __future__ import annotations


def load_ai_as_raster(ai_path: str, out_path: str, dpi: float = 300.0, page_index: int = 0) -> str:
    """
    Reads `ai_path` (read-only) and writes a plain raster image to
    `out_path` (PNG), returning `out_path`. Never modifies `ai_path` itself.
    """
    import fitz  # PyMuPDF; .ai saved "PDF compatible" opens fine as PDF

    doc = fitz.open(ai_path)
    try:
        if page_index >= doc.page_count:
            raise ValueError(
                f"{ai_path}: 페이지 {page_index}가 없습니다 (전체 {doc.page_count}페이지)."
            )
        page = doc[page_index]

        images = page.get_images(full=True)
        distinct_xrefs = {entry[0] for entry in images}
        if images and len(distinct_xrefs) == 1:
            # 실제 인쇄용 래스터가 이미 심겨 있고(같은 그림이 여러 번
            # 배치됐더라도 전부 같은 xref) 딱 하나뿐이면(이 프로젝트가
            # 검증한 실제 완성 파일들은 전부 이런 구조) 그 원본 픽셀을
            # 그대로 추출 -- 다시 렌더링해서 해상도/화질을 잃지 않도록.
            xref = images[0][0]
            info = doc.extract_image(xref)
            with open(out_path, "wb") as f:
                f.write(info["image"])
            return out_path

        # 아래 두 경우 모두 페이지 자체를 통째로 래스터화(알파 유지)한다:
        #   1. 심겨 있는 래스터가 아예 없는 순수 벡터 일러스트
        #   2. 서로 다른 이미지가 2개 이상 심겨 있는 경우(예: 마스킹테이프
        #      시트에 서로 다른 모티프 여러 개가 각각 여러 번 배치된 구성)
        #      -- 이땐 그중 하나만 골라 추출하면 나머지 모티프를 전부
        #      잃어버리므로, 모든 모티프가 실제 배치대로 합성된 페이지
        #      전체를 쓰는 것만이 올바르다.
        zoom = max(dpi, 1.0) / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        pix.save(out_path)
        return out_path
    finally:
        doc.close()


def ai_raster_placement(ai_path: str, dpi: float = 300.0, page_index: int = 0) -> dict:
    """load_ai_as_raster가 만든 이미지의 픽셀이 AI 대지(페이지) 어디에, 어떤 크기로
    놓이는지(2026-09-29, 멍푸: "svg 파일 원본 이미지 크기에 맞게 적용되게 해줘").

    심긴 인쇄 이미지를 그대로 꺼낸 경우 그 이미지는 대지 전체가 아니라 대지 안의 한
    자리에 놓여 있다(예: 대지 1315x893pt 중 왼쪽 위 (74.6, 66.2)pt부터). 그래서 SVG를
    원본 대지와 겹치게 저장하려면 이 위치와 배율이 필요하다. 페이지를 통째로 렌더링한
    경우는 대지 전체 = 이미지이고 1px = 72/dpi pt.

    Returns dict: page_w_pt, page_h_pt, x0_pt, y0_pt(이미지 왼쪽 위, 대지 왼쪽 위 기준),
    pt_per_px, placements(같은 이미지가 대지에 놓인 횟수)."""
    import fitz

    doc = fitz.open(ai_path)
    try:
        page = doc[page_index]
        pr = page.rect
        out = {"page_w_pt": pr.width, "page_h_pt": pr.height, "x0_pt": 0.0, "y0_pt": 0.0,
               "pt_per_px": 72.0 / max(float(dpi), 1.0), "placements": 1}
        images = page.get_images(full=True)
        distinct_xrefs = {entry[0] for entry in images}
        if images and len(distinct_xrefs) == 1:
            xref = images[0][0]
            info = doc.extract_image(xref)
            rects = page.get_image_rects(xref)
            if rects and info.get("width"):
                r = rects[0]
                out.update({
                    "x0_pt": r.x0 - pr.x0, "y0_pt": r.y0 - pr.y0,
                    "pt_per_px": r.width / float(info["width"]), "placements": len(rects),
                })
        return out
    finally:
        doc.close()
